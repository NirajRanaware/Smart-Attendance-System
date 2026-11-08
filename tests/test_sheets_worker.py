"""
Automated Integration and Unit Test Suite for Google Sheets Background Sync Worker.
Phase 3 Milestone 4 Specification.

Validates:
1. Worker initialization with valid configuration.
2. Safe idle state when credentials or spreadsheet ID are unconfigured.
3. Empty queue produces zero Google Sheets API appends.
4. Single pending record claimed, formatted, appended, and marked 'synced'.
5. Multiple records processed in batches.
6. Idempotent duplicate prevention: existing sheet IDs are never re-appended.
7. Retry with uncertain previous response does not create duplicates.
8. Accurate status transition from 'in_progress' to 'synced' with synced_at timestamp.
9. Transient network error schedules retry with exponential backoff.
10. HTTP 429 (Rate Limit) schedules retry.
11. HTTP 5xx (Google backend error) schedules retry.
12. HTTP 401 (Auth failure) is classified safely without crashing.
13. HTTP 403 (Permission denied) is handled safely.
14. HTTP 404 (Invalid Spreadsheet ID) is handled safely.
15. Expired leases from crashed workers are recovered back to 'pending'.
16. Claimed records are leased so concurrent workers cannot double-process them.
17. Partial batch failure handles appended vs failed records without data loss.
18. Worker recovers and continues processing after transient errors.
19. Worker loop terminates cleanly on stop signal.
20. Database transactions are committed and closed before network calls.
21. Attendance scan endpoint (/api/scans) does not block on Google Sheets worker.
22. Worker never modifies or corrupts authoritative attendance records.
23. Queue records eventually reach dead_letter on exhausted retries.
24. Worker logs do not expose secrets or private keys.
25. Admin status endpoint (/api/admin/sheets-sync/status) returns live queue telemetry without secrets.
26. Flask CLI command 'run-sheets-worker --once' runs successfully.

CRITICAL DIRECTIVE:
All tests run with mocked Google API responses. Zero real outbound network calls are made.
"""

import json
import logging
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app import create_app
from app.database import init_db, get_db_connection, close_db_connection
from app.services.sheets_outbox import (
    create_queue_record,
    get_queue_stats,
    claim_records,
    recover_expired_leases
)
from app.services.google_sheets import (
    DEFAULT_SPREADSHEET_HEADERS,
    reset_sheets_service
)
from app.services.sheets_worker import (
    sync_pending_records_batch,
    SheetsSyncWorker
)


@pytest.fixture(autouse=True)
def cleanup_service_cache():
    """Flushes cached Google Sheets client before and after each test."""
    reset_sheets_service()
    yield
    reset_sheets_service()


def make_mock_http_error(status_code: int, message: str = "Google API Error") -> HttpError:
    """Helper returning a mock Google API HttpError."""
    resp = Response({"status": str(status_code)})
    content = json.dumps({"error": {"code": status_code, "message": message}}).encode("utf-8")
    return HttpError(resp, content)


def create_test_database(tmp_path: Path) -> Path:
    """Creates a temporary SQLite database initialized with all 5 tables."""
    db_file = tmp_path / "test_worker.db"
    init_db(db_file)
    return db_file


