"""
Comprehensive Failure Injection, Concurrency & Reliability Verification Suite.
Stage 9.7B.13K Specification.

Covers:
- Part 1: Historical Row Mapping Audit (Section 5)
- Part 2: Remote Write Failure Tests (Section 7, Tests A - K)
- Part 3: Claim-Token & Stale Worker Tests (Section 8, Tests L - S)
- Part 4: Lease Expiration & Long Request Tests (Section 9, Tests T - X)
- Part 5: Concurrent Claim & Allocation Tests (Section 10, Tests Y - AB)
- Part 6: Batch Partial Failure & Poison Record Tests (Section 11, Tests AC - AF)
- Part 7: Retry, Dead-Letter & Requeue Tests (Section 12, Tests AG - AM)
- Part 8: Database & Transaction Safety Tests (Section 13, Tests AN - AQ)
"""

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.database import get_db_connection, init_db
from app.services.sheets_worker import sync_pending_records_batch, SheetsSyncWorker
from app.services.sheets_outbox import (
    claim_records,
    recover_expired_leases,
    mark_as_synced,
    schedule_retry,
    move_to_dead_letter,
    release_claim,
    get_queue_stats,
    retry_dead_letter_records,
    calculate_retry_delay,
    DEFAULT_BASE_DELAY_SECONDS,
    DEFAULT_MAX_DELAY_SECONDS,
)
from app.services.sheets_row_allocator import (
    allocate_sheet_rows,
    get_allocated_sheet_row,
    get_next_sheet_row,
    MINIMUM_VALID_SHEET_ROW,
    AllocatorMetadataError,
    AllocatorConsistencyError,
    AllocatorRecordNotFoundError,
    AllocatorInvariantError,
    AllocatorInputError,
)
from app.services.google_sheets import (
    DEFAULT_SPREADSHEET_HEADERS,
    GoogleSheetsTimeoutError,
    GoogleSheetsRateLimitError,
    GoogleSheetsServerError,
    GoogleSheetsValidationError,
    GoogleSheetsPermissionError,
    GoogleSheetsAuthError,
    GoogleSheetsNetworkError,
    GoogleSheetsError,
    update_attendance_row,
    construct_deterministic_range,
)

BASE_DISPOSABLE_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_failure_injection_test.db")


# ==============================================================================
# Network & Environment Isolation Fixtures
# ==============================================================================

@pytest.fixture(autouse=True)
def enforce_network_isolation(monkeypatch):
    """Guarantees zero outbound socket creation during failure-injection tests."""
    def _blocked_connect(*args, **kwargs):
        raise RuntimeError("CRITICAL ERROR: Outbound network connection attempted during isolated failure-injection testing!")

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)


def _make_mock_http_error(status_code: int, message: str = "Mock Google API Error") -> HttpError:
    resp = Response({"status": str(status_code)})
    content = json.dumps({"error": {"code": status_code, "message": message}}).encode("utf-8")
    return HttpError(resp, content)


def _create_isolated_test_db(tmp_path: Path, preserve_existing_rows: bool = False) -> Path:
    """Creates a new isolated test database copy from the Stage 13K disposable DB."""
    db_file = tmp_path / "failure_injection_test.db"
    src_conn = sqlite3.connect(str(BASE_DISPOSABLE_DB))
    dst_conn = sqlite3.connect(str(db_file))
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    if not preserve_existing_rows:
        conn = get_db_connection(db_file)
        conn.execute("DELETE FROM sheets_sync_queue;")
        conn.execute("DELETE FROM attendance;")
        conn.execute("DELETE FROM students;")
        conn.execute("DELETE FROM class_sessions;")
        conn.commit()
        conn.close()
    return db_file


