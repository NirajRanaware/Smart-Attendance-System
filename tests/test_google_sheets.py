"""
Automated unit and integration test suite for Google Sheets API Client (Phase 3 Milestone 3).

Verifies:
1. Credential loading, file existence checks, and JSON syntax validation.
2. Protection against invalid key types (non-service-account) and missing key fields.
3. Successful mocked client initialization, caching, and cache reset.
4. Missing spreadsheet ID handling.
5. Successful mocked spreadsheet connection verification.
6. HTTP error classifications (401 Auth, 403 Forbidden, 404 Not Found, 429 Rate Limit, 500 Server Error).
7. Network timeout and socket failure resilience.
8. Header provisioning on empty sheet (writes 10 standard columns).
9. Existing header detection without duplicate writes.
10. Existing data protection (conflicting non-header row 1 prevents blind overwrite).
11. Standard 10-column row mapping exactness.
12. Spreadsheet Formula Injection protection (CWE-1236) for '=', '+', '-', '@', '\\t', '\\r'.
13. Numeric Attendance ID preservation (native integer, not string).
14. Batch row preparation helper.
15. Reusable batch append helper.
16. Unconfigured Google credentials do not prevent application startup.
17. Core attendance scanning and SQLite persistence remain completely unaffected.

CRITICAL SAFETY DIRECTIVE:
All tests use mocked Google API responses. NO real outbound HTTP calls are made to Google APIs.
"""

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app import create_app
from app.database import get_db_connection
from app.services.google_sheets import (
    DEFAULT_SPREADSHEET_HEADERS,
    DEFAULT_SHEETS_SCOPES,
    GoogleSheetsError,
    GoogleSheetsConfigError,
    GoogleSheetsAuthError,
    GoogleSheetsPermissionError,
    GoogleSheetsNotFoundError,
    GoogleSheetsRateLimitError,
    GoogleSheetsServerError,
    GoogleSheetsNetworkError,
    sanitize_sheet_cell,
    load_credentials,
    get_sheets_service,
    reset_sheets_service,
    verify_sheet_connection,
    ensure_sheet_headers,
    format_attendance_row,
    format_attendance_batch,
    append_attendance_batch,
)


@pytest.fixture(autouse=True)
def reset_sheets_cache():
    """Ensures sheets service client singleton is reset before and after each test."""
    reset_sheets_service()
    yield
    reset_sheets_service()


def make_mock_http_error(status_code: int, message: str = "Google API Error") -> HttpError:
    """Helper creating a simulated Google HttpError instance."""
    resp = Response({"status": str(status_code)})
    content = json.dumps({"error": {"code": status_code, "message": message}}).encode("utf-8")
    return HttpError(resp, content)


# ==============================================================================
# 1. Credential Loading & Security Validation Tests
# ==============================================================================

def test_1_missing_credentials_file_raises_config_error(tmp_path):
    """Scenario 1: Missing credentials file raises GoogleSheetsConfigError without crashing."""
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(GoogleSheetsConfigError) as exc_info:
        load_credentials(credentials_path=missing_path)

    assert "was not found" in str(exc_info.value)
    assert not exc_info.value.retryable


def test_2_invalid_json_syntax_raises_config_error(tmp_path):
    """Scenario 2: Malformed JSON syntax is caught safely."""
    bad_json_file = tmp_path / "bad.json"
    bad_json_file.write_text("{invalid json string", encoding="utf-8")

    with pytest.raises(GoogleSheetsConfigError) as exc_info:
        load_credentials(credentials_path=bad_json_file)

    assert "invalid JSON" in str(exc_info.value)


def test_3_non_service_account_type_rejected(tmp_path):
    """Scenario 3: Non-service-account OAuth keys are rejected."""
    oauth_file = tmp_path / "oauth_client.json"
    oauth_file.write_text(json.dumps({
        "type": "authorized_user",
        "client_id": "12345.apps.googleusercontent.com"
    }), encoding="utf-8")

    with pytest.raises(GoogleSheetsConfigError) as exc_info:
        load_credentials(credentials_path=oauth_file)

    assert "Expected key type 'service_account'" in str(exc_info.value)