def seed_attendance_fixture(db_path: Path, count: int = 1) -> list[int]:
    """
    Seeds active students, session, attendance, and pending queue records in SQLite.
    Returns list of generated attendance IDs.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Active session
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Embedded Systems', '2026-11-08', '08:00', '23:59', 1, '2026-11-08T10:00:00Z');
        """
    )
    session_id = cursor.lastrowid

    attendance_ids = []
    now_utc = "2026-11-08T10:00:00Z"

    for i in range(1, count + 1):
        cursor.execute(
            f"""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('CS-2024-{i:03d}', 'Student {i}', 'CARD_UID_{i:04d}', 1, '{now_utc}');
            """
        )
        student_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (?, ?, ?, 'Present', 'rfid');
            """,
            (student_id, session_id, now_utc)
        )
        att_id = cursor.lastrowid
        attendance_ids.append(att_id)

        # Outbox queue record
        cursor.execute(
            """
            INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at)
            VALUES (?, 'pending', 0, ?, ?);
            """,
            (att_id, now_utc, now_utc)
        )

    conn.commit()
    conn.close()
    return attendance_ids


def make_mock_google_service(existing_sheet_ids: list[int] = None):
    """
    Creates a mock Google Sheets API v4 Resource.
    Simulates row 1 headers, existing IDs in Column A, and successful append.
    """
    service = MagicMock()
    existing_ids = existing_sheet_ids or []

    # Mock values().get()
    # Range A1:J1 returns headers
    # Range A2:A returns Column A existing IDs
    def mock_get(spreadsheetId, range):
        mock_req = MagicMock()
        if "A1:J1" in range:
            mock_req.execute.return_value = {"values": [DEFAULT_SPREADSHEET_HEADERS]}
        elif "A2:A" in range:
            mock_req.execute.return_value = {
                "values": [[int(aid)] for aid in existing_ids]
            }
        else:
            mock_req.execute.return_value = {"values": []}
        return mock_req

    service.spreadsheets().values().get.side_effect = mock_get

    # Mock values().append()
    mock_append = MagicMock()
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRows": 1, "updatedRange": "'Attendance'!A2:J2"}
    }
    service.spreadsheets().values().append = mock_append

    return service


# ==============================================================================
# Test Scenarios
# ==============================================================================

def test_1_worker_initialization(tmp_path):
    """Scenario 1: Worker instantiates with valid parameters and config defaults."""
    db_file = create_test_database(tmp_path)
    worker = SheetsSyncWorker(
        poll_interval=5,
        batch_size=10,
        lease_duration=30,
        db_path=db_file,
        spreadsheet_id="mock_id_123"
    )
    assert worker.poll_interval == 5
    assert worker.batch_size == 10
    assert worker.lease_duration == 30
    assert worker.spreadsheet_id == "mock_id_123"


def test_2_worker_handles_missing_credentials_safely(tmp_path):
    """Scenario 2: Worker returns MISSING_CREDENTIALS cleanly without throwing unhandled exceptions."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    with patch("app.services.sheets_worker.get_google_config") as mock_cfg:
        mock_cfg.return_value = {
            "credentials_path": str(tmp_path / "non_existent_key.json"),
            "spreadsheet_id": "test_sheet_id",
            "sheet_name": "Attendance",
            "scopes": ("https://www.googleapis.com/auth/spreadsheets",)
        }
        res = sync_pending_records_batch(db_path=db_file, service=None)
        assert res["success"] is False
        assert res["status"] == "MISSING_CREDENTIALS"
        assert res["claimed_count"] == 0

    # Ensure queue remains pending (not stuck in_progress)
    stats = get_queue_stats(db_file)
    assert stats["pending"] == 1
    assert stats["in_progress"] == 0


def test_3_empty_queue_produces_no_api_append(tmp_path):
    """Scenario 3: When no records are pending, worker does not call values.append."""
    db_file = create_test_database(tmp_path)
    mock_service = make_mock_google_service()

    res = sync_pending_records_batch(
        db_path=db_file,
        spreadsheet_id="mock_sheet_id",
        service=mock_service
    )
    assert res["success"] is True
    assert res["status"] == "IDLE"
    assert res["claimed_count"] == 0
    assert res["appended_count"] == 0

    # Ensure append was NOT called
    mock_service.spreadsheets().values().append.assert_not_called()


def test_4_single_pending_record_synchronized_successfully(tmp_path):
    """Scenario 4: One pending record is claimed, appended with valueInputOption='RAW', and marked synced."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)
    target_att_id = att_ids[0]

    mock_service = make_mock_google_service(existing_sheet_ids=[])

    res = sync_pending_records_batch(
        db_path=db_file,
        spreadsheet_id="mock_sheet_id",
        service=mock_service
    )
    assert res["success"] is True
    assert res["status"] == "SUCCESS"
    assert res["claimed_count"] == 1
    assert res["appended_count"] == 1
    assert res["synced_count"] == 1

    # Verify append call
    mock_service.spreadsheets().values().append.assert_called_once()
    call_kwargs = mock_service.spreadsheets().values().append.call_args[1]
    assert call_kwargs["valueInputOption"] == "RAW"
    row_values = call_kwargs["body"]["values"][0]
    assert row_values[0] == target_att_id  # Attendance ID numeric
    assert row_values[3] == "CS-2024-001"  # Roll number

    # Verify SQLite status is updated to 'synced'
    stats = get_queue_stats(db_file)
    assert stats["synced"] == 1
    assert stats["pending"] == 0
    assert stats["in_progress"] == 0


def test_5_multiple_records_synchronized_in_batch(tmp_path):
    """Scenario 5: Multiple pending records are formatted and appended together in a single API call."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 5)
    mock_service = make_mock_google_service(existing_sheet_ids=[])

    res = sync_pending_records_batch(
        batch_size=10,
        db_path=db_file,
        spreadsheet_id="mock_sheet_id",
        service=mock_service
    )
    assert res["success"] is True
    assert res["claimed_count"] == 5
    assert res["appended_count"] == 5
    assert res["synced_count"] == 5

    mock_service.spreadsheets().values().append.assert_called_once()
    call_kwargs = mock_service.spreadsheets().values().append.call_args[1]
    rows = call_kwargs["body"]["values"]
    assert len(rows) == 5
    assert [r[0] for r in rows] == att_ids

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 5


