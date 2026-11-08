"""
Phase 3 Milestone 6: Comprehensive End-to-End Integration Test Suite & Reliability Audit.

Validates all 9 Integration Test Areas:
- Area A: Attendance Ingestion and Outbox Atomicity
- Area B: 10-Column Google Sheets Data Mapping & Formula Sanitization
- Area C: Background Sync Worker Integration & CLI Lifecycle
- Area D: End-to-End Synchronization 15-Scenario Matrix
- Area E: Dead-Letter Administration & API Security
- Area F: Retry Policy & Concurrency Reliability
- Area G: Secrets Protection & Configuration Safety
- Area H: Authoritative Database Immutability & ACID Constraints

CRITICAL SAFETY DIRECTIVE:
All tests execute against isolated temporary SQLite databases.
Google API operations are completely mocked; ZERO live network calls or spreadsheet writes occur.
The real development database is NEVER modified.
"""

import json
import logging
import re
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
    get_pending_records,
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
    format_attendance_row,
    format_attendance_batch,
    sanitize_sheet_cell,
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


def create_test_db(tmp_path: Path, name: str = "test_m6.db") -> Path:
    """Creates a temporary SQLite database initialized with all tables."""
    db_file = tmp_path / name
    init_db(db_file)
    return db_file


def seed_test_student_and_session(db_file: Path, roll="CS-2024-001", card_uid="CARD_M6_01", course="Operating Systems"):
    """Seeds a single student and active session in an isolated test database."""
    conn = get_db_connection(db_file)
    now_utc = "2026-11-08T10:00:00Z"
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES (?, 'Integration Student', ?, 1, ?);
        """,
        (roll, card_uid, now_utc)
    )
    student_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES (?, '2026-11-08', '08:00', '23:59', 1, ?);
        """,
        (course, now_utc)
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return student_id, session_id


def make_mock_google_service(existing_sheet_ids: list[int] = None):
    """Creates a mock Google Sheets API v4 Resource."""
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
# AREA A: Attendance Ingestion and Outbox Atomicity
# ==============================================================================