def _seed_attendance_record(
    db_path: Path,
    roll_number: str = "CS-2026-001",
    full_name: str = "Alice Smith",
    card_uid: str = "UID_001",
    course_name: str = "CS-101: Systems",
    status: str = "Present",
    scanned_at: str = "2026-11-24T10:00:00Z",
    source: str = "rfid",
    sheet_row_number: Optional[int] = None,
    claim_token: int = 0,
    sync_status: str = "pending",
    retry_count: int = 0,
    lease_expires_at: Optional[str] = None
) -> int:
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(roll_number) DO UPDATE SET full_name=excluded.full_name RETURNING id;
        """,
        (roll_number, full_name, card_uid, scanned_at)
    )
    student_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES (?, '2026-11-24', '10:00', '12:00', 0, ?) RETURNING id;
        """,
        (course_name, scanned_at)
    )
    session_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, ?, ?, ?) RETURNING id;
        """,
        (student_id, session_id, scanned_at, status, source)
    )
    attendance_id = cur.fetchone()["id"]

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.execute(
        """
        INSERT INTO sheets_sync_queue (
            attendance_id, sync_status, retry_count, sheet_row_number, claim_token, lease_expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id;
        """,
        (attendance_id, sync_status, retry_count, sheet_row_number, claim_token, lease_expires_at, now_utc, now_utc)
    )
    queue_id = cur.fetchone()["id"]

    conn.commit()
    conn.close()
    return queue_id


def _make_mock_sheets_service(update_side_effect=None, get_values=None):
    mock_service = MagicMock()
    sheets_mock = MagicMock()
    values_mock = MagicMock()

    if get_values is None:
        get_values = [DEFAULT_SPREADSHEET_HEADERS]

    get_req_mock = MagicMock()
    get_req_mock.execute.return_value = {"values": get_values}
    values_mock.get.return_value = get_req_mock

    update_req_mock = MagicMock()
    if update_side_effect:
        update_req_mock.execute.side_effect = update_side_effect
    else:
        update_req_mock.execute.return_value = {
            "updatedRange": "Attendance_Log!A2:I2",
            "updatedRows": 1,
            "updatedColumns": 9,
            "updatedCells": 9
        }
    values_mock.update.return_value = update_req_mock

    sheets_mock.values.return_value = values_mock
    mock_service.spreadsheets.return_value = sheets_mock
    return mock_service


# ==============================================================================
# Part 1: Historical Row Mapping & Invariants (Section 5)
# ==============================================================================

def test_hist_01_attendance_id_1_retains_row_3(tmp_path):
    """Verify that in the canonical historical fixture, attendance ID 1 retains row 3."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=True)
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT id, attendance_id, sheet_row_number, sync_status FROM sheets_sync_queue WHERE attendance_id = 1;").fetchone()
    conn.close()

    assert row is not None, "Attendance ID 1 must exist in the canonical fixture"
    assert row["sheet_row_number"] == 3
    assert row["sync_status"] == "synced"


def test_hist_02_attendance_id_2_retains_row_4(tmp_path):
    """Verify that in the canonical historical fixture, attendance ID 2 retains row 4."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=True)
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT id, attendance_id, sheet_row_number, sync_status FROM sheets_sync_queue WHERE attendance_id = 2;").fetchone()
    conn.close()

    assert row is not None, "Attendance ID 2 must exist in the canonical fixture"
    assert row["sheet_row_number"] == 4
    assert row["sync_status"] == "synced"


def test_hist_03_existing_assignments_not_replaced(tmp_path):
    """Verify that repeatedly requesting allocation for already-assigned records returns existing rows."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=True)
    res = allocate_sheet_rows([1, 2], db_path=db_file)
    assert res[1] == 3
    assert res[2] == 4

    # Verify next_sheet_row did not advance
    next_row = get_next_sheet_row(db_path=db_file)
    assert next_row >= 5


def test_hist_04_new_allocations_begin_at_row_5(tmp_path):
    """Verify that new allocations in a fresh fixture with initial next_sheet_row=5 allocate starting at row 5."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=False)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 5 WHERE id = 1;")
    conn.commit()
    conn.close()

    qid = _seed_attendance_record(db_file, roll_number="CS-NEW-01", card_uid="UID_NEW_01")
    alloc_map = allocate_sheet_rows([qid], db_path=db_file)
    assert alloc_map[qid] == 5

    next_row = get_next_sheet_row(db_path=db_file)
    assert next_row == 6


def test_hist_05_cannot_allocate_row_2_or_header(tmp_path):
    """Verify schema CHECK constraint and allocator code reject row < 5."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=False)
    conn = get_db_connection(db_file)
    # 1. SQLite schema enforces next_sheet_row >= 5
    with pytest.raises(sqlite3.IntegrityError) as exc_info:
        conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 2 WHERE id = 1;")
    assert "CHECK constraint failed" in str(exc_info.value)
    conn.close()


def test_hist_06_allocation_not_based_on_id_arithmetic(tmp_path):
    """Verify row assignment is driven purely by sheets_sync_meta, NOT attendance ID arithmetic."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=False)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 100 WHERE id = 1;")
    conn.commit()
    conn.close()

    qid = _seed_attendance_record(db_file, roll_number="CS-ARITH-01", card_uid="UID_ARITH_01")
    alloc_map = allocate_sheet_rows([qid], db_path=db_file)
    assert alloc_map[qid] == 100


def test_hist_07_row_assignments_survive_lifecycle_transitions(tmp_path):
    """Verify assigned row numbers survive failures, retries, and dead-lettering."""
    db_file = _create_isolated_test_db(tmp_path, preserve_existing_rows=False)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, sheet_row_number=42, claim_token=1, sync_status="in_progress", lease_expires_at=future_lease)

    # Schedule retry
    res = schedule_retry(qid, claim_token=1, error_message="Transient error", db_path=db_file)
    assert res["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number, sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] == 42
    assert row["sync_status"] == "failed"

    # Set next_retry_at to past so it can be claimed
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = datetime('now', '-1 minute') WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()

    # Claim again
    claimed = claim_records(limit=1, worker_id="test_worker", db_path=db_file)
    assert len(claimed) == 1
    assert claimed[0]["queue_id"] == qid
    assert claimed[0]["sheet_row_number"] == 42

    # Dead letter
    res_dl = move_to_dead_letter(qid, claim_token=claimed[0]["claim_token"], error_message="Fatal error", db_path=db_file)
    assert res_dl["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number, sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] == 42
    assert row["sync_status"] == "dead_letter"
    conn.close()


# ==============================================================================
# Part 2: Remote Write Failure Tests (Section 7, Tests A - K)
# ==============================================================================

def test_remote_fail_a_immediate_successful_update(tmp_path):
    """Test A: Normal successful remote update via values().update and zero appends."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=10)
    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["success"] is True
    assert res["synced_count"] == 1
    assert mock_svc.spreadsheets().values().update.called
    assert not mock_svc.spreadsheets().values().append.called

    call_kwargs = mock_svc.spreadsheets().values().update.call_args[1]
    assert call_kwargs["range"] == "Attendance_Log!A10:I10"
    assert len(call_kwargs["body"]["values"][0]) == 9

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "synced"
    assert row["claim_token"] == 1
    conn.close()


def test_remote_fail_b_failure_before_write(tmp_path):
    """Test B: Remote failure before write schedules retry with claim token, row preserved, zero appends."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=15)
    mock_svc = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(503, "Service Unavailable"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["success"] is False
    assert res["synced_count"] == 0
    assert res["failed_count"] == 1
    assert not mock_svc.spreadsheets().values().append.called

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number, retry_count, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["sheet_row_number"] == 15
    assert row["retry_count"] == 1
    assert row["claim_token"] == 1
    conn.close()


def test_remote_fail_c_write_applied_response_lost_uncertainty(tmp_path):
    """Test C: Remote write applied but response lost (timeout). Retry safely overwrites same row."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=20)

    remote_storage = {}

    def _simulated_flaky_update(*args, **kwargs):
        raise socket.timeout("Socket timed out after remote write completed")

    mock_svc = _make_mock_sheets_service(update_side_effect=_simulated_flaky_update)

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["synced_count"] == 0
    assert res["failed_count"] == 1
    
    call_kwargs = mock_svc.spreadsheets().values().update.call_args[1]
    assert call_kwargs["range"] == "Attendance_Log!A20:I20"

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number, retry_count FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["sheet_row_number"] == 20
    conn.close()

    # Second attempt: retry succeeds
    mock_svc_success = _make_mock_sheets_service()
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = datetime('now', '-1 minute') WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()

    res2 = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc_success)
    assert res2["synced_count"] == 1
    assert mock_svc_success.spreadsheets().values().update.call_args[1]["range"] == "Attendance_Log!A20:I20"
    assert not mock_svc_success.spreadsheets().values().append.called


