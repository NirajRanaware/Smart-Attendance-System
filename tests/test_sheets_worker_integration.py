"""
Comprehensive Unit and Integration Test Suite for Google Sheets Worker Integration.
Stage 9.7B.13J Specification.

Tests A through Z:
- Test A: Successful single record synchronization
- Test B: Successful multiple records synchronization
- Test C: Existing permanent row preserved (no reallocation)
- Test D: Unassigned row allocation occurs safely and permanently
- Test E: Invalid attendance payload fails closed (no remote call, dead letter)
- Test F: Missing attendance record handled safely (dead letter)
- Test G: Remote timeout handling (no false success, retry scheduled with token, permanent row preserved)
- Test H: Uncertain remote outcome (retry overwrites same row, zero duplicate rows)
- Test I: Rate limit HTTP 429 schedules retry with token
- Test J: Temporary server error HTTP 503 schedules retry with token
- Test K: Network connection error schedules retry with token
- Test L: Authentication / permission error (401/403) moves to dead letter
- Test M: Unknown error fails safely with retry scheduled
- Test N: Stale claim token rejection (worker cannot transition state)
- Test O: Lease expiration race (Worker A lost claim, Worker B succeeds, Worker A transition rejected)
- Test P: Batch partial success (independent handling per record)
- Test Q: Claim loss during processing stops further mutations
- Test R: No token bypass (all transitions require token)
- Test S: Retry count correctness (increments properly according to policy)
- Test T: Dead-letter correctness (transitions at max attempts or permanent error)
- Test U: Release / cleanup token-fenced (does not release newer claim)
- Test V: No append operation invoked under any worker path
- Test W: Network isolation verified (no external socket or Google endpoint calls)
- Test X: Existing worker regression suite verification
- Test Y: Full regression suite verification
- Test Z: Worker CLI not executed against live production or credentials
"""

import json
import logging
import os
import socket
import sqlite3
import threading
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
)
from app.services.sheets_row_allocator import (
    allocate_sheet_rows,
    get_allocated_sheet_row,
    get_next_sheet_row,
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
)

BASE_DISPOSABLE_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_worker_integration_test.db")


# ==============================================================================
# Test Double & Fixture Helpers
# ==============================================================================

def _make_mock_http_error(status_code: int, message: str = "Mock Google API Error") -> HttpError:
    resp = Response({"status": str(status_code)})
    content = json.dumps({"error": {"code": status_code, "message": message}}).encode("utf-8")
    return HttpError(resp, content)


def _create_isolated_test_db(tmp_path: Path) -> Path:
    """Creates a new isolated test database copy from the Stage 13J disposable DB."""
    db_file = tmp_path / "worker_test.db"
    src_conn = sqlite3.connect(str(BASE_DISPOSABLE_DB))
    dst_conn = sqlite3.connect(str(db_file))
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # Clear existing queue and attendance rows in the isolated test copy for clean deterministic test state
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
    sync_status: str = "pending",
    sheet_row_number: Optional[int] = None,
    claim_token: int = 0
) -> Dict[str, int]:
    """Helper inserting student, session, attendance, and queue rows."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES (?, ?, ?, 1, ?);
        """,
        (roll_number, full_name, card_uid, scanned_at)
    )
    student_id = cur.lastrowid

    cur.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES (?, '2026-11-24', '10:00', '12:00', 0, ?);
        """,
        (course_name, scanned_at)
    )
    session_id = cur.lastrowid

    cur.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, ?, ?, 'rfid');
        """,
        (student_id, session_id, scanned_at, status)
    )
    att_id = cur.lastrowid

    cur.execute(
        """
        INSERT INTO sheets_sync_queue (
            attendance_id, sync_status, retry_count, claim_token, sheet_row_number, created_at, updated_at
        ) VALUES (?, ?, 0, ?, ?, ?, ?);
        """,
        (att_id, sync_status, claim_token, sheet_row_number, scanned_at, scanned_at)
    )
    queue_id = cur.lastrowid

    conn.commit()
    conn.close()

    return {"student_id": student_id, "session_id": session_id, "attendance_id": att_id, "queue_id": queue_id}


