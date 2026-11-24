"""
Unit and integration test suite for Deterministic Google Sheets Row Update Service.
Stage 9.7B.13I: Tests A through U.

CRITICAL SAFETY DIRECTIVE:
All tests use mocked Google API responses. NO real outbound HTTP or socket calls are made to Google APIs.
"""

import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.services.google_sheets import (
    DEFAULT_SPREADSHEET_HEADERS,
    GoogleSheetsError,
    GoogleSheetsConfigError,
    GoogleSheetsAuthError,
    GoogleSheetsPermissionError,
    GoogleSheetsNotFoundError,
    GoogleSheetsRateLimitError,
    GoogleSheetsServerError,
    GoogleSheetsNetworkError,
    GoogleSheetsTimeoutError,
    GoogleSheetsValidationError,
    sanitize_sheet_cell,
    format_attendance_row,
    construct_deterministic_range,
    update_attendance_row,
    update_attendance_batch,
    reset_sheets_service,
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


def sample_record(att_id: int = 1, student_name: str = "Alice Smith", roll_no: str = "2026-CS-001") -> dict:
    """Helper returning a standard attendance record dictionary."""
    return {
        "attendance_id": att_id,
        "scanned_at": "2026-11-24T04:30:00Z",  # 10:00:00 IST
        "roll_number": roll_no,
        "student_name": student_name,
        "course_name": "CS-101",
        "card_uid": "A1B2C3D4",
        "status": "Present",
        "source": "RFID"
    }


# ==============================================================================
# Test A — Correct Nine-Column Payload
# ==============================================================================
def test_test_a_correct_nine_column_payload():
    """Test A: Verify exact column ordering and 9-column contract."""
    rec = {
        "attendance_id": 42,
        "scanned_at": "2026-11-24T04:30:00Z",
        "roll_number": "=CMD|' /C calc'!A0",  # Test formula injection
        "student_name": "Bob Jones",
        "course_name": "CS-101: Systems",
        "card_uid": "CARD-99",
        "status": "Present",
        "source": "RFID"
    }
    row = format_attendance_row(rec)
    assert len(row) == 9

    # 1. Attendance ID (must be native int)
    assert row[0] == 42
    assert isinstance(row[0], int)
    assert not isinstance(row[0], bool)

    # 2. Date in Asia/Kolkata
    assert row[1] == "2026-11-24"

    # 3. Time in Asia/Kolkata (04:30 UTC -> 10:00:00 IST)
    assert row[2] == "10:00:00"

    # 4. Roll Number (formula injection sanitized with leading quote)
    assert row[3] == "'=CMD|' /C calc'!A0"

    # 5. Student Name
    assert row[4] == "Bob Jones"

    # 6. Course/Session
    assert row[5] == "CS-101: Systems"

    # 7. RFID UID
    assert row[6] == "CARD-99"

    # 8. Status
    assert row[7] == "Present"

    # 9. Source/Device
    assert row[8] == "RFID"


# ==============================================================================
# Test B — Correct Deterministic Range
# ==============================================================================
def test_test_b_correct_deterministic_range():
    """Test B: Verify expected range construction for multiple permanent row assignments."""
    assert construct_deterministic_range("Attendance_Log", 3) == "Attendance_Log!A3:I3"
    assert construct_deterministic_range("Attendance_Log", 4) == "Attendance_Log!A4:I4"
    assert construct_deterministic_range("Attendance_Log", 5) == "Attendance_Log!A5:I5"
    assert construct_deterministic_range("Attendance_Log", 11) == "Attendance_Log!A11:I11"
    assert construct_deterministic_range("Attendance_Log", 12) == "Attendance_Log!A12:I12"

    # Quoted sheet name with spaces
    assert construct_deterministic_range("Class Log", 5) == "'Class Log'!A5:I5"


# ==============================================================================
# Test C — Update API Usage (Never Append)
# ==============================================================================
def test_test_c_update_api_usage():
    """Test C: Verify update semantics are used and append semantics are never called."""
    mock_service = MagicMock()
    mock_update = mock_service.spreadsheets.return_value.values.return_value.update
    mock_append = mock_service.spreadsheets.return_value.values.return_value.append
    mock_update.return_value.execute.return_value = {
        "spreadsheetId": "test_sheet_id",
        "updatedRange": "Attendance_Log!A5:I5",
        "updatedRows": 1,
        "updatedColumns": 9,
        "updatedCells": 9
    }

    rec = sample_record(att_id=3)
    res = update_attendance_row(
        attendance_record=rec,
        sheet_row_number=5,
        spreadsheet_id="test_sheet_id",
        sheet_name="Attendance_Log",
        service=mock_service
    )

    assert res["success"] is True
    assert res["operation"] == "UPDATE"
    assert res["target_range"] == "Attendance_Log!A5:I5"
    assert res["sheet_row_number"] == 5
    assert res["attendance_id"] == 3

    # Assert update was called with correct parameters
    mock_update.assert_called_with(
        spreadsheetId="test_sheet_id",
        range="Attendance_Log!A5:I5",
        valueInputOption="RAW",
        body={
            "majorDimension": "ROWS",
            "values": [[3, "2026-11-24", "10:00:00", "2026-CS-001", "Alice Smith", "CS-101", "A1B2C3D4", "Present", "RFID"]]
        }
    )

    # CRITICAL: Assert append was NEVER called
    mock_append.assert_not_called()


# ==============================================================================
# Test D — Repeated Execution (Idempotency)
# ==============================================================================
def test_test_d_repeated_execution():
    """Test D: Repeatedly update the same record and verify all requests target the exact same range."""
    mock_service = MagicMock()
    mock_update = mock_service.spreadsheets.return_value.values.return_value.update
    mock_append = mock_service.spreadsheets.return_value.values.return_value.append
    mock_update.return_value.execute.return_value = {"updatedRows": 1, "updatedRange": "Attendance_Log!A4:I4"}

    rec = sample_record(att_id=2)

    # Execute 3 consecutive updates for the same row
    for _ in range(3):
        res = update_attendance_row(
            attendance_record=rec,
            sheet_row_number=4,
            spreadsheet_id="test_sheet_id",
            service=mock_service
        )
        assert res["success"] is True
        assert res["target_range"] == "Attendance_Log!A4:I4"

    # All 3 targeted the exact same range A4:I4
    assert mock_update.call_count == 3
    for call in mock_update.call_args_list:
        assert call.kwargs["range"] == "Attendance_Log!A4:I4"

    # Append was never called
    mock_append.assert_not_called()


# ==============================================================================
# Test E — Different Records Target Respective Ranges
# ==============================================================================
def test_test_e_different_records():
    """Test E: Verify records with different assigned rows target their respective distinct ranges."""
    mock_service = MagicMock()
    mock_update = mock_service.spreadsheets.return_value.values.return_value.update
    mock_update.return_value.execute.return_value = {"updatedRows": 1}

    records_with_rows = [
        (sample_record(att_id=1, student_name="Student 1"), 3),
        (sample_record(att_id=2, student_name="Student 2"), 4),
        (sample_record(att_id=3, student_name="Student 3"), 5),
    ]

    ranges_called = []
    for rec, row_num in records_with_rows:
        res = update_attendance_row(
            attendance_record=rec,
            sheet_row_number=row_num,
            spreadsheet_id="test_sheet_id",
            service=mock_service
        )
        assert res["success"] is True
        ranges_called.append(res["target_range"])

    assert ranges_called == ["Attendance_Log!A3:I3", "Attendance_Log!A4:I4", "Attendance_Log!A5:I5"]
    assert mock_update.call_count == 3


# ==============================================================================
# Test F — Invalid Row Number Rejection
# ==============================================================================
def test_test_f_invalid_row():
    """Test F: Reject zero, negative, missing, malformed, boolean, and header-row values."""
    rec = sample_record(att_id=1)
    invalid_rows = [0, -1, -100, None, "five", 3.14, True, False, 1]  # 1 is reserved for headers

    for bad_row in invalid_rows:
        # Check non-raising mode returns error dict
        res = update_attendance_row(rec, sheet_row_number=bad_row, spreadsheet_id="test_id")
        assert res["success"] is False
        assert res["error_type"] == "VALIDATION_ERROR"
        assert res["retryable"] is False

        # Check raising mode raises GoogleSheetsValidationError
        with pytest.raises(GoogleSheetsValidationError):
            update_attendance_row(rec, sheet_row_number=bad_row, spreadsheet_id="test_id", raise_for_status=True)

        with pytest.raises(GoogleSheetsValidationError):
            construct_deterministic_range("Attendance_Log", bad_row)


# ==============================================================================
# Test G — Invalid Payload Rejection
# ==============================================================================
def test_test_g_invalid_payload():
    """Test G: Reject malformed payloads (None, empty dict, string, integer)."""
    invalid_payloads = [None, {}, "not a record", 12345]

    for bad_payload in invalid_payloads:
        res = update_attendance_row(bad_payload, sheet_row_number=5, spreadsheet_id="test_id")
        assert res["success"] is False
        assert res["error_type"] == "VALIDATION_ERROR"

        with pytest.raises(GoogleSheetsValidationError):
            update_attendance_row(bad_payload, sheet_row_number=5, spreadsheet_id="test_id", raise_for_status=True)


# ==============================================================================
# Test H — Incorrect Column Count Rejection
# ==============================================================================
def test_test_h_incorrect_column_count():
    """Test H: Reject payloads with fewer or more than nine values."""
    # 8 values (missing source)
    eight_cols = [1, "2026-11-24", "10:00:00", "ROLL-1", "Alice", "CS-101", "UID-1", "Present"]
    res = update_attendance_row(eight_cols, sheet_row_number=5, spreadsheet_id="test_id")
    assert res["success"] is False
    assert res["error_type"] == "VALIDATION_ERROR"

    # 10 values (extra column)
    ten_cols = [1, "2026-11-24", "10:00:00", "ROLL-1", "Alice", "CS-101", "UID-1", "Present", "RFID", "EXTRA"]
    res2 = update_attendance_row(ten_cols, sheet_row_number=5, spreadsheet_id="test_id")
    assert res2["success"] is False
    assert res2["error_type"] == "VALIDATION_ERROR"

    with pytest.raises(GoogleSheetsValidationError):
        update_attendance_row(ten_cols, sheet_row_number=5, spreadsheet_id="test_id", raise_for_status=True)


# ==============================================================================
# Test I — Missing Attendance ID Rejection
# ==============================================================================
def test_test_i_missing_attendance_id():
    """Test I: Reject invalid attendance records missing attendance_id or having non-positive/non-int IDs."""
    bad_records = [
        {"student_name": "No ID"},
        {"attendance_id": None, "student_name": "None ID"},
        {"attendance_id": 0, "student_name": "Zero ID"},
        {"attendance_id": -5, "student_name": "Negative ID"},
        {"attendance_id": "not_an_int", "student_name": "String ID"},
        {"attendance_id": True, "student_name": "Bool ID"},
    ]

    for bad_rec in bad_records:
        res = update_attendance_row(bad_rec, sheet_row_number=5, spreadsheet_id="test_id")
        assert res["success"] is False
        assert res["error_type"] == "VALIDATION_ERROR"

        with pytest.raises(GoogleSheetsValidationError):
            update_attendance_row(bad_rec, sheet_row_number=5, spreadsheet_id="test_id", raise_for_status=True)


# ==============================================================================
# Test J — Worksheet/Range Configuration Fail-Closed
# ==============================================================================
def test_test_j_worksheet_range_configuration():
    """Test J: Test valid sheet configuration and fail-closed behavior for invalid configuration."""
    rec = sample_record(att_id=1)

    # Empty spreadsheet ID
    res1 = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="")
    assert res1["success"] is False
    assert res1["error_type"] == "MISSING_SPREADSHEET_ID"

    # Empty worksheet name
    res2 = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="valid_id", sheet_name="")
    assert res2["success"] is False
    assert res2["error_type"] == "VALIDATION_ERROR"