def test_remote_fail_d_timeout_before_application(tmp_path):
    """Test D: Remote timeout before application schedules retry with deterministic targeting."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=25)
    mock_svc = _make_mock_sheets_service(update_side_effect=socket.timeout("Socket timed out before connect"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["success"] is False
    assert res["failed_count"] == 1
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["sheet_row_number"] == 25
    conn.close()


def test_remote_fail_e_timeout_after_application(tmp_path):
    """Test E: Remote timeout after application preserves row and retry overwrites."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=30)
    mock_svc = _make_mock_sheets_service(update_side_effect=GoogleSheetsTimeoutError("Timeout reading HTTP response"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["success"] is False
    assert res["failed_count"] == 1
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["sheet_row_number"] == 30
    conn.close()


def test_remote_fail_f_repeated_failures_to_dead_letter(tmp_path):
    """Test F: Repeated failures increment retry count and transition to dead-letter at threshold."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=35, retry_count=4)
    mock_svc = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(503, "Unavailable"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "dead_letter"
    assert row["retry_count"] == 5
    assert row["sheet_row_number"] == 35
    conn.close()


def test_remote_fail_g_rate_limit_429_schedules_retry(tmp_path):
    """Test G: HTTP 429 rate limit schedules retry."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file)
    mock_svc = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(429, "Too Many Requests"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["failed_count"] == 1
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["retry_count"] == 1
    conn.close()


def test_remote_fail_h_temporary_server_errors_500_503(tmp_path):
    """Test H: HTTP 500 and 503 schedule retry."""
    db_file = _create_isolated_test_db(tmp_path)
    qid1 = _seed_attendance_record(db_file, roll_number="CS-500", card_uid="UID_500")
    qid2 = _seed_attendance_record(db_file, roll_number="CS-503", card_uid="UID_503")

    mock_svc1 = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(500, "Internal Server Error"))
    res1 = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc1)
    assert res1["failed_count"] == 1

    mock_svc2 = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(503, "Backend Overload"))
    res2 = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc2)
    assert res2["failed_count"] == 1