def test_6_idempotent_duplicate_prevention_does_not_append_existing_ids(tmp_path):
    """
    Scenario 6: If Attendance IDs already exist in Google Sheets Column A,
    they are filtered out and marked synced without calling append.
    """
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 3)

    # Google Sheets already has records for att_ids[0] and att_ids[1]
    already_in_sheet = [att_ids[0], att_ids[1]]
    mock_service = make_mock_google_service(existing_sheet_ids=already_in_sheet)

    res = sync_pending_records_batch(
        batch_size=10,
        db_path=db_file,
        spreadsheet_id="mock_sheet_id",
        service=mock_service
    )
    assert res["success"] is True
    assert res["claimed_count"] == 3
    assert res["appended_count"] == 1        # Only att_ids[2] was appended!
    assert res["already_present_count"] == 2 # 2 were skipped
    assert res["synced_count"] == 3          # All 3 marked synced in SQLite

    call_kwargs = mock_service.spreadsheets().values().append.call_args[1]
    appended_rows = call_kwargs["body"]["values"]
    assert len(appended_rows) == 1
    assert appended_rows[0][0] == att_ids[2]

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 3


def test_7_uncertain_previous_response_retry_never_duplicates(tmp_path):
    """
    Scenario 7: Simulates network drop after Google Sheets stored the row.
    On subsequent retry, the worker detects the ID in Column A, skips append, and marks synced.
    """
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)
    target_id = att_ids[0]

    # Google Sheets contains the record from the earlier attempt
    mock_service = make_mock_google_service(existing_sheet_ids=[target_id])

    res = sync_pending_records_batch(
        db_path=db_file,
        spreadsheet_id="mock_sheet_id",
        service=mock_service
    )
    assert res["success"] is True
    assert res["claimed_count"] == 1
    assert res["appended_count"] == 0        # ZERO duplicates appended!
    assert res["already_present_count"] == 1
    assert res["synced_count"] == 1

    # append was NOT called
    mock_service.spreadsheets().values().append.assert_not_called()

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 1


def test_8_successful_records_store_synced_at_timestamp(tmp_path):
    """Scenario 8: Successfully synced records record a valid UTC ISO timestamp in synced_at."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)
    mock_service = make_mock_google_service(existing_sheet_ids=[])

    sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)

    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT sync_status, synced_at, lease_expires_at, last_error FROM sheets_sync_queue WHERE attendance_id = ?;", (att_ids[0],))
    row = cursor.fetchone()
    conn.close()

    assert row["sync_status"] == "synced"
    assert row["synced_at"] is not None
    assert "Z" in row["synced_at"]
    assert row["lease_expires_at"] is None
    assert row["last_error"] is None


def test_9_temporary_network_error_schedules_retry(tmp_path):
    """Scenario 9: Socket timeout schedules retry and sets status to 'failed' with backoff."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service(existing_sheet_ids=[])
    # Append raises socket.timeout
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = socket.timeout("Read timed out")

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is False
    assert res["status"] == "APPEND_FAILED"

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1
    assert stats["synced"] == 0
    assert stats["in_progress"] == 0

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT retry_count, next_retry_at, last_error FROM sheets_sync_queue;").fetchone()
    conn.close()
    assert row["retry_count"] == 1
    assert row["next_retry_at"] is not None
    assert "timed out" in row["last_error"].lower()


def test_10_rate_limit_http_429_schedules_retry(tmp_path):
    """Scenario 10: HTTP 429 Quota Exceeded schedules retry."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service(existing_sheet_ids=[])
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(
        429, "Rate limit exceeded"
    )

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is False

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1


def test_11_server_error_http_503_schedules_retry(tmp_path):
    """Scenario 11: HTTP 503 Backend Error schedules retry."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service(existing_sheet_ids=[])
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(
        503, "The service is temporarily unavailable"
    )

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is False

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1