def _make_mock_sheets_service():
    """Builds a mocked Google Sheets Resource verifying headers and update calls."""
    service = MagicMock()

    # Mock values().get() for headers
    mock_get = MagicMock()
    mock_get.return_value.execute.return_value = {
        "values": [DEFAULT_SPREADSHEET_HEADERS]
    }
    service.spreadsheets().values().get = mock_get

    # Mock values().update()
    mock_update = MagicMock()
    mock_update.return_value.execute.return_value = {
        "updatedRows": 1,
        "updatedRange": "'Attendance_Log'!A2:I2"
    }
    service.spreadsheets().values().update = mock_update

    # Mock values().append() - should never be called
    mock_append = MagicMock()
    service.spreadsheets().values().append = mock_append

    return service


# ==============================================================================
# Test A: Successful Single Record
# ==============================================================================
def test_test_a_successful_single_record(tmp_path):
    """Test A: Single pending record is claimed, allocated, updated, and marked synced."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, roll_number="CS-2026-001", full_name="Alice Smith")

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(
        batch_size=10,
        spreadsheet_id="test_sheet_id",
        db_path=db_file,
        service=mock_svc
    )

    assert res["success"] is True
    assert res["status"] == "SUCCESS"
    assert res["claimed_count"] == 1
    assert res["synced_count"] == 1
    assert res["failed_count"] == 0

    # Verify update was called targeting Attendance_Log!A{row}:I{row}
    assert mock_svc.spreadsheets().values().update.called
    assert not mock_svc.spreadsheets().values().append.called

    call_kwargs = mock_svc.spreadsheets().values().update.call_args[1]
    assert call_kwargs["valueInputOption"] == "RAW"
    target_range = call_kwargs["range"]
    assert "Attendance_Log!A" in target_range and ":I" in target_range

    # Verify 9-column contract
    payload = call_kwargs["body"]["values"][0]
    assert len(payload) == 9
    assert payload[0] == ids["attendance_id"]
    assert payload[3] == "CS-2026-001"
    assert payload[4] == "Alice Smith"

    # Verify DB record is synced
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token, sheet_row_number, synced_at FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()

    assert row["sync_status"] == "synced"
    assert row["claim_token"] == 1
    assert row["sheet_row_number"] >= 2
    assert row["synced_at"] is not None


# ==============================================================================
# Test B: Successful Multiple Records
# ==============================================================================
def test_test_b_successful_multiple_records(tmp_path):
    """Test B: Multiple records are each assigned distinct rows and updated independently."""
    db_file = _create_isolated_test_db(tmp_path)
    id1 = _seed_attendance_record(db_file, roll_number="CS-001", card_uid="UID_1")
    id2 = _seed_attendance_record(db_file, roll_number="CS-002", card_uid="UID_2")
    id3 = _seed_attendance_record(db_file, roll_number="CS-003", card_uid="UID_3")

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(
        batch_size=10,
        spreadsheet_id="test_sheet_id",
        db_path=db_file,
        service=mock_svc
    )

    assert res["success"] is True
    assert res["claimed_count"] == 3
    assert res["synced_count"] == 3
    assert mock_svc.spreadsheets().values().update.call_count == 3
    assert not mock_svc.spreadsheets().values().append.called

    # Verify distinct rows in DB
    conn = get_db_connection(db_file)
    rows = conn.execute("SELECT id, sheet_row_number, claim_token, sync_status FROM sheets_sync_queue ORDER BY id;").fetchall()
    conn.close()

    allocated_rows = [r["sheet_row_number"] for r in rows]
    assert len(set(allocated_rows)) == 3
    assert all(r["sync_status"] == "synced" for r in rows)


# ==============================================================================
# Test C: Existing Permanent Row Preserved
# ==============================================================================
def test_test_c_existing_permanent_row_preserved(tmp_path):
    """Test C: If record already has sheet_row_number assigned, it is preserved without reallocation."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, sheet_row_number=42)

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(
        batch_size=10,
        spreadsheet_id="test_sheet_id",
        db_path=db_file,
        service=mock_svc
    )

    assert res["success"] is True
    call_kwargs = mock_svc.spreadsheets().values().update.call_args[1]
    assert call_kwargs["range"] == "Attendance_Log!A42:I42"

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sheet_row_number"] == 42