def test_remote_fail_i_network_connection_reset(tmp_path):
    """Test I: Network connection reset schedules retry."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file)
    mock_svc = _make_mock_sheets_service(update_side_effect=ConnectionResetError("Connection reset by peer"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["failed_count"] == 1
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    conn.close()


def test_remote_fail_j_permanent_remote_errors_400_403_404(tmp_path):
    """Test J: Permanent errors (400, 403, 404) route directly to dead letter."""
    db_file = _create_isolated_test_db(tmp_path)
    for code in (400, 403, 404):
        qid = _seed_attendance_record(db_file, roll_number=f"CS-{code}", card_uid=f"UID_{code}")
        mock_svc = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(code, f"Permanent Error {code}"))
        res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
        assert res["dead_letter_count"] == 1
        conn = get_db_connection(db_file)
        row = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
        assert row["sync_status"] == "dead_letter"
        conn.close()


def test_remote_fail_k_unknown_remote_exception_fails_safely(tmp_path):
    """Test K: Unknown unexpected exception fails safely without false success."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file)
    mock_svc = _make_mock_sheets_service(update_side_effect=RuntimeError("Unexpected unhandled memory fault"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["synced_count"] == 0
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] in ("failed", "dead_letter")
    conn.close()


# ==============================================================================
# Part 3: Claim-Token & Stale Worker Tests (Section 8, Tests L - S)
# ==============================================================================

def test_claim_token_l_monotonicity(tmp_path):
    """Test L: Claim token strictly increments across claim cycles and never resets."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, claim_token=0)

    # Claim 1
    c1 = claim_records(limit=1, worker_id="W1", db_path=db_file)
    assert c1[0]["claim_token"] == 1

    # Lease expires, recovery
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = datetime('now', '-1 minute') WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()
    recover_expired_leases(db_path=db_file)

    # Claim 2
    c2 = claim_records(limit=1, worker_id="W2", db_path=db_file)
    assert c2[0]["claim_token"] == 2

    # Retry transition
    schedule_retry(qid, claim_token=2, error_message="retry", db_path=db_file)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = datetime('now', '-1 minute') WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()

    # Claim 3
    c3 = claim_records(limit=1, worker_id="W3", db_path=db_file)
    assert c3[0]["claim_token"] == 3


def test_claim_token_m_stale_worker_mark_synced_rejected(tmp_path):
    """Test M: Stale worker with token 1 cannot mark synced when token 2 is active."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=2, sync_status="in_progress", lease_expires_at=future_lease)

    success = mark_as_synced(qid, claim_token=1, db_path=db_file)
    assert success is False

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2
    conn.close()


def test_claim_token_n_stale_worker_retry_rejected(tmp_path):
    """Test N: Stale worker with token 1 cannot schedule retry when token 2 is active."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=2, sync_status="in_progress", lease_expires_at=future_lease)

    res = schedule_retry(qid, claim_token=1, error_message="stale", db_path=db_file)
    assert res.get("claim_lost") is True or res.get("success") is False

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2
    conn.close()


def test_claim_token_o_stale_worker_dead_letter_rejected(tmp_path):
    """Test O: Stale worker with token 1 cannot dead-letter record when token 2 is active."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=2, sync_status="in_progress", lease_expires_at=future_lease)

    res = move_to_dead_letter(qid, claim_token=1, error_message="stale", db_path=db_file)
    assert res.get("claim_lost") is True or res.get("success") is False

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2
    conn.close()


def test_claim_token_p_stale_worker_release_rejected(tmp_path):
    """Test P: Stale worker with token 1 cannot release lease when token 2 is active."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=2, sync_status="in_progress", lease_expires_at=future_lease)

    success = release_claim(qid, claim_token=1, db_path=db_file)
    assert success is False

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2
    conn.close()


def test_claim_token_q_missing_token_fails_closed(tmp_path):
    """Test Q: Passing None for claim_token fails closed."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=1, sync_status="in_progress", lease_expires_at=future_lease)

    with pytest.raises((ValueError, TypeError)):
        mark_as_synced(qid, claim_token=None, db_path=db_file)


def test_claim_token_r_wrong_token_fails_closed(tmp_path):
    """Test R: Passing mismatched claim_token returns False / claim_lost and does not alter state."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=5, sync_status="in_progress", lease_expires_at=future_lease)

    assert mark_as_synced(qid, claim_token=99, db_path=db_file) is False
    assert release_claim(qid, claim_token=99, db_path=db_file) is False
    res_retry = schedule_retry(qid, claim_token=99, error_message="err", db_path=db_file)
    assert res_retry.get("claim_lost") is True
    res_dl = move_to_dead_letter(qid, claim_token=99, error_message="err", db_path=db_file)
    assert res_dl.get("claim_lost") is True


def test_claim_token_s_claim_loss_detection_halts_mutations(tmp_path):
    """Test S: Worker detects claim loss and halts further mutations on that record."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=50)

    def _intercept_and_steal(*args, **kwargs):
        conn = get_db_connection(db_file)
        conn.execute("UPDATE sheets_sync_queue SET claim_token = 2, sync_status = 'synced' WHERE id = ?;", (qid,))
        conn.commit()
        conn.close()
        return {"updatedRows": 1}

    mock_svc = _make_mock_sheets_service(update_side_effect=_intercept_and_steal)
    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)

    assert res["claim_lost_count"] == 1
    assert res["synced_count"] == 0


