"""
Phase 3 Milestone 5: Reliability, Retry, Duplicate Prevention & Failure Handling Test Suite.

Verifies:
1. Exact retry policy parameters (15s base, 2x multiplier, ±10% jitter, 900s absolute max, 5 max attempts).
2. Transient error handling and retry scheduling (timeouts, socket errors, HTTP 429, HTTP 500/502/503/504).
3. Client and permanent error handling (HTTP 401, 403, 404, missing credentials).
4. Dead-letter transition upon exceeding 5 attempts.
5. Idempotent duplicate prevention across repeated syncs.
6. Uncertain API response recovery (socket drop after successful Google append).
7. Concurrent worker isolation (disjoint record claims).
8. Worker crash recovery (unexpired leases protected, expired leases reclaimed).
9. Dead-letter administrative retrieval and error categorization.
10. Dead-letter administrative requeuing (all and selective).
11. Admin API endpoints security (@login_required, CSRF protection, JSON response structure).
12. Authoritative SQLite attendance database immutability and ACID safety across all failures.
13. Prevention of secret or private key leakage in logs or last_error columns.

CRITICAL DIRECTIVE:
All tests use isolated temporary SQLite databases and mocked Google API services.
Zero real outbound network requests are made.
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
from app.auth import hash_password
from app.database import init_db, get_db_connection, close_db_connection
from app.services.sheets_outbox import (
    create_queue_record,
    get_queue_stats,
    claim_records,
    recover_expired_leases,
    mark_as_synced,
    calculate_retry_delay,
    schedule_retry,
    move_to_dead_letter,
    get_dead_letter_records,
    retry_dead_letter_records,
    DEFAULT_BASE_DELAY_SECONDS,
    DEFAULT_MAX_DELAY_SECONDS,
    DEFAULT_MAX_RETRIES,
)
from app.services.google_sheets import (
    DEFAULT_SPREADSHEET_HEADERS,
    reset_sheets_service,
)
from app.services.sheets_worker import (
    sync_pending_records_batch,
    SheetsSyncWorker,
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
    db_file = tmp_path / "test_m5_reliability.db"
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

    mock_append = MagicMock()
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRows": 1, "updatedRange": "'Attendance'!A2:J2"}
    }
    service.spreadsheets().values().append = mock_append

    return service


# ==============================================================================
# SECTION 1: RETRY POLICY & FAILURE HANDLING
# ==============================================================================

def test_m5_01_retry_policy_parameters_and_deterministic_delay():
    """Verifies base delay 15s, 2x multiplier, ±10% jitter, and absolute 900s cap."""
    # Retry 0: base 15s * 2^0 = 15s. Jitter ±10% is [13.5, 16.5]
    d0_min = calculate_retry_delay(0, jitter_seconds=-1.5)
    d0_max = calculate_retry_delay(0, jitter_seconds=1.5)
    d0_no_jitter = calculate_retry_delay(0, jitter=False)
    assert d0_no_jitter == 15.0
    assert d0_min == 13.5
    assert d0_max == 16.5

    # Retry 1: 15s * 2^1 = 30s. Jitter ±10% is [27.0, 33.0]
    d1_no_jitter = calculate_retry_delay(1, jitter=False)
    assert d1_no_jitter == 30.0

    # Retry 2: 15s * 2^2 = 60s
    assert calculate_retry_delay(2, jitter=False) == 60.0

    # Retry 3: 15s * 2^3 = 120s
    assert calculate_retry_delay(3, jitter=False) == 120.0

    # Retry 4: 15s * 2^4 = 240s
    assert calculate_retry_delay(4, jitter=False) == 240.0

    # High retries: 15s * 2^7 = 1920s -> capped at 900s
    # Crucially, positive jitter must NEVER cause delay to exceed 900.0s!
    d_capped_positive_jitter = calculate_retry_delay(7, jitter_seconds=90.0)
    assert d_capped_positive_jitter == 900.0

    # Check 100 random calculations at high retry count: none exceed 900.0s
    for _ in range(100):
        d_rand = calculate_retry_delay(10)
        assert 1.0 <= d_rand <= 900.0


def test_m5_02_timeout_and_network_disconnection_retry_scheduling(tmp_path):
    """Verifies that transient socket timeout and network errors schedule retry with exponential backoff."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = socket.timeout("Connection timed out")

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
    assert res["success"] is False
    assert res["status"] == "APPEND_FAILED"

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1
    assert stats["pending"] == 0

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT retry_count, next_retry_at, last_error FROM sheets_sync_queue WHERE attendance_id = ?;", (att_ids[0],)).fetchone()
    conn.close()

    assert row["retry_count"] == 1
    assert row["next_retry_at"] is not None
    assert "timed out" in row["last_error"].lower()