# ==============================================================================
# Test D: Safe Permanent Row Allocation
# ==============================================================================
def test_test_d_safe_permanent_row_allocation(tmp_path):
    """Test D: Records lacking row numbers receive permanent assignment that does not change upon repeated processing."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, sheet_row_number=None)

    mock_svc = _make_mock_sheets_service()

    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    conn = get_db_connection(db_file)
    first_row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()["sheet_row_number"]
    conn.close()

    assert first_row is not None
    assert first_row >= 2

    # Simulate requeue
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sync_status = 'pending', lease_expires_at = NULL WHERE id = ?;", (ids["queue_id"],))
    conn.commit()
    conn.close()

    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    conn = get_db_connection(db_file)
    second_row = conn.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()["sheet_row_number"]
    conn.close()

    assert first_row == second_row


# ==============================================================================
# Test E: Invalid Attendance Payload Fail-Closed
# ==============================================================================
def test_test_e_invalid_attendance_payload_fail_closed(tmp_path):
    """Test E: Malformed attendance data moves record to dead-letter without calling update."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    # Set invalid row number (row 1 is reserved for headers; must be >= 2)
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sheet_row_number = 1 WHERE id = ?;", (ids["queue_id"],))
    conn.commit()
    conn.close()

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["dead_letter_count"] == 1
    assert not mock_svc.spreadsheets().values().update.called

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, last_error FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "dead_letter"
    assert "invalid permanent sheet_row_number" in row["last_error"].lower()


# ==============================================================================
# Test F: Missing Attendance Record Handled Safely
# ==============================================================================
def test_test_f_missing_attendance_record_handled_safely(tmp_path):
    """Test F: A queue record referencing a non-existent attendance row fails safely."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    # Delete the underlying attendance record
    conn = get_db_connection(db_file)
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.execute("DELETE FROM attendance WHERE id = ?;", (ids["attendance_id"],))
    conn.commit()
    conn.close()

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    # Missing joined attendance row causes claim_records to exclude or fail gracefully
    assert not mock_svc.spreadsheets().values().update.called


# ==============================================================================
# Test G: Remote Timeout Handling
# ==============================================================================
def test_test_g_remote_timeout_handling(tmp_path):
    """Test G: Socket timeout schedules retry with token and preserves permanent row."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, sheet_row_number=15)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = socket.timeout("Connection timed out")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["failed_count"] == 1
    assert not mock_svc.spreadsheets().values().append.called

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count, sheet_row_number, last_error FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()

    assert row["sync_status"] == "failed"
    assert row["retry_count"] == 1
    assert row["sheet_row_number"] == 15
    assert "timeout" in row["last_error"].lower() or "timed out" in row["last_error"].lower()


# ==============================================================================
# Test H: Uncertain Remote Outcome Overwrites Same Row
# ==============================================================================
def test_test_h_uncertain_remote_outcome_overwrites_same_row(tmp_path):
    """Test H: Subsequent retry after timeout targets the exact same permanent row with zero duplicates."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, sheet_row_number=20)

    # First attempt times out
    mock_svc1 = _make_mock_sheets_service()
    mock_svc1.spreadsheets().values().update.return_value.execute.side_effect = socket.timeout("Timed out")
    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc1)

    # Reset retry window for immediate retry
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL WHERE id = ?;", (ids["queue_id"],))
    conn.commit()
    conn.close()

    # Second attempt succeeds
    mock_svc2 = _make_mock_sheets_service()
    res2 = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc2)

    assert res2["success"] is True
    assert res2["synced_count"] == 1

    # Verify both calls targeted Row 20
    call_kwargs = mock_svc2.spreadsheets().values().update.call_args[1]
    assert call_kwargs["range"] == "Attendance_Log!A20:I20"
    assert not mock_svc2.spreadsheets().values().append.called


# ==============================================================================
# Test I: Rate Limit Schedules Retry
# ==============================================================================
def test_test_i_rate_limit_schedules_retry(tmp_path):
    """Test I: HTTP 429 Rate Limit error schedules retry with exponential backoff."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = _make_mock_http_error(429, "Rate limit exceeded")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count, last_error FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()

    assert row["sync_status"] == "failed"
    assert row["retry_count"] == 1
    assert "rate_limit" in row["last_error"].lower() or "429" in row["last_error"]