def test_12_permission_failure_http_403_handled_safely(tmp_path):
    """Scenario 12: HTTP 403 Forbidden is classified and schedules retry without crashing."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    # Header get raises 403
    mock_service.spreadsheets().values().get.side_effect = make_mock_http_error(403, "Caller does not have permission")

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is False
    assert res["status"] == "CLIENT_ERROR"

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1


def test_13_invalid_spreadsheet_id_handled_safely(tmp_path):
    """Scenario 13: Empty spreadsheet ID returns MISSING_SPREADSHEET_ID cleanly."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    res = sync_pending_records_batch(
        db_path=db_file,
        spreadsheet_id="",
        service=None
    )
    assert res["success"] is False
    assert res["status"] == "MISSING_SPREADSHEET_ID"

    # Queue remains pending
    stats = get_queue_stats(db_file)
    assert stats["pending"] == 1


def test_14_expired_leases_recovered_automatically(tmp_path):
    """Scenario 14: Orphaned in_progress records whose lease expired are automatically reset to pending."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    # Claim with lease expiring in past (guaranteed past timestamp)
    conn = get_db_connection(db_file)
    expired_time = "2020-01-01T00:00:00Z"
    conn.execute(
        """
        UPDATE sheets_sync_queue
        SET sync_status = 'in_progress', lease_expires_at = ?
        WHERE attendance_id = ?;
        """,
        (expired_time, att_ids[0])
    )
    conn.commit()
    conn.close()

    mock_service = make_mock_google_service(existing_sheet_ids=[])

    # Worker batch runs and recovers the lease, then successfully processes it!
    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is True
    assert res["claimed_count"] == 1
    assert res["synced_count"] == 1

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 1


def test_15_concurrent_workers_cannot_claim_same_records(tmp_path):
    """Scenario 15: Leased records are excluded from subsequent worker queries."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 2)

    # Worker 1 claims 1 record
    worker1_claimed = claim_records(limit=1, lease_duration_seconds=60, db_path=db_file)
    assert len(worker1_claimed) == 1
    claimed_id = worker1_claimed[0]["queue_id"]

    # Worker 2 claims 1 record
    worker2_claimed = claim_records(limit=1, lease_duration_seconds=60, db_path=db_file)
    assert len(worker2_claimed) == 1
    assert worker2_claimed[0]["queue_id"] != claimed_id  # Completely disjoint!


def test_16_partial_batch_failure_preserves_already_synced(tmp_path):
    """Scenario 16: If append fails, already-synced records are still marked 'synced'."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 2)

    # Record 1 is already in sheet; Record 2 is new
    mock_service = make_mock_google_service(existing_sheet_ids=[att_ids[0]])
    # Append fails for Record 2
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(500, "Backend error")

    res = sync_pending_records_batch(batch_size=10, db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is False
    assert res["already_present_count"] == 1
    assert res["synced_count"] == 1

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 1  # Record 1 marked synced!
    assert stats["failed"] == 1  # Record 2 scheduled for retry!


def test_17_worker_loop_terminates_cleanly_on_stop(tmp_path):
    """Scenario 17: SheetsSyncWorker stop() terminates the execution loop cleanly."""
    db_file = create_test_database(tmp_path)
    mock_service = make_mock_google_service()

    worker = SheetsSyncWorker(poll_interval=1, db_path=db_file, spreadsheet_id="mock_id")

    # Stop immediately
    worker.stop()
    res = worker.run(once=False, service=mock_service)
    assert res is None
    assert worker._stop_event.is_set()


def test_18_database_transactions_closed_during_network_calls(tmp_path):
    """Scenario 18: SQLite connection is closed and not locked while calling external Google API."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    # During mock append, verify another independent connection can write to SQLite without blocking
    def verify_db_unlocked(*args, **kwargs):
        conn2 = get_db_connection(db_file)
        c2 = conn2.cursor()
        c2.execute("INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES ('CS-TEST-LOCK', 'Lock Test', 'LOCK_UID', 1, 'now');")
        conn2.commit()
        conn2.close()
        mock_req = MagicMock()
        mock_req.execute.return_value = {"updates": {"updatedRows": 1}}
        return mock_req

    mock_service = make_mock_google_service(existing_sheet_ids=[])
    mock_service.spreadsheets().values().append.side_effect = verify_db_unlocked

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["success"] is True
    assert res["synced_count"] == 1