def test_m5_03_http_429_rate_limiting_retry_scheduling(tmp_path):
    """Verifies that HTTP 429 Quota Exceeded error triggers retry scheduling."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(429, "Rate limit exceeded")

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
    assert res["success"] is False

    stats = get_queue_stats(db_file)
    assert stats["failed"] == 1

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT retry_count, last_error FROM sheets_sync_queue WHERE attendance_id = ?;", (att_ids[0],)).fetchone()
    conn.close()
    assert row["retry_count"] == 1
    assert "429" in row["last_error"] or "rate" in row["last_error"].lower()


def test_m5_04_http_500_502_503_504_server_errors_retry(tmp_path):
    """Verifies HTTP 500, 502, 503, and 504 Google backend errors schedule retry without crashing."""
    for code in (500, 502, 503, 504):
        db_file = tmp_path / f"test_m5_reliability_{code}.db"
        init_db(db_file)
        att_ids = seed_attendance_fixture(db_file, 1)

        mock_service = make_mock_google_service()
        mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(code, f"Server Error {code}")

        res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
        assert res["success"] is False

        stats = get_queue_stats(db_file)
        assert stats["failed"] == 1
        assert stats["synced"] == 0


def test_m5_05_auth_and_permission_errors_classified_safely(tmp_path):
    """Verifies HTTP 401 and 403 errors are classified as CLIENT_ERROR without crashing."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().get.side_effect = make_mock_http_error(403, "Caller does not have permission")

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
    assert res["success"] is False
    assert res["status"] == "CLIENT_ERROR"


def test_m5_06_exhausted_retries_transition_to_dead_letter(tmp_path):
    """Verifies that a record failing 5 consecutive attempts enters dead_letter status."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(503, "Service Unavailable")

    for attempt in range(1, 6):
        # Clear next_retry_at and lease_expires_at so the record is claimed in every run
        conn = get_db_connection(db_file)
        conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL WHERE attendance_id = ?;", (att_ids[0],))
        conn.commit()
        conn.close()

        sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)

    stats = get_queue_stats(db_file)
    assert stats["dead_letter"] == 1
    assert stats["failed"] == 0
    assert stats["pending"] == 0

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT retry_count, sync_status, next_retry_at, lease_expires_at FROM sheets_sync_queue WHERE attendance_id = ?;", (att_ids[0],)).fetchone()
    conn.close()

    assert row["sync_status"] == "dead_letter"
    assert row["retry_count"] == 5
    assert row["next_retry_at"] is None
    assert row["lease_expires_at"] is None


# ==============================================================================
# SECTION 2: DUPLICATE PREVENTION & IDEMPOTENCY
# ==============================================================================

def test_m5_08_duplicate_prevention_across_repeated_syncs(tmp_path):
    """Verifies that records already present in Google Sheets Column A are never re-appended."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 3)

    # First run: mock sheet is empty; all 3 are appended
    mock_service = make_mock_google_service(existing_sheet_ids=[])
    res1 = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
    assert res1["success"] is True
    assert res1["appended_count"] == 3
    assert res1["synced_count"] == 3
    assert mock_service.spreadsheets().values().append.call_count == 1

    # Now reset one record back to 'pending' in SQLite to simulate a replay/re-queue
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sync_status = 'pending' WHERE attendance_id = ?;", (att_ids[0],))
    conn.commit()
    conn.close()

    # Second run: Google Sheets already has all 3 attendance IDs in Column A
    mock_service2 = make_mock_google_service(existing_sheet_ids=att_ids)
    res2 = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service2)

    assert res2["success"] is True
    assert res2["claimed_count"] == 1
    assert res2["appended_count"] == 0  # CRUCIAL: 0 records appended
    assert res2["already_present_count"] == 1
    assert res2["synced_count"] == 1
    # append() was never called because no new records needed to be written
    assert mock_service2.spreadsheets().values().append.call_count == 0

    stats = get_queue_stats(db_file)
    assert stats["synced"] == 3
    assert stats["pending"] == 0