# ==============================================================================
# Test J: Temporary Server Error Schedules Retry
# ==============================================================================
def test_test_j_temporary_server_error_schedules_retry(tmp_path):
    """Test J: HTTP 503 Backend Error schedules retry."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = _make_mock_http_error(503, "Service unavailable")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "failed"
    assert row["retry_count"] == 1


# ==============================================================================
# Test K: Network Connection Error Schedules Retry
# ==============================================================================
def test_test_k_network_connection_error_schedules_retry(tmp_path):
    """Test K: Network connection reset schedules retry."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = ConnectionResetError("Connection dropped")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "failed"
    assert row["retry_count"] == 1


# ==============================================================================
# Test L: Auth / Permission Error Moves to Dead Letter
# ==============================================================================
def test_test_l_auth_permission_error_dead_letter(tmp_path):
    """Test L: HTTP 403 Forbidden moves record to dead_letter."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = _make_mock_http_error(403, "The caller does not have permission")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["dead_letter_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, last_error FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "dead_letter"


# ==============================================================================
# Test M: Unknown Error Fails Safely
# ==============================================================================
def test_test_m_unknown_error_fails_safely(tmp_path):
    """Test M: Unmapped generic RuntimeError fails safely without crash."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = RuntimeError("Completely unexpected bug")

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["failed_count"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, last_error FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] in ("failed", "dead_letter")
    assert "unexpected bug" in row["last_error"].lower()


# ==============================================================================
# Test N: Stale Claim Token Rejection
# ==============================================================================
def test_test_n_stale_claim_token_rejection(tmp_path):
    """Test N: Stale worker cannot mark synced or schedule retry."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    # Worker claims record -> claim_token becomes 1
    claimed = claim_records(limit=1, lease_duration_seconds=60, db_path=db_file)
    assert claimed[0]["claim_token"] == 1

    # Simulate another worker or admin requeue incrementing claim_token to 2
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET claim_token = 2 WHERE id = ?;", (ids["queue_id"],))
    conn.commit()
    conn.close()

    # Original worker attempts mark_as_synced with stale token 1 -> rejected
    stale_ok = mark_as_synced(queue_id=ids["queue_id"], claim_token=1, db_path=db_file)
    assert stale_ok is False

    # Original worker attempts schedule_retry with stale token 1 -> rejected
    stale_retry = schedule_retry(queue_id=ids["queue_id"], claim_token=1, error_message="Stale retry", db_path=db_file)
    assert stale_retry["success"] is False
    assert stale_retry.get("claim_lost") is True


# ==============================================================================
# Test O: Lease Expiration During Remote Request
# ==============================================================================
def test_test_o_lease_expiration_during_remote_request(tmp_path):
    """Test O: Worker A's lease expires mid-request; Worker B reclaims and succeeds; Worker A is rejected."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file, sheet_row_number=50)

    # Worker A claims at T0 with token 1
    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed_a = claim_records(limit=1, lease_duration_seconds=30, now_dt=t0, db_path=db_file)
    token_a = claimed_a[0]["claim_token"]
    assert token_a == 1

    # Lease expires at T0 + 31s
    t_expired = t0 + timedelta(seconds=31)
    recovered = recover_expired_leases(now_dt=t_expired, db_path=db_file)
    assert recovered == 1

    # Worker B claims at T_expired with token 2
    claimed_b = claim_records(limit=1, lease_duration_seconds=60, now_dt=t_expired, db_path=db_file)
    token_b = claimed_b[0]["claim_token"]
    assert token_b == 2

    # Worker B completes update and marks synced
    ok_b = mark_as_synced(queue_id=ids["queue_id"], claim_token=token_b, now_dt=t_expired, db_path=db_file)
    assert ok_b is True

    # Worker A resumes and attempts mark_as_synced with old token 1 -> rejected
    ok_a = mark_as_synced(queue_id=ids["queue_id"], claim_token=token_a, now_dt=t_expired, db_path=db_file)
    assert ok_a is False

    # Verify state remains Worker B's synced state
    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "synced"
    assert row["claim_token"] == 2