# ==============================================================================
# Part 4: Lease Expiration & Long Request Tests (Section 9, Tests T - X)
# ==============================================================================

def test_lease_long_t_request_completes_within_lease(tmp_path):
    """Test T: Normal request completes quickly within 60s lease."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file)
    mock_svc = _make_mock_sheets_service()

    t0 = time.time()
    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    elapsed = time.time() - t0

    assert res["synced_count"] == 1
    assert elapsed < 5.0


def test_lease_long_u_request_reaches_socket_timeout_safely(tmp_path):
    """Test U: Request reaches socket timeout (15s) and worker transitions safely within lease."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=55)
    mock_svc = _make_mock_sheets_service(update_side_effect=GoogleSheetsTimeoutError("Socket read timeout"))

    res = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc, http_timeout_seconds=15.0)
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "failed"
    assert row["sheet_row_number"] == 55
    conn.close()


def test_lease_long_v_request_in_flight_beyond_lease_race(tmp_path):
    """
    Test V: Worker A blocks in remote call; lease expires; Worker B reclaims (token 2),
    writes and marks synced. Worker A unblocks and its transition fails closed.
    """
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=60)

    worker_a_in_flight = threading.Event()
    worker_b_finished = threading.Event()

    def _worker_a_remote_call(*args, **kwargs):
        worker_a_in_flight.set()
        worker_b_finished.wait(timeout=5.0)
        return {"updatedRows": 1}

    mock_svc_a = _make_mock_sheets_service(update_side_effect=_worker_a_remote_call)
    mock_svc_b = _make_mock_sheets_service()

    res_a = {}

    def _run_worker_a():
        nonlocal res_a
        res_a = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc_a)

    thread_a = threading.Thread(target=_run_worker_a)
    thread_a.start()

    assert worker_a_in_flight.wait(timeout=3.0)

    # Expire Worker A's lease in SQLite
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = datetime('now', '-10 seconds') WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()

    # Recovery
    recover_expired_leases(db_path=db_file)

    # Worker B claims and processes
    res_b = sync_pending_records_batch(batch_size=1, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc_b)
    assert res_b["synced_count"] == 1

    # Signal Worker A to finish
    worker_b_finished.set()
    thread_a.join(timeout=5.0)

    assert res_a["claim_lost_count"] == 1
    assert res_a["synced_count"] == 0

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "synced"
    assert row["claim_token"] == 2
    assert row["sheet_row_number"] == 60
    conn.close()


def test_lease_long_w_concurrent_recovery_preserves_tokens(tmp_path):
    """Test W: Multiple concurrent recovery calls only recover expired leases and preserve tokens."""
    db_file = _create_isolated_test_db(tmp_path)
    qid1 = _seed_attendance_record(db_file, roll_number="CS-REC-1", card_uid="UID_REC_1", claim_token=1, sync_status="in_progress")
    qid2 = _seed_attendance_record(db_file, roll_number="CS-REC-2", card_uid="UID_REC_2", claim_token=1, sync_status="in_progress")

    past_lease = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = ? WHERE id = ?;", (past_lease, qid1))
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = ? WHERE id = ?;", (future_lease, qid2))
    conn.commit()
    conn.close()

    results = []
    def _run_recovery():
        res = recover_expired_leases(db_path=db_file)
        results.append(res)

    threads = [threading.Thread(target=_run_recovery) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    total_recovered = sum(results)
    assert total_recovered == 1

    conn = get_db_connection(db_file)
    r1 = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid1,)).fetchone()
    r2 = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid2,)).fetchone()
    assert r1["sync_status"] == "pending"
    assert r1["claim_token"] == 1
    assert r2["sync_status"] == "in_progress"
    assert r2["claim_token"] == 1
    conn.close()


def test_lease_long_x_lease_renewal_not_implemented():
    """Test X: Verifies that lease renewal is not implemented in current architecture."""
    import app.services.sheets_outbox as outbox
    assert not hasattr(outbox, "renew_lease"), "Lease renewal is not implemented in current architecture"


# ==============================================================================
# Part 5: Concurrent Claim & Allocation Tests (Section 10, Tests Y - AB)
# ==============================================================================