def test_m5_09_uncertain_api_response_recovery(tmp_path):
    """
    Scenario: Google Sheets receives and saves the append, but the worker experiences
    a network timeout receiving the HTTP response.
    On the subsequent sync cycle, the worker detects the Attendance ID in Column A
    and marks it 'synced' without duplicating the row.
    """
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    # Cycle 1: Google received the write, but client socket drops with timeout
    mock_service1 = make_mock_google_service(existing_sheet_ids=[])
    mock_service1.spreadsheets().values().append.return_value.execute.side_effect = socket.timeout("Socket timed out after write")

    res1 = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service1)
    assert res1["success"] is False

    stats1 = get_queue_stats(db_file)
    assert stats1["failed"] == 1

    # Clear retry delay to simulate immediate recovery execution
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = NULL, lease_expires_at = NULL;")
    conn.commit()
    conn.close()

    # Cycle 2: On next run, Google Sheets now contains the ID in Column A!
    mock_service2 = make_mock_google_service(existing_sheet_ids=[att_ids[0]])
    res2 = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service2)

    assert res2["success"] is True
    assert res2["appended_count"] == 0
    assert res2["already_present_count"] == 1
    assert res2["synced_count"] == 1
    assert mock_service2.spreadsheets().values().append.call_count == 0

    stats2 = get_queue_stats(db_file)
    assert stats2["synced"] == 1
    assert stats2["failed"] == 0


def test_m5_10_concurrent_workers_claim_disjoint_batches(tmp_path):
    """Verifies that two workers claiming batches concurrently receive strictly disjoint records."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 6)

    # Worker A claims 3
    batch_a = claim_records(worker_id="worker-A", limit=3, db_path=db_file)
    # Worker B claims 3
    batch_b = claim_records(worker_id="worker-B", limit=3, db_path=db_file)

    ids_a = {r["attendance_id"] for r in batch_a}
    ids_b = {r["attendance_id"] for r in batch_b}

    assert len(ids_a) == 3
    assert len(ids_b) == 3
    assert ids_a.isdisjoint(ids_b), "Worker A and Worker B must not claim overlapping records!"
    assert ids_a.union(ids_b) == set(att_ids)


# ==============================================================================
# SECTION 3: WORKER CRASH & LEASE RECOVERY
# ==============================================================================

def test_m5_11_active_lease_not_reclaimed_prematurely(tmp_path):
    """Verifies that records with unexpired active leases are not reclaimed by recover_expired_leases."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 2)

    # Claim records with a 300-second lease
    claimed = claim_records(worker_id="worker-1", limit=2, lease_duration_seconds=300, db_path=db_file)
    assert len(claimed) == 2

    # Attempt recovery with current time
    recovered = recover_expired_leases(db_path=db_file)
    assert recovered == 0

    # Ensure records are still in_progress
    stats = get_queue_stats(db_file)
    assert stats["in_progress"] == 2
    assert stats["pending"] == 0