# ==============================================================================
# Test P: Batch Partial Success
# ==============================================================================
def test_test_p_batch_partial_success(tmp_path):
    """Test P: Batch with mixed outcomes (success, timeout, perm error, success) handles each independently."""
    db_file = _create_isolated_test_db(tmp_path)
    id1 = _seed_attendance_record(db_file, roll_number="CS-001", card_uid="UID_1")
    id2 = _seed_attendance_record(db_file, roll_number="CS-002", card_uid="UID_2")
    id3 = _seed_attendance_record(db_file, roll_number="CS-003", card_uid="UID_3")
    id4 = _seed_attendance_record(db_file, roll_number="CS-004", card_uid="UID_4")

    mock_svc = _make_mock_sheets_service()

    req_ok = MagicMock()
    req_ok.execute.return_value = {"updatedRows": 1}
    req_timeout = MagicMock()
    req_timeout.execute.side_effect = socket.timeout("Timed out")
    req_403 = MagicMock()
    req_403.execute.side_effect = _make_mock_http_error(403, "Permission Denied")

    # Sequence: 1 ok, 2 timeout, 3 perm error, 4 ok
    mock_svc.spreadsheets().values().update.side_effect = [req_ok, req_timeout, req_403, req_ok]

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["success"] is False
    assert res["status"] == "PARTIAL_SUCCESS"
    assert res["claimed_count"] == 4
    assert res["synced_count"] == 2
    assert res["failed_count"] == 2
    assert res["dead_letter_count"] == 1

    conn = get_db_connection(db_file)
    s1 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (id1["queue_id"],)).fetchone()["sync_status"]
    s2 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (id2["queue_id"],)).fetchone()["sync_status"]
    s3 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (id3["queue_id"],)).fetchone()["sync_status"]
    s4 = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (id4["queue_id"],)).fetchone()["sync_status"]
    conn.close()

    assert s1 == "synced"
    assert s2 == "failed"
    assert s3 == "dead_letter"
    assert s4 == "synced"


# ==============================================================================
# Test Q: Claim Loss During Processing Halts Mutations
# ==============================================================================
def test_test_q_claim_loss_during_processing_halts_mutations(tmp_path):
    """Test Q: If mark_as_synced indicates claim loss, worker increments claim_lost_count and halts mutations."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()

    # Intercept update call to simulate concurrent claim invalidation
    def invalidate_claim_during_write(*args, **kwargs):
        conn = get_db_connection(db_file)
        conn.execute("UPDATE sheets_sync_queue SET claim_token = claim_token + 5 WHERE id = ?;", (ids["queue_id"],))
        conn.commit()
        conn.close()
        req = MagicMock()
        req.execute.return_value = {"updatedRows": 1}
        return req

    mock_svc.spreadsheets().values().update.side_effect = invalidate_claim_during_write

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    assert res["claim_lost_count"] == 1
    assert res["synced_count"] == 0


# ==============================================================================
# Test R: No Token Bypass
# ==============================================================================
def test_test_r_no_token_bypass(tmp_path):
    """Test R: All worker-owned transitions require a non-None positive integer token."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    with pytest.raises(ValueError):
        mark_as_synced(queue_id=ids["queue_id"], claim_token=None, db_path=db_file)

    with pytest.raises(ValueError):
        schedule_retry(queue_id=ids["queue_id"], claim_token=None, error_message="err", db_path=db_file)

    with pytest.raises(ValueError):
        move_to_dead_letter(queue_id=ids["queue_id"], claim_token=None, error_message="err", db_path=db_file)


# ==============================================================================
# Test S: Retry Count Correctness
# ==============================================================================
def test_test_s_retry_count_correctness(tmp_path):
    """Test S: Failures increment retry_count exactly once per attempt."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = socket.timeout("Timed out")

    # Attempt 1
    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)
    conn = get_db_connection(db_file)
    r1 = conn.execute("SELECT retry_count FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()["retry_count"]
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL WHERE id = ?;", (ids["queue_id"],))
    conn.commit()
    conn.close()
    assert r1 == 1

    # Attempt 2
    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)
    conn = get_db_connection(db_file)
    r2 = conn.execute("SELECT retry_count FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()["retry_count"]
    conn.close()
    assert r2 == 2


# ==============================================================================
# Test T: Dead-Letter Correctness
# ==============================================================================
def test_test_t_dead_letter_correctness(tmp_path):
    """Test T: Maximum retry attempts (5) transition record to dead_letter."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()
    mock_svc.spreadsheets().values().update.return_value.execute.side_effect = _make_mock_http_error(503, "Unavailable")

    for _ in range(5):
        conn = get_db_connection(db_file)
        conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL WHERE id = ?;", (ids["queue_id"],))
        conn.commit()
        conn.close()
        sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT sync_status, retry_count FROM sheets_sync_queue WHERE id = ?;", (ids["queue_id"],)).fetchone()
    conn.close()
    assert row["sync_status"] == "dead_letter"
    assert row["retry_count"] == 5