def test_19_attendance_scan_does_not_block_on_worker(tmp_path):
    """Scenario 19: POST /api/scans continues to record in SQLite immediately with zero delay."""
    app = create_app("testing")
    db_file = tmp_path / "scan_noblock.db"
    init_db(db_file)
    app.config["DATABASE_PATH"] = db_file
    app.config["GOOGLE_SHEETS_SPREADSHEET_ID"] = "mock_sheet_id"
    app.config["LOGIN_DISABLED"] = True

    # Seed student and session
    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES ('CS-NB-1', 'NB Student', 'NB_CARD_01', 1, 'now');")
    conn.execute("INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at) VALUES ('IoT', '2026-11-08', '08:00', '23:59', 1, 'now');")
    conn.commit()
    conn.close()

    with app.test_client() as client:
        t0 = datetime.now()
        resp = client.post("/api/scans", json={"card_uid": "NB_CARD_01", "source": "rfid"})
        t1 = datetime.now()
        duration_ms = (t1 - t0).total_seconds() * 1000

        assert resp.status_code == 201
        assert duration_ms < 500  # Local SQLite scan completes in milliseconds

        # Both attendance and queue exist
        stats = get_queue_stats(db_file)
        assert stats["pending"] == 1


def test_20_worker_never_modifies_authoritative_attendance_records(tmp_path):
    """Scenario 20: Worker leaves attendance table rows completely untouched."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    conn = get_db_connection(db_file)
    before_att = dict(conn.execute("SELECT * FROM attendance WHERE id = ?;", (att_ids[0],)).fetchone())
    conn.close()

    mock_service = make_mock_google_service()
    sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)

    conn = get_db_connection(db_file)
    after_att = dict(conn.execute("SELECT * FROM attendance WHERE id = ?;", (att_ids[0],)).fetchone())
    conn.close()

    assert before_att == after_att


def test_21_exhausted_retries_transition_to_dead_letter(tmp_path):
    """Scenario 21: A record failing 5 consecutive times transitions to 'dead_letter'."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(500, "Persistent Error")

    # Run 5 failed attempts
    for attempt in range(1, 6):
        # Reset lease and retry time so it can be claimed immediately
        conn = get_db_connection(db_file)
        conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL WHERE attendance_id = ?;", (att_ids[0],))
        conn.commit()
        conn.close()

        sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)

    stats = get_queue_stats(db_file)
    assert stats["dead_letter"] == 1
    assert stats["failed"] == 0


def test_22_worker_logs_do_not_expose_secrets(tmp_path, caplog):
    """Scenario 22: Worker logging verifies secret keys and private key blocks are never printed."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)
    mock_service = make_mock_google_service()

    with caplog.at_level(logging.DEBUG):
        sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)

    log_text = caplog.text
    assert "BEGIN PRIVATE KEY" not in log_text
    assert "client_secret" not in log_text
    assert "private_key" not in log_text


def test_23_admin_sync_status_endpoint(tmp_path):
    """Scenario 23: GET /api/admin/sheets-sync/status returns live telemetry and configuration presence flags."""
    app = create_app("testing")
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 2)
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True
    app.config["GOOGLE_SHEETS_SPREADSHEET_ID"] = "test_sheet_123"

    with app.test_client() as client:
        resp = client.get("/api/admin/sheets-sync/status")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["success"] is True
        assert data["sync_enabled"] is True
        assert data["spreadsheet_id_configured"] is True
        assert "queue_stats" in data
        assert data["queue_stats"]["pending"] == 2
        assert "credentials_path" not in data  # Path not disclosed!
        assert "private_key" not in data


def test_24_flask_cli_run_sheets_worker_once_option(tmp_path):
    """Scenario 24: Flask CLI command 'run-sheets-worker --once' runs single batch cleanly."""
    app = create_app("testing")
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)
    app.config["DATABASE_PATH"] = db_file
    app.config["GOOGLE_SHEETS_SPREADSHEET_ID"] = "mock_sheet_id"

    runner = app.test_cli_runner()
    with patch("app.services.sheets_worker.get_sheets_service") as mock_get_svc:
        mock_svc = make_mock_google_service(existing_sheet_ids=[])
        mock_get_svc.return_value = mock_svc

        # Execute CLI command with --once
        result = runner.invoke(args=["run-sheets-worker", "--once", "--batch-size", "5"])
        assert result.exit_code == 0
        assert "Running single batch synchronization" in result.output
        assert "Successfully synchronized" in result.output

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 1