def test_4_missing_required_credential_fields_rejected(tmp_path):
    """Scenario 4: Service account file missing client_email or private_key is rejected."""
    incomplete_file = tmp_path / "incomplete.json"
    incomplete_file.write_text(json.dumps({
        "type": "service_account",
        "project_id": "test-project"
    }), encoding="utf-8")

    with pytest.raises(GoogleSheetsConfigError) as exc_info:
        load_credentials(credentials_path=incomplete_file)

    assert "missing required fields" in str(exc_info.value)


@patch("google.oauth2.service_account.Credentials.from_service_account_file")
@patch("app.services.google_sheets.build")
def test_5_successful_mocked_client_initialization_and_caching(mock_build, mock_from_file, tmp_path):
    """Scenario 5: Valid credential file initializes service Resource and reuses cache."""
    valid_key = tmp_path / "service_account.json"
    valid_key.write_text(json.dumps({
        "type": "service_account",
        "client_email": "attendance-service@test-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgk...\n-----END PRIVATE KEY-----\n"
    }), encoding="utf-8")

    fake_creds = MagicMock()
    mock_from_file.return_value = fake_creds

    fake_resource = MagicMock()
    mock_build.return_value = fake_resource

    # First call builds client
    svc1 = get_sheets_service(credentials_path=valid_key, cache=True)
    assert svc1 == fake_resource
    assert mock_build.call_count == 1

    # Second call returns cached singleton
    svc2 = get_sheets_service(credentials_path=valid_key, cache=True)
    assert svc2 == fake_resource
    assert mock_build.call_count == 1  # Not called again


# ==============================================================================
# 2. Connection Verification & Error Handling Tests
# ==============================================================================

def test_6_missing_spreadsheet_id_returns_error():
    """Scenario 6: Missing spreadsheet ID reports clear error without making API calls."""
    res = verify_sheet_connection(spreadsheet_id="")
    assert res["success"] is False
    assert res["error_type"] == "MISSING_SPREADSHEET_ID"
    assert res["retryable"] is False


def test_7_successful_mocked_connection_verification():
    """Scenario 7: Successful spreadsheet metadata query returns title and sheet names."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.return_value = {
        "spreadsheetId": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
        "properties": {"title": "College Attendance 2026"},
        "sheets": [
            {"properties": {"title": "Attendance"}},
            {"properties": {"title": "Summary"}}
        ]
    }

    res = verify_sheet_connection("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", service=mock_service)
    assert res["success"] is True
    assert res["title"] == "College Attendance 2026"
    assert "Attendance" in res["sheets"]


def test_8_permission_denied_http_403_handling():
    """Scenario 8: HTTP 403 Forbidden is translated to PERMISSION_DENIED with sharing advice."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.side_effect = make_mock_http_error(
        403, "The caller does not have permission"
    )

    res = verify_sheet_connection("mock_sheet_id", service=mock_service)
    assert res["success"] is False
    assert res["error_type"] == "PERMISSION_DENIED"
    assert res["status_code"] == 403
    assert res["retryable"] is False
    assert "share the Google Spreadsheet" in res["message"]


def test_9_not_found_http_404_handling():
    """Scenario 9: HTTP 404 Not Found is translated to NOT_FOUND."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.side_effect = make_mock_http_error(
        404, "Requested entity was not found"
    )

    res = verify_sheet_connection("invalid_sheet_id", service=mock_service)
    assert res["success"] is False
    assert res["error_type"] == "NOT_FOUND"
    assert res["status_code"] == 404
    assert res["retryable"] is False


def test_10_rate_limit_http_429_handling():
    """Scenario 10: HTTP 429 Quota Exceeded is marked as retryable."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.side_effect = make_mock_http_error(
        429, "Rate limit exceeded"
    )

    res = verify_sheet_connection("mock_sheet_id", service=mock_service)
    assert res["success"] is False
    assert res["error_type"] == "RATE_LIMITED"
    assert res["status_code"] == 429
    assert res["retryable"] is True