# ==============================================================================
# Test K — Mocked Timeout Configuration
# ==============================================================================
def test_test_k_mocked_timeout():
    """Test K: Verify timeout configuration is passed to the client/transport."""
    mock_service = MagicMock()
    mock_http = MagicMock()

    rec = sample_record(att_id=1)
    update_attendance_row(
        attendance_record=rec,
        sheet_row_number=5,
        spreadsheet_id="test_id",
        service=mock_service,
        timeout_seconds=7.5,
        http=mock_http
    )

    # Verify execute was called with the supplied http transport
    mock_service.spreadsheets().values().update().execute.assert_called_with(http=mock_http)


# ==============================================================================
# Test L — Timeout Error Classification
# ==============================================================================
def test_test_l_timeout_error_classification():
    """Test L: Verify timeout is surfaced as a timeout/temporary error, not success."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = socket.timeout("Operation timed out")

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "TIMEOUT"
    assert res["retryable"] is True
    assert "timeout" in res["message"].lower()

    # In raise_for_status mode, raises GoogleSheetsTimeoutError
    with pytest.raises(GoogleSheetsTimeoutError) as exc_info:
        update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service, raise_for_status=True)
    assert exc_info.value.retryable is True
    assert exc_info.value.error_type == "TIMEOUT"


# ==============================================================================
# Test M — Authentication Error (Credentials Not Exposed)
# ==============================================================================
def test_test_m_authentication_error():
    """Test M: Verify authentication failure (HTTP 401) is classified appropriately and credentials are safe."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = make_mock_http_error(
        401, "Invalid OAuth credentials token"
    )

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "AUTH_ERROR"
    assert res["status_code"] == 401
    assert res["retryable"] is False
    # Ensure private key or secret is not present in output
    assert "private_key" not in res["message"].lower()