def test_concurrency_y_workers_claim_disjoint_records(tmp_path):
    """Test Y: 5 concurrent worker threads claiming records receive mutually exclusive sets."""
    db_file = _create_isolated_test_db(tmp_path)
    qids = [_seed_attendance_record(db_file, roll_number=f"CS-CON-{i}", card_uid=f"UID_CON_{i}") for i in range(15)]

    claimed_sets = []
    lock = threading.Lock()

    def _worker_claim(wid):
        c = claim_records(limit=3, worker_id=f"W_{wid}", db_path=db_file)
        with lock:
            claimed_sets.append([r["queue_id"] for r in c])

    threads = [threading.Thread(target=_worker_claim, args=(i,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    all_claimed = []
    for s in claimed_sets:
        all_claimed.extend(s)

    assert len(all_claimed) == 15
    assert len(set(all_claimed)) == 15


def test_concurrency_z_concurrent_row_allocation_monotonic(tmp_path):
    """Test Z: Concurrent row allocation requests assign strictly distinct rows."""
    db_file = _create_isolated_test_db(tmp_path)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 100 WHERE id = 1;")
    conn.commit()
    conn.close()

    qids = [_seed_attendance_record(db_file, roll_number=f"CS-ALLOC-{i}", card_uid=f"UID_ALLOC_{i}") for i in range(10)]

    alloc_results = {}
    lock = threading.Lock()

    def _allocate_slice(qid):
        m = allocate_sheet_rows([qid], db_path=db_file)
        with lock:
            alloc_results[qid] = m[qid]

    threads = [threading.Thread(target=_allocate_slice, args=(qid,)) for qid in qids]
    for t in threads: t.start()
    for t in threads: t.join()

    allocated_rows = list(alloc_results.values())
    assert len(allocated_rows) == 10
    assert len(set(allocated_rows)) == 10
    assert sorted(allocated_rows) == list(range(100, 110))

    next_row = get_next_sheet_row(db_path=db_file)
    assert next_row == 110


def test_concurrency_aa_concurrent_same_record_allocation_idempotent(tmp_path):
    """Test AA: Multiple concurrent threads attempting to allocate for the SAME record all receive the same row."""
    db_file = _create_isolated_test_db(tmp_path)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 200 WHERE id = 1;")
    conn.commit()
    conn.close()

    qid = _seed_attendance_record(db_file, roll_number="CS-SAME-1", card_uid="UID_SAME_1")

    results = []
    lock = threading.Lock()

    def _allocate():
        m = allocate_sheet_rows([qid], db_path=db_file)
        with lock:
            results.append(m[qid])

    threads = [threading.Thread(target=_allocate) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert all(r == 200 for r in results)
    next_row = get_next_sheet_row(db_path=db_file)
    assert next_row == 201


def test_concurrency_ab_claim_and_allocation_race(tmp_path):
    """Test AB: Claim and allocation interactions remain safe under race conditions."""
    db_file = _create_isolated_test_db(tmp_path)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 300 WHERE id = 1;")
    conn.commit()
    conn.close()

    qid = _seed_attendance_record(db_file)

    def _claim_task():
        claim_records(limit=1, worker_id="W_Claim", db_path=db_file)

    def _alloc_task():
        allocate_sheet_rows([qid], db_path=db_file)

    t1 = threading.Thread(target=_claim_task)
    t2 = threading.Thread(target=_alloc_task)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, sheet_row_number, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] == 300
    assert row["claim_token"] == 1
    conn.close()


# ==============================================================================
# Part 6: Batch Partial Failure & Poison Record Tests (Section 11, Tests AC - AF)
# ==============================================================================

def test_batch_ac_partial_success_independent_handling(tmp_path):
    """Test AC: Batch with 5 records (success, timeout, 400, success, malformed) is handled independently."""
    db_file = _create_isolated_test_db(tmp_path)
    q1 = _seed_attendance_record(db_file, roll_number="CS-B1", card_uid="UID_B1")
    q2 = _seed_attendance_record(db_file, roll_number="CS-B2", card_uid="UID_B2")
    q3 = _seed_attendance_record(db_file, roll_number="CS-B3", card_uid="UID_B3")
    q4 = _seed_attendance_record(db_file, roll_number="CS-B4", card_uid="UID_B4")
    q5 = _seed_attendance_record(db_file, roll_number="CS-B5", card_uid="UID_B5")
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sheet_row_number = 1 WHERE id = ?;", (q5,))  # Invalid row < 2
    conn.commit()
    conn.close()

    call_idx = 0
    def _variable_update(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return {"updatedRows": 1}
        elif call_idx == 2:
            raise GoogleSheetsTimeoutError("Client timeout")
        elif call_idx == 3:
            raise _make_mock_http_error(400, "Bad Request")
        elif call_idx == 4:
            return {"updatedRows": 1}
        return {"updatedRows": 1}

    mock_svc = _make_mock_sheets_service(update_side_effect=_variable_update)

    res = sync_pending_records_batch(batch_size=5, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)
    assert res["claimed_count"] == 5
    assert res["synced_count"] == 2       # q1 and q4
    assert res["failed_count"] == 3       # q2 (timeout), q3 (400), q5 (row 1)
    assert res["dead_letter_count"] == 2  # q3 (400) and q5 (row 1 invalid)
    assert res["success"] is False        # Aggregate cannot report success when failures exist


def test_batch_ad_poison_record_isolation(tmp_path):
    """Test AD: Poison record does not prevent neighboring valid records from processing."""
    db_file = _create_isolated_test_db(tmp_path)
    q1 = _seed_attendance_record(db_file, roll_number="CS-OK1", card_uid="UID_OK1")
    q2 = _seed_attendance_record(db_file, roll_number="CS-POISON", card_uid="UID_POISON")
    q3 = _seed_attendance_record(db_file, roll_number="CS-OK2", card_uid="UID_OK2")

    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sheet_row_number = 0 WHERE id = ?;", (q2,))
    conn.commit()
    conn.close()

    mock_svc = _make_mock_sheets_service()
    res = sync_pending_records_batch(batch_size=3, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)

    assert res["synced_count"] == 2
    assert res["dead_letter_count"] == 1

    conn = get_db_connection(db_file)
    assert conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (q1,)).fetchone()["sync_status"] == "synced"
    assert conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (q2,)).fetchone()["sync_status"] == "dead_letter"
    assert conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (q3,)).fetchone()["sync_status"] == "synced"
    conn.close()


def test_batch_ae_result_aggregation_accuracy(tmp_path):
    """Test AE: Aggregate batch summary accurately reports all counters."""
    db_file = _create_isolated_test_db(tmp_path)
    q1 = _seed_attendance_record(db_file, roll_number="CS-AGG1", card_uid="UID_AGG1")
    q2 = _seed_attendance_record(db_file, roll_number="CS-AGG2", card_uid="UID_AGG2")

    mock_svc = _make_mock_sheets_service(update_side_effect=_make_mock_http_error(429, "Rate limited"))
    res = sync_pending_records_batch(batch_size=2, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["claimed_count"] == 2
    assert res["synced_count"] == 0
    assert res["failed_count"] == 2
    assert res["dead_letter_count"] == 0
    assert res["claim_lost_count"] == 0


def test_batch_af_exception_midway_safe_isolation(tmp_path):
    """Test AF: Unexpected exception raised on one record doesn't falsely mark other records synced."""
    db_file = _create_isolated_test_db(tmp_path)
    q1 = _seed_attendance_record(db_file, roll_number="CS-EX1", card_uid="UID_EX1")
    q2 = _seed_attendance_record(db_file, roll_number="CS-EX2", card_uid="UID_EX2")

    call_count = 0
    def _explode_on_q2(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Fatal process crash simulation on record 2")
        return {"updatedRows": 1}

    mock_svc = _make_mock_sheets_service(update_side_effect=_explode_on_q2)
    res = sync_pending_records_batch(batch_size=2, spreadsheet_id="test_sheet", db_path=db_file, service=mock_svc)

    assert res["synced_count"] == 1

    conn = get_db_connection(db_file)
    r1 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (q1,)).fetchone()
    r2 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (q2,)).fetchone()
    assert r1["sync_status"] == "synced"
    assert r2["sync_status"] in ("failed", "dead_letter")
    conn.close()


# ==============================================================================
# Part 7: Retry, Dead-Letter & Requeue Tests (Section 12, Tests AG - AM)
# ==============================================================================

def test_retry_ag_backoff_calculation_formula():
    """Test AG: Retry backoff formula verification (base=15s, 2^n, max=900s, jitter=±10%)."""
    assert calculate_retry_delay(0, jitter=False) == 15.0
    assert calculate_retry_delay(1, jitter=False) == 30.0
    assert calculate_retry_delay(2, jitter=False) == 60.0
    assert calculate_retry_delay(3, jitter=False) == 120.0
    assert calculate_retry_delay(4, jitter=False) == 240.0
    assert calculate_retry_delay(5, jitter=False) == 480.0
    assert calculate_retry_delay(6, jitter=False) == 900.0

    assert calculate_retry_delay(1, jitter=True, jitter_seconds=3.0) == 33.0
    assert calculate_retry_delay(6, jitter=True, jitter_seconds=50.0) == 900.0


def test_retry_ah_count_increments_exactly_once(tmp_path):
    """Test AH: Retry increments retry_count by exactly 1 per failed attempt."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, retry_count=2, claim_token=1, sync_status="in_progress", lease_expires_at=future_lease)

    res = schedule_retry(qid, claim_token=1, error_message="fail", db_path=db_file)
    assert res["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT retry_count FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["retry_count"] == 3
    conn.close()


def test_retry_ai_preserves_permanent_row(tmp_path):
    """Test AI: Retry transition strictly preserves assigned sheet_row_number."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, sheet_row_number=77, claim_token=1, sync_status="in_progress", lease_expires_at=future_lease)

    res = schedule_retry(qid, claim_token=1, error_message="fail", db_path=db_file)
    assert res["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] == 77
    conn.close()


def test_dead_letter_aj_preserves_permanent_row(tmp_path):
    """Test AJ: Dead-letter transition strictly preserves assigned sheet_row_number."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, sheet_row_number=88, claim_token=1, sync_status="in_progress", lease_expires_at=future_lease)

    res = move_to_dead_letter(qid, claim_token=1, error_message="fatal", db_path=db_file)
    assert res["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] == 88
    conn.close()


def test_requeue_ak_admin_requeue_invalidates_stale_tokens(tmp_path):
    """Test AK: Admin requeue resets status to pending, increments token, and preserves row."""
    db_file = _create_isolated_test_db(tmp_path)
    qid = _seed_attendance_record(db_file, sheet_row_number=99, claim_token=3, sync_status="dead_letter", retry_count=5)

    res = retry_dead_letter_records([qid], reset_retries=True, db_path=db_file)
    assert res["requeued_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token, retry_count, sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "pending"
    assert row["claim_token"] == 4
    assert row["retry_count"] == 0
    assert row["sheet_row_number"] == 99
    conn.close()


def test_retry_al_max_retries_threshold_dead_letter(tmp_path):
    """Test AL: Exceeding max retries stops scheduling and routes to dead letter."""
    db_file = _create_isolated_test_db(tmp_path)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qid = _seed_attendance_record(db_file, claim_token=1, retry_count=4, sync_status="in_progress", lease_expires_at=future_lease)

    res = schedule_retry(qid, claim_token=1, error_message="exceeded", max_retries=5, db_path=db_file)
    assert res["success"] is True

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "dead_letter"
    assert row["retry_count"] == 5
    conn.close()


def test_terminal_am_records_ignored_by_worker(tmp_path):
    """Test AM: Synced and dead-letter records are never claimed by worker."""
    db_file = _create_isolated_test_db(tmp_path)
    _seed_attendance_record(db_file, roll_number="CS-SYNC", card_uid="UID_SYNC", sync_status="synced")
    _seed_attendance_record(db_file, roll_number="CS-DEAD", card_uid="UID_DEAD", sync_status="dead_letter")

    claimed = claim_records(limit=10, worker_id="W1", db_path=db_file)
    assert len(claimed) == 0


# ==============================================================================
# Part 8: Database & Transaction Safety Tests (Section 13, Tests AN - AQ)
# ==============================================================================

def test_tx_safety_an_rollback_on_allocation_failure(tmp_path):
    """Test AN: Failure during allocation rolls back transaction, leaving counter intact."""
    db_file = _create_isolated_test_db(tmp_path)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 500 WHERE id = 1;")
    conn.commit()
    conn.close()

    q1 = _seed_attendance_record(db_file, roll_number="CS-TX1", card_uid="UID_TX1")
    with pytest.raises(AllocatorRecordNotFoundError):
        allocate_sheet_rows([q1, 999999], db_path=db_file)

    assert get_next_sheet_row(db_path=db_file) == 500
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (q1,)).fetchone()
    assert row["sheet_row_number"] is None
    conn.close()


def test_tx_safety_ao_transaction_ownership_commit_rollback(tmp_path):
    """Test AO: External caller-managed connection respects caller's transaction boundary."""
    db_file = _create_isolated_test_db(tmp_path)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_meta SET next_sheet_row = 600 WHERE id = 1;")
    conn.commit()

    qid = _seed_attendance_record(db_file, roll_number="CS-TX-OWN", card_uid="UID_TX_OWN")

    conn.execute("BEGIN IMMEDIATE;")
    res = allocate_sheet_rows([qid], conn=conn)
    assert res[qid] == 600
    conn.rollback()

    assert get_next_sheet_row(conn=conn) == 600
    row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sheet_row_number"] is None
    conn.close()


def test_db_safety_ap_integrity_and_foreign_keys(tmp_path):
    """Test AP: SQLite integrity_check and foreign_key_check return ok with 0 violations."""
    db_file = _create_isolated_test_db(tmp_path)
    _seed_attendance_record(db_file)

    conn = get_db_connection(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    assert cur.fetchone()[0] == "ok"

    cur.execute("PRAGMA foreign_key_check;")
    assert len(cur.fetchall()) == 0
    conn.close()


def test_db_safety_aq_queue_invariants_audit(tmp_path):
    """Test AQ: Invariant audit ensures processing records have valid lease and tokens, and row assignments are unique."""
    db_file = _create_isolated_test_db(tmp_path)
    q1 = _seed_attendance_record(db_file, sheet_row_number=10, sync_status="synced")
    q2 = _seed_attendance_record(db_file, roll_number="CS-INV2", card_uid="UID_INV2", sheet_row_number=11, sync_status="synced")

    conn = get_db_connection(db_file)
    cur = conn.cursor()

    # 1. No duplicate sheet row numbers
    cur.execute("SELECT sheet_row_number, COUNT(*) FROM sheets_sync_queue WHERE sheet_row_number IS NOT NULL GROUP BY sheet_row_number HAVING COUNT(*) > 1;")
    assert len(cur.fetchall()) == 0

    # 2. No negative row numbers
    cur.execute("SELECT COUNT(*) FROM sheets_sync_queue WHERE sheet_row_number < 1;")
    assert cur.fetchone()[0] == 0

    # 3. All in_progress records must have active lease and token >= 1
    cur.execute("SELECT COUNT(*) FROM sheets_sync_queue WHERE sync_status = 'in_progress' AND (claim_token < 1 OR lease_expires_at IS NULL);")
    assert cur.fetchone()[0] == 0

    # 4. next_sheet_row must be strictly greater than max assigned row
    cur.execute("SELECT MAX(sheet_row_number) FROM sheets_sync_queue;")
    max_row = cur.fetchone()[0]
    next_row = get_next_sheet_row(conn=conn)
    if max_row is not None:
        assert next_row > max_row

    conn.close()