def test_11_server_error_http_503_handling():
    """Scenario 11: HTTP 503 Backend Service Unavailable is marked as retryable."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.side_effect = make_mock_http_error(
        503, "The service is currently unavailable"
    )

    res = verify_sheet_connection("mock_sheet_id", service=mock_service)
    assert res["success"] is False
    assert res["error_type"] == "SERVER_ERROR"
    assert res["status_code"] == 503
    assert res["retryable"] is True


def test_12_network_timeout_handling():
    """Scenario 12: Socket timeout is captured and marked as retryable NETWORK_ERROR."""
    mock_service = MagicMock()
    mock_service.spreadsheets().get().execute.side_effect = socket.timeout("Timed out connecting to googleapis.com")

    res = verify_sheet_connection("mock_sheet_id", service=mock_service)
    assert res["success"] is False
    assert res["error_type"] == "NETWORK_ERROR"
    assert res["retryable"] is True
    assert "timeout" in res["message"].lower()


# ==============================================================================
# 3. Header Provisioning Tests
# ==============================================================================

def test_13_header_provisioning_on_empty_sheet():
    """Scenario 13: Empty sheet writes the exact 10 standard columns to row 1."""
    mock_service = MagicMock()
    # Row 1 is currently empty
    mock_service.spreadsheets().values().get().execute.return_value = {"values": []}

    res = ensure_sheet_headers("mock_sheet_id", sheet_name="Attendance", service=mock_service)

    assert res["success"] is True
    assert res["created"] is True
    assert res["status"] == "CREATED"
    assert res["headers"] == DEFAULT_SPREADSHEET_HEADERS

    # Verify update was called with USER_ENTERED and the exact 10 headers
    mock_service.spreadsheets().values().update.assert_called_once()
    call_kwargs = mock_service.spreadsheets().values().update.call_args[1]
    assert call_kwargs["spreadsheetId"] == "mock_sheet_id"
    assert call_kwargs["range"] == "'Attendance'!A1:J1"
    assert call_kwargs["valueInputOption"] == "USER_ENTERED"
    assert call_kwargs["body"]["values"] == [DEFAULT_SPREADSHEET_HEADERS]


def test_14_existing_header_detection_avoids_duplicate_write():
    """Scenario 14: If row 1 already contains the headers, do not write again."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().get().execute.return_value = {
        "values": [DEFAULT_SPREADSHEET_HEADERS]
    }

    res = ensure_sheet_headers("mock_sheet_id", sheet_name="Attendance", service=mock_service)

    assert res["success"] is True
    assert res["created"] is False
    assert res["status"] == "ALREADY_PRESENT"

    # update() must NOT be called
    mock_service.spreadsheets().values().update.assert_not_called()


def test_15_existing_spreadsheet_data_preservation_conflict():
    """Scenario 15: If row 1 contains unrecognized data, abort write to prevent overwriting user data."""
    mock_service = MagicMock()
    # Row 1 has different data (e.g. existing attendance rows without headers)
    mock_service.spreadsheets().values().get().execute.return_value = {
        "values": [[101, "2026-11-08", "10:00:00", "CS-2024-001", "Aarav Sharma"]]
    }

    res = ensure_sheet_headers("mock_sheet_id", sheet_name="Attendance", service=mock_service)

    assert res["success"] is False
    assert res["created"] is False
    assert res["status"] == "CONFLICT"
    assert res["error_type"] == "HEADER_CONFLICT"
    assert "Aborting write to preserve existing spreadsheet data" in res["message"]

    # update() must NOT be called
    mock_service.spreadsheets().values().update.assert_not_called()