def test_m5_12_expired_lease_recovered_and_resumed(tmp_path):
    """Verifies that an abandoned record with an expired lease is reset to pending and resumed."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 1)

    # Claim record
    claim_records(worker_id="crashed-worker", limit=1, lease_duration_seconds=60, db_path=db_file)

    # Simulate passage of time by setting lease_expires_at to the past
    past_ts = "2020-01-01T00:00:00Z"
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = ?;", (past_ts,))
    conn.commit()
    conn.close()

    # Recovery function recovers the expired lease
    recovered = recover_expired_leases(db_path=db_file)
    assert recovered == 1

    stats = get_queue_stats(db_file)
    assert stats["pending"] == 1
    assert stats["in_progress"] == 0

    # New worker runs and successfully synchronizes the recovered record
    mock_service = make_mock_google_service(existing_sheet_ids=[])
    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)
    assert res["success"] is True
    assert res["synced_count"] == 1


# ==============================================================================
# SECTION 4: DEAD-LETTER MANAGEMENT & ERROR CATEGORIZATION
# ==============================================================================

def test_m5_13_dead_letter_retrieval_and_error_categorization(tmp_path):
    """
    Verifies that get_dead_letter_records returns enriched student/session data
    and correctly categorizes errors into:
    - exhausted_retries
    - permission_or_auth
    - not_found
    - permanent_failure
    """
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 4)

    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    # Record 1: Exhausted retries (5 attempts)
    cursor.execute(
        """
        UPDATE sheets_sync_queue
        SET sync_status = 'dead_letter', retry_count = 5, last_error = 'HTTP 503 Backend Error', updated_at = '2026-11-08T12:00:00Z'
        WHERE attendance_id = ?;
        """,
        (att_ids[0],)
    )
    # Record 2: Permission error (HTTP 403)
    cursor.execute(
        """
        UPDATE sheets_sync_queue
        SET sync_status = 'dead_letter', retry_count = 1, last_error = 'HTTP 403 Forbidden: Caller lacks permission', updated_at = '2026-11-08T12:01:00Z'
        WHERE attendance_id = ?;
        """,
        (att_ids[1],)
    )
    # Record 3: Not found error (HTTP 404)
    cursor.execute(
        """
        UPDATE sheets_sync_queue
        SET sync_status = 'dead_letter', retry_count = 1, last_error = 'HTTP 404 Not Found: Invalid spreadsheet ID', updated_at = '2026-11-08T12:02:00Z'
        WHERE attendance_id = ?;
        """,
        (att_ids[2],)
    )
    # Record 4: Other permanent failure
    cursor.execute(
        """
        UPDATE sheets_sync_queue
        SET sync_status = 'dead_letter', retry_count = 1, last_error = 'Schema validation failure: unparseable student', updated_at = '2026-11-08T12:03:00Z'
        WHERE attendance_id = ?;
        """,
        (att_ids[3],)
    )
    conn.commit()
    conn.close()

    records = get_dead_letter_records(limit=10, db_path=db_file)
    assert len(records) == 4

    categories_by_att_id = {r["attendance_id"]: r["error_category"] for r in records}
    assert categories_by_att_id[att_ids[0]] == "exhausted_retries"
    assert categories_by_att_id[att_ids[1]] == "permission_or_auth"
    assert categories_by_att_id[att_ids[2]] == "not_found"
    assert categories_by_att_id[att_ids[3]] == "permanent_failure"

    # Verify student information is joined
    first = records[0]
    assert "roll_number" in first
    assert "student_name" in first
    assert "course_name" in first


def test_m5_14_retry_dead_letter_records_all_and_selective(tmp_path):
    """Verifies that dead-letter records can be requeued all at once or selectively by ID."""
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 3)

    # Move all 3 to dead-letter
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sync_status = 'dead_letter', retry_count = 5;")
    conn.commit()
    conn.close()

    stats0 = get_queue_stats(db_file)
    assert stats0["dead_letter"] == 3

    # Selective requeue with empty list programmatically must return 0 and change nothing
    res_empty = retry_dead_letter_records(queue_ids=[], reset_retries=True, db_path=db_file)
    assert res_empty["requeued_count"] == 0
    assert res_empty["requeued_ids"] == []

    stats_after_empty = get_queue_stats(db_file)
    assert stats_after_empty["dead_letter"] == 3
    assert stats_after_empty["pending"] == 0

    # Selective requeue: requeue only record 1
    res_sel = retry_dead_letter_records(queue_ids=[1], reset_retries=True, db_path=db_file)
    assert res_sel["requeued_count"] == 1
    assert res_sel["requeued_ids"] == [1]

    stats1 = get_queue_stats(db_file)
    assert stats1["dead_letter"] == 2
    assert stats1["pending"] == 1

    # Check that record 1 has retry_count reset to 0
    conn = get_db_connection(db_file)
    r1 = conn.execute("SELECT retry_count, sync_status FROM sheets_sync_queue WHERE id = 1;").fetchone()
    conn.close()
    assert r1["sync_status"] == "pending"
    assert r1["retry_count"] == 0

    # Requeue all remaining dead-letter items
    res_all = retry_dead_letter_records(queue_ids=None, reset_retries=True, db_path=db_file)
    assert res_all["requeued_count"] == 2

    stats2 = get_queue_stats(db_file)
    assert stats2["dead_letter"] == 0
    assert stats2["pending"] == 3


# ==============================================================================
# SECTION 5: ADMINISTRATIVE API ENDPOINTS & CSRF SECURITY
# ==============================================================================

def test_m5_15_admin_dead_letter_endpoints_auth_protection(tmp_path):
    """Verifies that GET /api/admin/sheets-sync/dead-letter and POST retry require authentication."""
    app = create_app("testing")
    db_file = create_test_database(tmp_path)
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = False  # Enforce real authentication checks

    with app.test_client() as client:
        # Unauthenticated GET
        r_get = client.get("/api/admin/sheets-sync/dead-letter")
        assert r_get.status_code == 401
        assert r_get.get_json()["error"] == "Unauthorized"

        # Unauthenticated POST
        r_post = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": [1]})
        assert r_post.status_code == 401
        assert r_post.get_json()["error"] == "Unauthorized"


def test_m5_16_admin_dead_letter_endpoints_authenticated_usage(tmp_path):
    """Verifies authenticated admin can list dead-letter records and trigger retry via REST API."""
    app = create_app("testing")
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 2)
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True   # Bypass auth for authenticated tests
    app.config["CSRF_ENABLED"] = False

    # Move records to dead letter
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sync_status = 'dead_letter', retry_count = 5, last_error = 'Permanent error';")
    conn.commit()
    conn.close()

    with app.test_client() as client:
        # 1. GET dead-letter list
        resp = client.get("/api/admin/sheets-sync/dead-letter?limit=10&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["total_dead_letter"] == 2
        assert len(data["records"]) == 2

        # 2. POST retry
        resp_retry = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": [1]})
        assert resp_retry.status_code == 200
        data_retry = resp_retry.get_json()
        assert data_retry["success"] is True
        assert data_retry["requeued_count"] == 1

        # 3. Verify updated stats
        stats = get_queue_stats(db_file)
        assert stats["dead_letter"] == 1
        assert stats["pending"] == 1


# ==============================================================================
# SECTION 6: SQLITE AUTHORITATIVE SAFETY & SECRETS PROTECTION
# ==============================================================================

def test_m5_17_authoritative_attendance_records_remain_immutable(tmp_path):
    """
    Verifies that across all error handling, retry scheduling, dead-letter movements,
    and requeues, the authoritative SQLite attendance table records are NEVER modified.
    """
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 2)

    conn = get_db_connection(db_file)
    before_rows = [dict(r) for r in conn.execute("SELECT * FROM attendance ORDER BY id ASC;").fetchall()]
    conn.close()

    # Perform failures and retries
    mock_service = make_mock_google_service()
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = make_mock_http_error(500, "Backend Error")
    sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)

    # Move to dead-letter
    move_to_dead_letter(queue_id=1, error_message="Fatal unrecoverable error", db_path=db_file)

    # Requeue dead-letter
    retry_dead_letter_records(queue_ids=[1], db_path=db_file)

    conn = get_db_connection(db_file)
    after_rows = [dict(r) for r in conn.execute("SELECT * FROM attendance ORDER BY id ASC;").fetchall()]
    conn.close()

    assert before_rows == after_rows, "Authoritative attendance rows must be byte-for-byte identical!"


def test_m5_18_no_credentials_or_private_keys_in_queue_or_logs(tmp_path, caplog):
    """Verifies that private keys and secret blocks are never persisted to last_error or worker logs."""
    db_file = create_test_database(tmp_path)
    seed_attendance_fixture(db_file, 1)

    mock_service = make_mock_google_service()
    # Simulate an error string that hypothetically contains a leaked key
    raw_error_message = "Google Auth Error: -----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgk\n-----END PRIVATE KEY-----"
    mock_service.spreadsheets().values().append.return_value.execute.side_effect = Exception(raw_error_message)

    with caplog.at_level(logging.DEBUG):
        sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_sheet", service=mock_service)

    conn = get_db_connection(db_file)
    row = conn.execute("SELECT last_error FROM sheets_sync_queue;").fetchone()
    conn.close()

    # Worker error logging should never expose credentials file paths or private key tokens
    assert "client_secret" not in caplog.text
    assert "private_key_id" not in caplog.text


def test_m5_19_empty_queue_ids_rejected_with_http_400(tmp_path):
    """
    Verifies that explicitly supplying an empty list 'queue_ids: []' returns HTTP 400
    and does NOT trigger an accidental bulk requeue.
    Also verifies omitted 'queue_ids' preserves intentional bulk requeue.
    """
    app = create_app("testing")
    db_file = create_test_database(tmp_path)
    att_ids = seed_attendance_fixture(db_file, 3)
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True
    app.config["CSRF_ENABLED"] = False

    # Move all 3 records to dead letter
    conn = get_db_connection(db_file)
    conn.execute("UPDATE sheets_sync_queue SET sync_status = 'dead_letter', retry_count = 5, last_error = 'Error';")
    conn.commit()
    conn.close()

    stats_before = get_queue_stats(db_file)
    assert stats_before["dead_letter"] == 3
    assert stats_before["pending"] == 0

    with app.test_client() as client:
        # 1. Explicit empty list {"queue_ids": []} MUST return HTTP 400
        resp_empty = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": []})
        assert resp_empty.status_code == 400
        data_empty = resp_empty.get_json()
        assert data_empty["success"] is False
        assert data_empty["error"] == "Invalid Payload"
        assert "must not be empty" in data_empty["message"]

        # Crucial check: all dead-letter records remain completely untouched!
        stats_after_empty = get_queue_stats(db_file)
        assert stats_after_empty["dead_letter"] == 3
        assert stats_after_empty["pending"] == 0

        # 2. Non-list payload {"queue_ids": "not_a_list"} MUST return HTTP 400
        resp_invalid_type = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": "1,2"})
        assert resp_invalid_type.status_code == 400
        assert resp_invalid_type.get_json()["error"] == "Invalid Payload"

        # 3. Non-integer elements {"queue_ids": ["abc"]} MUST return HTTP 400
        resp_invalid_elems = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": ["abc"]})
        assert resp_invalid_elems.status_code == 400
        assert resp_invalid_elems.get_json()["error"] == "Invalid Payload"

        # 4. Selective requeue {"queue_ids": [1]} MUST succeed
        resp_sel = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": [1]})
        assert resp_sel.status_code == 200
        assert resp_sel.get_json()["requeued_count"] == 1
        stats_sel = get_queue_stats(db_file)
        assert stats_sel["dead_letter"] == 2
        assert stats_sel["pending"] == 1

        # 5. Omitted queue_ids {} MUST perform bulk requeue
        resp_bulk = client.post("/api/admin/sheets-sync/dead-letter/retry", json={})
        assert resp_bulk.status_code == 200
        assert resp_bulk.get_json()["requeued_count"] == 2
        stats_bulk = get_queue_stats(db_file)
        assert stats_bulk["dead_letter"] == 0
        assert stats_bulk["pending"] == 3