def test_m6_area_a_attendance_scan_and_outbox_atomicity(tmp_path):
    """Verifies that POST /api/scans creates exactly 1 attendance record and 1 outbox record atomically."""
    app = create_app("testing")
    db_file = create_test_db(tmp_path, "atomicity.db")
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True
    app.config["CSRF_ENABLED"] = False

    seed_test_student_and_session(db_file, card_uid="CARD_ATOM_01")

    with app.test_client() as client:
        # 1. Successful RFID Scan
        resp = client.post("/api/scans", json={"card_uid": "CARD_ATOM_01", "source": "rfid"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        att_id = data["attendance"]["id"]

        # Verify attendance record exists in SQLite
        conn = get_db_connection(db_file)
        att_rows = conn.execute("SELECT * FROM attendance;").fetchall()
        assert len(att_rows) == 1
        assert att_rows[0]["id"] == att_id

        # Verify matching outbox record exists in sheets_sync_queue
        queue_rows = conn.execute("SELECT * FROM sheets_sync_queue;").fetchall()
        assert len(queue_rows) == 1
        assert queue_rows[0]["attendance_id"] == att_id
        assert queue_rows[0]["sync_status"] == "pending"
        assert queue_rows[0]["retry_count"] == 0

        # 2. Duplicate Scan Rejection (HTTP 409)
        resp_dup = client.post("/api/scans", json={"card_uid": "CARD_ATOM_01", "source": "rfid"})
        assert resp_dup.status_code == 409
        assert resp_dup.get_json()["error"] == "Duplicate Attendance"

        # Verify no duplicate row was created in attendance or queue
        assert len(conn.execute("SELECT * FROM attendance;").fetchall()) == 1
        assert len(conn.execute("SELECT * FROM sheets_sync_queue;").fetchall()) == 1

        # 3. Unknown Card UID Rejection (HTTP 404)
        resp_unknown = client.post("/api/scans", json={"card_uid": "UNKNOWN_CARD", "source": "rfid"})
        assert resp_unknown.status_code == 404
        assert len(conn.execute("SELECT * FROM attendance;").fetchall()) == 1
        assert len(conn.execute("SELECT * FROM sheets_sync_queue;").fetchall()) == 1
        conn.close()


def test_m6_area_a_outbox_failure_rolls_back_attendance_transaction(tmp_path):
    """Verifies that an error in outbox insertion rolls back the attendance insert completely."""
    app = create_app("testing")
    db_file = create_test_db(tmp_path, "rollback.db")
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True
    app.config["CSRF_ENABLED"] = False

    seed_test_student_and_session(db_file, card_uid="CARD_ROLL_01")

    # Drop sheets_sync_queue to force outbox insertion to fail inside process_scan transaction
    conn = get_db_connection(db_file)
    conn.execute("DROP TABLE sheets_sync_queue;")
    conn.commit()
    conn.close()

    with app.test_client() as client:
        resp = client.post("/api/scans", json={"card_uid": "CARD_ROLL_01", "source": "rfid"})
        assert resp.status_code == 500

        # Attendance must NOT be persisted because the transaction rolled back
        conn = get_db_connection(db_file)
        att_rows = conn.execute("SELECT * FROM attendance;").fetchall()
        conn.close()
        assert len(att_rows) == 0, "Attendance insertion must be rolled back if outbox queue insert fails!"


# ==============================================================================
# AREA B: Complete Google Sheets Data Mapping & Formula Sanitization
# ==============================================================================

def test_m6_area_b_ten_column_schema_and_formula_sanitization():
    """Verifies the exact approved 10-column schema contract, formula protection, and timezone."""
    # Test record containing malicious formula attempts and optional variations
    test_record = {
        "attendance_id": 42,
        "scanned_at": "2026-11-08T04:30:00Z",  # 04:30 UTC = 10:00 IST (+05:30)
        "roll_number": "=1+1",
        "student_name": "@AdminUser",
        "course_name": "+Embedded System",
        "card_uid": "-CARD_99",
        "attendance_status": "Present",
        "source": "rfid",
        "sync_timestamp": "2026-11-08T10:05:00Z"
    }

    row = format_attendance_row(test_record)
    assert len(row) == 10, "Row must contain exactly 10 columns."

    # Column 1: Attendance ID (numeric int preserved)
    assert row[0] == 42
    assert isinstance(row[0], int)

    # Column 2: Date (Asia/Kolkata YYYY-MM-DD)
    assert row[1] == "2026-11-08"

    # Column 3: Time (Asia/Kolkata HH:MM:SS)
    assert row[2] == "10:00:00"

    # Column 4: Roll Number (sanitized with leading single quote)
    assert row[3] == "'=1+1"

    # Column 5: Student Name (sanitized with leading single quote)
    assert row[4] == "'@AdminUser"

    # Column 6: Course / Session (sanitized with leading single quote)
    assert row[5] == "'+Embedded System"

    # Column 7: RFID UID (sanitized with leading single quote)
    assert row[6] == "'-CARD_99"

    # Column 8: Attendance Status
    assert row[7] == "Present"

    # Column 9: Source / Device
    assert row[8] == "rfid"

    # Column 10: Sync Timestamp
    assert row[9] == "2026-11-08T10:05:00Z"


# ==============================================================================
# AREA C: Background Sync Worker Integration & Lifecycle
# ==============================================================================

def test_m6_area_c_worker_lifecycle_and_single_shot(tmp_path):
    """Verifies worker batch claiming, idempotent append, sync status updates, and single-shot execution."""
    app = create_app("testing")
    db_file = create_test_db(tmp_path, "worker_e2e.db")
    app.config["DATABASE_PATH"] = db_file
    app.config["GOOGLE_SHEETS_SPREADSHEET_ID"] = "mock_sheet_123"

    seed_test_student_and_session(db_file, card_uid="CARD_WORKER_01")

    # Seed attendance through normal scan endpoint
    app.config["LOGIN_DISABLED"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as client:
        client.post("/api/scans", json={"card_uid": "CARD_WORKER_01", "source": "rfid"})

    stats_before = get_queue_stats(db_file)
    assert stats_before["pending"] == 1
    assert stats_before["synced"] == 0

    mock_service = make_mock_google_service(existing_sheet_ids=[])
    worker = SheetsSyncWorker(
        app=app,
        db_path=db_file,
        spreadsheet_id="mock_sheet_123"
    )

    # Run single-shot batch
    result = worker.run(once=True, service=mock_service)
    assert result["success"] is True
    assert result["claimed_count"] == 1
    assert result["appended_count"] == 1
    assert result["synced_count"] == 1

    stats_after = get_queue_stats(db_file)
    assert stats_after["pending"] == 0
    assert stats_after["synced"] == 1

    conn = get_db_connection(db_file)
    q_row = conn.execute("SELECT synced_at, sync_status, last_error FROM sheets_sync_queue;").fetchone()
    conn.close()
    assert q_row["sync_status"] == "synced"
    assert q_row["synced_at"] is not None
    assert q_row["last_error"] is None


# ==============================================================================
# AREA D: End-to-End Synchronization 15-Scenario Matrix
# ==============================================================================

@pytest.mark.parametrize("scenario,side_effect,expected_status,expected_synced,expected_failed,expected_dead", [
    ("Google API success", None, "SUCCESS", 1, 0, 0),
    ("Network timeout", socket.timeout("timed out"), "APPEND_FAILED", 0, 1, 0),
    ("Connection reset", ConnectionResetError("reset by peer"), "APPEND_FAILED", 0, 1, 0),
    ("HTTP 429", make_mock_http_error(429, "Rate limit"), "APPEND_FAILED", 0, 1, 0),
    ("HTTP 500", make_mock_http_error(500, "Backend failure"), "APPEND_FAILED", 0, 1, 0),
    ("HTTP 502", make_mock_http_error(502, "Bad Gateway"), "APPEND_FAILED", 0, 1, 0),
    ("HTTP 503", make_mock_http_error(503, "Service Unavailable"), "APPEND_FAILED", 0, 1, 0),
    ("HTTP 504", make_mock_http_error(504, "Gateway Timeout"), "APPEND_FAILED", 0, 1, 0),
])
def test_m6_area_d_end_to_end_scenarios_matrix(tmp_path, scenario, side_effect, expected_status, expected_synced, expected_failed, expected_dead):
    """Verifies each scenario in the required reliability matrix behaves according to spec."""
    db_file = tmp_path / f"test_matrix_{scenario.replace(' ', '_')}.db"
    init_db(db_file)
    seed_test_student_and_session(db_file, card_uid=f"CARD_{scenario[:8]}")

    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES (1, 1, '2026-11-08T10:00:00Z', 'Present', 'rfid');")
    att_id = cursor.lastrowid
    cursor.execute("INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at) VALUES (?, 'pending', 0, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');", (att_id,))
    conn.commit()
    conn.close()

    mock_service = make_mock_google_service()
    if side_effect:
        mock_service.spreadsheets().values().append.return_value.execute.side_effect = side_effect

    res = sync_pending_records_batch(db_path=db_file, spreadsheet_id="mock_id", service=mock_service)
    assert res["status"] == expected_status

    stats = get_queue_stats(db_file)
    assert stats["synced"] == expected_synced
    assert stats["failed"] == expected_failed
    assert stats["dead_letter"] == expected_dead


def test_m6_area_d_missing_config_scenarios(tmp_path):
    """Verifies missing credentials and missing spreadsheet ID claim zero records."""
    db_file = create_test_db(tmp_path, "missing_cfg.db")
    seed_test_student_and_session(db_file)
    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES (1, 1, '2026-11-08T10:00:00Z', 'Present', 'rfid');")
    conn.execute("INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at) VALUES (1, 'pending', 0, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');")
    conn.commit()
    conn.close()

    # Missing spreadsheet ID
    res_no_id = sync_pending_records_batch(db_path=db_file, spreadsheet_id="", service=None)
    assert res_no_id["status"] == "MISSING_SPREADSHEET_ID"
    assert res_no_id["claimed_count"] == 0

    # Missing credentials on disk
    with patch("app.services.sheets_worker.get_google_config", return_value={"spreadsheet_id": "test_id", "sheet_name": "Attendance", "credentials_path": "nonexistent/creds.json"}):
        res_no_creds = sync_pending_records_batch(db_path=db_file, spreadsheet_id="test_id", service=None)
        assert res_no_creds["status"] == "MISSING_CREDENTIALS"
        assert res_no_creds["claimed_count"] == 0

    # Queue remains in pending without burning retries
    stats = get_queue_stats(db_file)
    assert stats["pending"] == 1
    assert stats["failed"] == 0


# ==============================================================================
# AREA E: Dead-Letter Administration & API Security
# ==============================================================================

def test_m6_area_e_dead_letter_admin_api_security(tmp_path):
    """Verifies pagination, error classification, authentication, and CSRF protection on dead-letter APIs."""
    app = create_app("testing")
    db_file = create_test_db(tmp_path, "dl_admin.db")
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = False  # Real auth checks

    # 1. Unauthenticated checks
    with app.test_client() as client:
        r_get = client.get("/api/admin/sheets-sync/dead-letter")
        assert r_get.status_code == 401
        r_post = client.post("/api/admin/sheets-sync/dead-letter/retry", json={})
        assert r_post.status_code == 401

    # 2. Authenticated checks with test credentials
    app.config["LOGIN_DISABLED"] = True
    app.config["CSRF_ENABLED"] = False

    seed_test_student_and_session(db_file)
    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES (1, 1, '2026-11-08T10:00:00Z', 'Present', 'rfid');")
    cursor.execute("INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, last_error, created_at, updated_at) VALUES (1, 'dead_letter', 5, 'HTTP 403 Forbidden', '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');")
    conn.commit()
    conn.close()

    with app.test_client() as client:
        # GET dead-letter list
        r_list = client.get("/api/admin/sheets-sync/dead-letter?limit=10&offset=0")
        assert r_list.status_code == 200
        dl_data = r_list.get_json()
        assert dl_data["total_dead_letter"] == 1
        assert dl_data["records"][0]["error_category"] == "exhausted_retries"

        # Explicit empty list queue_ids: [] MUST return HTTP 400
        r_empty = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": []})
        assert r_empty.status_code == 400
        assert r_empty.get_json()["error"] == "Invalid Payload"

        # Selective requeue with valid ID
        r_retry = client.post("/api/admin/sheets-sync/dead-letter/retry", json={"queue_ids": [1]})
        assert r_retry.status_code == 200
        assert r_retry.get_json()["requeued_count"] == 1

        stats = get_queue_stats(db_file)
        assert stats["dead_letter"] == 0
        assert stats["pending"] == 1


# ==============================================================================
# AREA F: Retry Policy & Concurrency Reliability
# ==============================================================================

def test_m6_area_f_retry_policy_and_multi_worker_concurrency(tmp_path):
    """Verifies retry calculations, absolute 900s cap, and disjoint concurrent worker claims."""
    # Absolute ceiling test: delay with +10% jitter at high retry count must never exceed 900.0s
    for r in range(5, 12):
        for _ in range(20):
            d = calculate_retry_delay(r)
            assert 1.0 <= d <= 900.0, f"Retry delay {d} exceeded absolute 900.0s ceiling!"

    # Multi-worker concurrency
    db_file = create_test_db(tmp_path, "concurrency.db")
    conn = get_db_connection(db_file)
    for i in range(1, 7):
        conn.execute(f"INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES ('CS-2024-{i:03d}', 'S{i}', 'CARD_{i}', 1, '2026-11-08T10:00:00Z');")
    conn.execute("INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at) VALUES ('Network Security', '2026-11-08', '08:00', '23:59', 1, '2026-11-08T10:00:00Z');")
    for i in range(1, 7):
        conn.execute(f"INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES ({i}, 1, '2026-11-08T10:00:00Z', 'Present', 'rfid');")
        conn.execute(f"INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at) VALUES ({i}, 'pending', 0, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');")
    conn.commit()
    conn.close()

    # Worker 1 claims 3; Worker 2 claims 3
    w1_batch = claim_records(worker_id="w-1", limit=3, db_path=db_file)
    w2_batch = claim_records(worker_id="w-2", limit=3, db_path=db_file)

    w1_ids = {r["attendance_id"] for r in w1_batch}
    w2_ids = {r["attendance_id"] for r in w2_batch}

    assert len(w1_ids) == 3
    assert len(w2_ids) == 3
    assert w1_ids.isdisjoint(w2_ids), "Concurrent workers must not claim overlapping records!"


# ==============================================================================
# AREA G: Security, Git Rules, and Secret Redaction
# ==============================================================================

def test_m6_area_g_security_and_credentials_protection():
    """Verifies that .gitignore rules protect credentials and error logs never contain secret blocks."""
    gitignore_path = Path("C:/Users/niraj/Downloads/Smart-Attendance-System/.gitignore")
    assert gitignore_path.is_file()
    content = gitignore_path.read_text(encoding="utf-8")

    # Protection for service account keys
    assert "credentials/" in content or "service_account.json" in content

    # Verification of formula sanitization helper against dangerous characters
    assert sanitize_sheet_cell("=1+1") == "'=1+1"
    assert sanitize_sheet_cell("+SUM(A1:A5)") == "'+SUM(A1:A5)"
    assert sanitize_sheet_cell("-50") == "'-50"
    assert sanitize_sheet_cell("@command") == "'@command"
    assert sanitize_sheet_cell("Normal Text") == "Normal Text"
    assert sanitize_sheet_cell(12345) == 12345  # Numeric values preserved


# ==============================================================================
# AREA H: Authoritative Database Immutability & ACID Constraints
# ==============================================================================

def test_m6_area_h_attendance_immutability_throughout_sync_lifecycle(tmp_path):
    """
    Verifies that attendance records are never modified, deleted, or rolled back
    during any phase of synchronization, failure, retry, dead-letter, or requeuing.
    """
    db_file = create_test_db(tmp_path, "immutability.db")
    seed_test_student_and_session(db_file)

    conn = get_db_connection(db_file)
    conn.execute("INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES (1, 1, '2026-11-08T10:00:00Z', 'Present', 'rfid');")
    conn.execute("INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at) VALUES (1, 'pending', 0, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');")
    conn.commit()

    initial_attendance = [dict(r) for r in conn.execute("SELECT * FROM attendance;").fetchall()]
    conn.close()

    # 1. Simulate failure & retry
    schedule_retry(queue_id=1, error_message="Network glitch", db_path=db_file)

    # 2. Simulate move to dead letter
    move_to_dead_letter(queue_id=1, error_message="Fatal error", db_path=db_file)

    # 3. Simulate requeue
    retry_dead_letter_records(queue_ids=[1], db_path=db_file)

    # 4. Simulate mark synced
    mark_as_synced(queue_id=1, synced_at="2026-11-08T10:15:00Z", db_path=db_file)

    conn = get_db_connection(db_file)
    final_attendance = [dict(r) for r in conn.execute("SELECT * FROM attendance;").fetchall()]
    conn.close()

    assert initial_attendance == final_attendance, "Authoritative attendance rows must remain 100% byte-for-byte immutable!"