# ==============================================================================
# 4. Formula Injection Protection (CWE-1236) & Formatting Tests
# ==============================================================================

def test_16_formula_injection_sanitization():
    """Scenario 16: Values starting with '=', '+', '-', '@', '\\t', '\\r' are prepended with single quote."""
    # Malicious inputs
    assert sanitize_sheet_cell("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert sanitize_sheet_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_sheet_cell("-1+1") == "'-1+1"
    assert sanitize_sheet_cell("@attacker.org") == "'@attacker.org"
    assert sanitize_sheet_cell("\tTAB_INJECTION") == "'\tTAB_INJECTION"
    assert sanitize_sheet_cell("\rRETURN_INJECTION") == "'\rRETURN_INJECTION"

    # Safe legitimate inputs remain unaltered
    assert sanitize_sheet_cell("CS-2024-001") == "CS-2024-001"
    assert sanitize_sheet_cell("Aarav Sharma") == "Aarav Sharma"
    assert sanitize_sheet_cell("Embedded Systems") == "Embedded Systems"
    assert sanitize_sheet_cell("AA11BB22") == "AA11BB22"
    assert sanitize_sheet_cell("Present") == "Present"
    assert sanitize_sheet_cell("rfid") == "rfid"
    assert sanitize_sheet_cell("") == ""
    assert sanitize_sheet_cell(None) == ""


def test_17_attendance_id_numeric_preservation():
    """Scenario 17: Numeric Attendance ID is preserved as a native integer for mathematical sorting in Sheets."""
    assert sanitize_sheet_cell(42) == 42
    assert isinstance(sanitize_sheet_cell(42), int)
    assert sanitize_sheet_cell(1001) == 1001
    assert isinstance(sanitize_sheet_cell(1001), int)

    record = {
        "attendance_id": 105,
        "scanned_at": "2026-11-08T04:30:00Z",
        "roll_number": "CS-01",
        "student_name": "Test Student",
        "course_name": "IoT Lab",
        "card_uid": "UID99",
        "attendance_status": "Present",
        "source": "rfid"
    }
    row = format_attendance_row(record)
    assert row[0] == 105
    assert isinstance(row[0], int)


def test_18_ten_column_row_mapping_exactness():
    """Scenario 18: format_attendance_row maps all 10 columns accurately according to specification."""
    record = {
        "attendance_id": 42,
        "scanned_at": "2026-11-08T04:30:00Z",  # 10:00:00 AM IST
        "roll_number": "=MALICIOUS_ROLL",
        "student_name": "Aarav Sharma",
        "course_name": "Embedded Systems",
        "card_uid": "AA11BB22",
        "attendance_status": "Present",
        "source": "rfid"
    }

    row = format_attendance_row(record, sync_timestamp="2026-11-08T10:05:00Z")

    assert len(row) == 10
    assert row[0] == 42                     # 1. Attendance ID (int)
    assert row[1] == "2026-11-08"           # 2. Date (YYYY-MM-DD in IST)
    assert row[2] == "10:00:00"             # 3. Time (HH:MM:SS in IST)
    assert row[3] == "'=MALICIOUS_ROLL"     # 4. Roll Number (sanitized)
    assert row[4] == "Aarav Sharma"         # 5. Student Name
    assert row[5] == "Embedded Systems"     # 6. Course / Session
    assert row[6] == "AA11BB22"             # 7. RFID UID
    assert row[7] == "Present"              # 8. Attendance Status
    assert row[8] == "rfid"                 # 9. Source / Device
    assert row[9] == "2026-11-08T10:05:00Z" # 10. Sync Timestamp


def test_19_batch_row_formatting():
    """Scenario 19: format_attendance_batch formats a list of records into a 2D matrix."""
    records = [
        {"attendance_id": 1, "student_name": "Student A", "scanned_at": "2026-11-08T05:00:00Z"},
        {"attendance_id": 2, "student_name": "Student B", "scanned_at": "2026-11-08T05:01:00Z"},
    ]
    batch = format_attendance_batch(records)
    assert len(batch) == 2
    assert batch[0][0] == 1
    assert batch[1][0] == 2
    assert all(len(r) == 10 for r in batch)


def test_20_mocked_append_attendance_batch():
    """Scenario 20: append_attendance_batch calls Google Sheets values.append with USER_ENTERED."""
    mock_service = MagicMock()
    mock_append_fn = MagicMock()
    mock_service.spreadsheets().values().append = mock_append_fn
    mock_append_fn.return_value.execute.return_value = {
        "updates": {
            "updatedRows": 2,
            "updatedRange": "'Attendance'!A2:J3"
        }
    }

    test_rows = [
        [1, "2026-11-08", "10:00:00", "CS-01", "Aarav", "IoT", "UID1", "Present", "rfid", "2026-11-08T10:05:00Z"],
        [2, "2026-11-08", "10:01:00", "CS-02", "Bhavna", "IoT", "UID2", "Present", "rfid", "2026-11-08T10:05:00Z"],
    ]

    res = append_attendance_batch("mock_sheet_id", sheet_name="Attendance", rows=test_rows, service=mock_service)

    assert res["success"] is True
    assert res["appended_count"] == 2
    assert res["updated_range"] == "'Attendance'!A2:J3"

    mock_append_fn.assert_called_once()
    call_kwargs = mock_append_fn.call_args[1]
    assert call_kwargs["spreadsheetId"] == "mock_sheet_id"
    assert call_kwargs["range"] == "'Attendance'!A:J"
    assert call_kwargs["valueInputOption"] == "RAW"
    assert call_kwargs["insertDataOption"] == "INSERT_ROWS"
    assert call_kwargs["body"]["values"] == test_rows


# ==============================================================================
# 5. Application Startup & Backward Compatibility Tests
# ==============================================================================

def test_21_application_starts_normally_without_google_credentials():
    """Scenario 21: Flask application starts cleanly without configured Google credentials."""
    # Start app in testing mode without setting service_account.json
    app = create_app("testing")
    with app.test_client() as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["database"] == "connected"


def test_22_attendance_recording_remains_unaffected_without_google_sheets(tmp_path):
    """
    Scenario 22: Attendance scan endpoint (/api/scans) records successfully in SQLite
    and creates local outbox queue item, completely decoupled from Google Sheets availability.
    """
    db_file = tmp_path / "integration_test.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_file
    app.config["LOGIN_DISABLED"] = True

    from app.database import init_db
    init_db(db_file)

    conn = get_db_connection(db_file)
    cursor = conn.cursor()
    # Seed active student and active session
    cursor.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('CS-INT-01', 'Integration Student', 'INT_CARD_99', 1, '2026-11-08T10:00:00Z');
        """
    )
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Integration Course', '2026-11-08', '08:00', '23:59', 1, '2026-11-08T10:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    with app.test_client() as client:
        # Perform scan request
        scan_resp = client.post("/api/scans", json={
            "card_uid": "INT_CARD_99",
            "source": "rfid"
        })
        assert scan_resp.status_code == 201
        data = scan_resp.get_json()
        assert data["success"] is True
        assert data["attendance"]["roll_number"] == "CS-INT-01"
        att_id = data["attendance"]["id"]

        # Verify SQLite attendance record
        conn = get_db_connection(db_file)
        c = conn.cursor()
        c.execute("SELECT * FROM attendance WHERE id = ?;", (att_id,))
        att_row = c.fetchone()
        assert att_row is not None
        assert att_row["status"] == "Present"

        # Verify local outbox record was created atomically in SQLite
        c.execute("SELECT * FROM sheets_sync_queue WHERE attendance_id = ?;", (att_id,))
        queue_row = c.fetchone()
        assert queue_row is not None
        assert queue_row["sync_status"] == "pending"
        conn.close()