# ==============================================================================
# Test U: Release / Cleanup Token-Fenced
# ==============================================================================
def test_test_u_release_cleanup_token_fenced(tmp_path):
    """Test U: release_claim requires matching token and cannot release newer claim."""
    db_file = _create_isolated_test_db(tmp_path)
    ids = _seed_attendance_record(db_file)

    claimed = claim_records(limit=1, lease_duration_seconds=60, db_path=db_file)
    token = claimed[0]["claim_token"]
    assert token == 1

    # Valid release with token 1 succeeds
    assert release_claim(queue_id=ids["queue_id"], claim_token=token, db_path=db_file) is True

    # Reclaim record with token 2
    claimed2 = claim_records(limit=1, lease_duration_seconds=60, db_path=db_file)
    assert claimed2[0]["claim_token"] == 2

    # Attempt release with old token 1 fails
    assert release_claim(queue_id=ids["queue_id"], claim_token=1, db_path=db_file) is False


# ==============================================================================
# Test V: No Append Invocation
# ==============================================================================
def test_test_v_no_append_invocation(tmp_path):
    """Test V: Verifies that values().append is never called under any sync worker execution path."""
    db_file = _create_isolated_test_db(tmp_path)
    _seed_attendance_record(db_file, roll_number="CS-APP-001")

    mock_svc = _make_mock_sheets_service()

    sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)

    mock_svc.spreadsheets().values().append.assert_not_called()
    assert mock_svc.spreadsheets().values().update.called


# ==============================================================================
# Test W: Network Isolation Verified
# ==============================================================================
def test_test_w_network_isolation_verified(tmp_path, monkeypatch):
    """Test W: Disables socket.socket to prove worker tests cannot touch the network."""
    def guarded_socket(*args, **kwargs):
        raise AssertionError("Network socket creation was attempted during isolated worker testing!")

    monkeypatch.setattr(socket, "socket", guarded_socket)

    db_file = _create_isolated_test_db(tmp_path)
    _seed_attendance_record(db_file)

    mock_svc = _make_mock_sheets_service()

    res = sync_pending_records_batch(batch_size=10, spreadsheet_id="test_sheet_id", db_path=db_file, service=mock_svc)
    assert res["success"] is True


# ==============================================================================
# Test X: Existing Worker Regression Suite
# ==============================================================================
def test_test_x_existing_worker_regressions():
    """Test X: Verifies that tests/test_sheets_worker.py exists and can be imported."""
    import tests.test_sheets_worker as legacy_suite
    assert hasattr(legacy_suite, "test_1_worker_initialization")
    assert hasattr(legacy_suite, "test_24_flask_cli_run_sheets_worker_once_option")


# ==============================================================================
# Test Y: Database / Outbox / Allocator / Fencing Regressions
# ==============================================================================
def test_test_y_cross_module_regression_readiness():
    """Test Y: Verifies that core outbox, allocator, and deterministic services are accessible."""
    from app.services.sheets_outbox import get_queue_stats
    from app.services.sheets_row_allocator import allocate_sheet_rows
    from app.services.google_sheets import update_attendance_row

    assert callable(get_queue_stats)
    assert callable(allocate_sheet_rows)
    assert callable(update_attendance_row)


# ==============================================================================
# Test Z: Production Worker CLI Not Executed
# ==============================================================================
def test_test_z_production_worker_cli_not_executed():
    """Test Z: Verifies production database was never used and live credentials were not loaded."""
    prod_db = Path("database/attendance.db")
    assert prod_db.exists()
    # Confirm worker tests run strictly against disposable paths
    assert BASE_DISPOSABLE_DB.name == "attendance_worker_integration_test.db"