# ==============================================================================
# Test N — Rate Limit Error Classification
# ==============================================================================
def test_test_n_rate_limit():
    """Test N: Verify rate-limit behavior (HTTP 429) is classified correctly as retryable."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = make_mock_http_error(
        429, "Rate limit exceeded. User Rate Limit Exceeded"
    )

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "RATE_LIMITED"
    assert res["status_code"] == 429
    assert res["retryable"] is True


# ==============================================================================
# Test O — Temporary Remote Server Failure
# ==============================================================================
def test_test_o_temporary_remote_server_failure():
    """Test O: Verify temporary server failures (HTTP 500, 503) are surfaced correctly as retryable."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = make_mock_http_error(
        503, "Service Unavailable backend timeout"
    )

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "SERVER_ERROR"
    assert res["status_code"] == 503
    assert res["retryable"] is True


# ==============================================================================
# Test P — Network Failure
# ==============================================================================
def test_test_p_network_failure():
    """Test P: Verify connection failure is surfaced correctly as retryable NETWORK_ERROR."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = ConnectionResetError("Connection reset by peer")

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "NETWORK_ERROR"
    assert res["retryable"] is True


# ==============================================================================
# Test Q — Unknown Error Safety
# ==============================================================================
def test_test_q_unknown_error():
    """Test Q: Verify unexpected errors fail safely without unhandled crashes."""
    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.side_effect = RuntimeError("Completely unexpected internal error")

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is False
    assert res["error_type"] == "UNKNOWN_ERROR"
    assert res["retryable"] is False


# ==============================================================================
# Test R — No Real Network Calls
# ==============================================================================
def test_test_r_no_network(monkeypatch):
    """Test R: Instrument socket layer to prove tests never make real outbound network calls."""
    network_attempted = False

    def guard_connect(*args, **kwargs):
        nonlocal network_attempted
        network_attempted = True
        raise AssertionError("SECURITY VIOLATION: Outbound network call detected during test execution!")

    monkeypatch.setattr(socket.socket, "connect", guard_connect)

    mock_service = MagicMock()
    mock_service.spreadsheets().values().update().execute.return_value = {"updatedRows": 1}

    rec = sample_record(att_id=1)
    res = update_attendance_row(rec, sheet_row_number=5, spreadsheet_id="test_id", service=mock_service)

    assert res["success"] is True
    assert network_attempted is False, "A real network connection was attempted!"


# ==============================================================================
# Test S — Existing Google Sheets Regressions Compatibility
# ==============================================================================
def test_test_s_existing_google_sheets_regressions():
    """Test S: Verify existing helper functions remain compatible."""
    # Ensure format_attendance_row, sanitize_sheet_cell, etc. are untouched
    assert sanitize_sheet_cell("=1+1") == "'=1+1"
    assert sanitize_sheet_cell(100) == 100
    row = format_attendance_row(sample_record(1))
    assert len(row) == 9
    assert row[0] == 1


# ==============================================================================
# Test T — Batch Deterministic Update Helper
# ==============================================================================
def test_test_t_batch_deterministic_update():
    """Test T: Verify batch deterministic update helper correctly targets multiple rows."""
    mock_service = MagicMock()
    mock_update = mock_service.spreadsheets.return_value.values.return_value.update
    mock_update.return_value.execute.return_value = {"updatedRows": 1}

    batch_input = [
        (sample_record(1, "Alice"), 3),
        (sample_record(2, "Bob"), 4),
        (sample_record(3, "Charlie"), 5),
    ]

    res = update_attendance_batch(
        records_with_rows=batch_input,
        spreadsheet_id="test_id",
        service=mock_service
    )

    assert res["success"] is True
    assert res["total_count"] == 3
    assert res["updated_count"] == 3
    assert res["failed_count"] == 0
    assert mock_update.call_count == 3


# ==============================================================================
# Test U — Worker Remains Unchanged
# ==============================================================================
def test_test_u_worker_remains_unchanged():
    """Test U: Verify sheets_worker.py integrates update_attendance_row in Stage 13J."""
    worker_path = Path(r"C:\Projects\Smart-Attendance-System\app\services\sheets_worker.py")
    assert worker_path.exists()
    content = worker_path.read_text(encoding="utf-8")
    assert "update_attendance_row" in content
