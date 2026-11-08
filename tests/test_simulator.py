"""
Automated unit and integration tests for the RFID Scanner Simulator (scripts/simulate_scan.py).

Tests all simulator functionality using mocks without requiring a running Flask server
or physical ESP32/RC522 hardware.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from scripts.simulate_scan import (
    validate_uid,
    send_scan,
    format_scan_result,
    process_single_uid,
    run_interactive,
    main,
    DEFAULT_SERVER_URL,
    DEFAULT_SOURCE,
)


# ==============================================================================
# 1. UID VALIDATION AND NORMALIZATION TESTS
# ==============================================================================

def test_1_valid_uid_normalization_to_uppercase_and_trimmed():
    """Valid UIDs should be trimmed of whitespace and converted to uppercase."""
    is_valid, normalized, err = validate_uid("  a1b2c3d4  ")
    assert is_valid is True
    assert normalized == "A1B2C3D4"
    assert err == ""

    is_valid, normalized, err = validate_uid("rfid-card_99")
    assert is_valid is True
    assert normalized == "RFID-CARD_99"
    assert err == ""


def test_2_empty_or_whitespace_uid_rejected():
    """Empty strings, whitespace-only strings, or None must be rejected locally."""
    is_valid, _, err = validate_uid("")
    assert is_valid is False
    assert "cannot be empty" in err

    is_valid, _, err = validate_uid("     ")
    assert is_valid is False
    assert "cannot be empty" in err

    is_valid, _, err = validate_uid(None)
    assert is_valid is False
    assert "cannot be empty" in err


def test_3_uid_length_boundaries_enforced():
    """UIDs shorter than 4 characters or longer than 50 characters must be rejected."""
    # Too short (< 4 chars)
    is_valid, _, err = validate_uid("ABC")
    assert is_valid is False
    assert "between 4 and 50 characters" in err

    # Valid boundary: 4 characters
    is_valid, norm, err = validate_uid("ABCD")
    assert is_valid is True
    assert norm == "ABCD"

    # Valid boundary: 50 characters
    valid_50 = "A" * 50
    is_valid, norm, err = validate_uid(valid_50)
    assert is_valid is True
    assert norm == valid_50

    # Too long (> 50 chars)
    too_long = "A" * 51
    is_valid, _, err = validate_uid(too_long)
    assert is_valid is False
    assert "between 4 and 50 characters" in err


def test_4_uid_with_invalid_characters_rejected():
    """Characters outside [A-Z0-9_-] such as spaces, symbols, and punctuation must be rejected."""
    invalid_cases = [
        "A1B2 C3D4",    # embedded space
        "CARD@123",     # @ character
        "UID#9999",     # # character
        "HELLO.WORLD",  # dot
        "CARD$100",     # dollar sign
    ]
    for uid in invalid_cases:
        is_valid, _, err = validate_uid(uid)
        assert is_valid is False
        assert "alphanumeric characters, dashes, or underscores" in err


# ==============================================================================
# 2. HTTP RESPONSE INTERPRETATION TESTS (STATUS CODES & ERROR TYPES)
# ==============================================================================

@patch("requests.post")
def test_5_successful_http_201_response_handled(mock_post):
    """HTTP 201 Created should yield a SUCCESS result with attendance details."""
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "success": True,
        "message": "Attendance recorded successfully",
        "attendance": {
            "id": 1,
            "roll_number": "101",
            "student_name": "Rahul Sharma",
            "course_name": "CS101",
            "session_id": 5,
            "scanned_at": "2026-11-07T10:00:00Z",
            "status": "Present",
            "source": "SIMULATOR",
        },
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "A1B2C3D4")

    assert result["success"] is True
    assert result["status_code"] == 201
    assert result["error_type"] is None
    assert result["attendance"]["student_name"] == "Rahul Sharma"
    assert result["attendance"]["roll_number"] == "101"

    formatted = format_scan_result(result)
    assert "[ SUCCESS ] Attendance Recorded!" in formatted
    assert "Student Name:      Rahul Sharma" in formatted
    assert "Roll Number:       101" in formatted
    assert "Attendance Status: Present" in formatted
    assert "Scan Source:       SIMULATOR" in formatted


@patch("requests.post")
def test_6_unknown_card_http_404_handled(mock_post):
    """HTTP 404 should be recognized as an unknown RFID card."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {
        "success": False,
        "error": "Student Not Found",
        "message": "This RFID card is not registered.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "UNKNOWN9999")

    assert result["success"] is False
    assert result["status_code"] == 404
    assert result["error_type"] == "UNKNOWN_CARD"

    formatted = format_scan_result(result)
    assert "[ ERROR ] Unknown RFID Card!" in formatted
    assert "This RFID UID is not registered in the system." in formatted


@patch("requests.post")
def test_7_inactive_student_http_403_handled(mock_post):
    """HTTP 403 should be recognized as an inactive student account."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {
        "success": False,
        "error": "Inactive Student",
        "message": "This student account is inactive.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "INACTIVE01")

    assert result["success"] is False
    assert result["status_code"] == 403
    assert result["error_type"] == "INACTIVE_STUDENT"

    formatted = format_scan_result(result)
    assert "[ ERROR ] Student Account Inactive!" in formatted
    assert "Attendance cannot be recorded for this student." in formatted


@patch("requests.post")
def test_8_duplicate_attendance_http_409_handled(mock_post):
    """HTTP 409 with duplicate error should be distinguished as duplicate attendance."""
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.json.return_value = {
        "success": False,
        "error": "Duplicate Attendance",
        "message": "Attendance has already been recorded for this student in this session.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "A1B2C3D4")

    assert result["success"] is False
    assert result["status_code"] == 409
    assert result["error_type"] == "DUPLICATE_ATTENDANCE"

    formatted = format_scan_result(result)
    assert "[ DUPLICATE ATTENDANCE ]" in formatted
    assert "Attendance has already been recorded for this student in this session." in formatted


@patch("requests.post")
def test_9_no_active_session_http_409_handled(mock_post):
    """HTTP 409 with session error should be distinguished as no active session."""
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.json.return_value = {
        "success": False,
        "error": "No Active Session",
        "message": "No class session is currently active. Attendance cannot be recorded.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "A1B2C3D4")

    assert result["success"] is False
    assert result["status_code"] == 409
    assert result["error_type"] == "NO_ACTIVE_SESSION"

    formatted = format_scan_result(result)
    assert "[ NO ACTIVE SESSION ]" in formatted
    assert "No class session is currently active." in formatted


@patch("requests.post")
def test_10_http_400_validation_error_handled(mock_post):
    """HTTP 400 Bad Request should be formatted as a validation error."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "success": False,
        "error": "Bad Request",
        "message": "Field 'card_uid' must be between 4 and 50 characters.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "BAD_PAYLOAD")

    assert result["success"] is False
    assert result["status_code"] == 400
    assert result["error_type"] == "VALIDATION_ERROR"

    formatted = format_scan_result(result)
    assert "[ VALIDATION ERROR ]" in formatted
    assert "Field 'card_uid' must be between 4 and 50 characters." in formatted


@patch("requests.post")
def test_11_unexpected_http_status_handled(mock_post):
    """Unexpected HTTP status codes (such as 500 or 503) should be safely formatted without crashing."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {
        "success": False,
        "error": "Internal Server Error",
        "message": "Failed to record attendance due to a database error.",
    }
    mock_post.return_value = mock_resp

    result = send_scan(DEFAULT_SERVER_URL, "A1B2C3D4")

    assert result["success"] is False
    assert result["status_code"] == 500
    assert result["error_type"] == "HTTP_ERROR"

    formatted = format_scan_result(result)
    assert "[ ERROR 500 ]" in formatted
    assert "Internal Server Error" in formatted


# ==============================================================================
# 3. NETWORK, TIMEOUT, AND MALFORMED RESPONSE TESTS
# ==============================================================================

@patch("requests.post")
def test_12_connection_error_handled(mock_post):
    """Connection errors (e.g. backend server offline) should be caught cleanly."""
    mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

    result = send_scan("http://127.0.0.1:5000", "A1B2C3D4")

    assert result["success"] is False
    assert result["status_code"] is None
    assert result["error_type"] == "CONNECTION_ERROR"

    formatted = format_scan_result(result)
    assert "[ CONNECTION ERROR ] Cannot connect to attendance server" in formatted
    assert "Please check whether the Flask backend is running" in formatted


@patch("requests.post")
def test_13_request_timeout_handled(mock_post):
    """Request timeouts should be caught and reported gracefully."""
    mock_post.side_effect = requests.exceptions.Timeout("Read timed out")

    result = send_scan("http://127.0.0.1:5000", "A1B2C3D4", timeout=5.0)

    assert result["success"] is False
    assert result["status_code"] is None
    assert result["error_type"] == "TIMEOUT_ERROR"

    formatted = format_scan_result(result)
    assert "[ TIMEOUT ERROR ] Request timed out" in formatted


@patch("requests.post")
def test_14_general_network_error_handled(mock_post):
    """Generic RequestException should be handled safely."""
    mock_post.side_effect = requests.exceptions.RequestException("DNS lookup failed")

    result = send_scan("http://127.0.0.1:5000", "A1B2C3D4")

    assert result["success"] is False
    assert result["error_type"] == "NETWORK_ERROR"
    assert "[ NETWORK ERROR ] Communication failed" in format_scan_result(result)


@patch("requests.post")
def test_15_invalid_non_json_response_handled(mock_post):
    """Non-JSON responses (e.g. HTML 502/504 Bad Gateway pages) should not crash the simulator."""
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = "<html><body>502 Bad Gateway</body></html>"
    mock_resp.json.side_effect = ValueError("Invalid JSON")
    mock_post.return_value = mock_resp

    result = send_scan("http://127.0.0.1:5000", "A1B2C3D4")

    assert result["success"] is False
    assert result["status_code"] == 502
    assert result["error_type"] == "INVALID_JSON"

    formatted = format_scan_result(result)
    assert "[ SERVER ERROR ] Received non-JSON response from server (HTTP 502)" in formatted


# ==============================================================================
# 4. CLI AND EXECUTION MODES TESTS (ONE-SHOT & INTERACTIVE)
# ==============================================================================

@patch("requests.post")
def test_16_one_shot_mode_success_sends_single_request(mock_post, capsys):
    """One-shot mode (--uid) should send a single scan, print the result, and exit with code 0."""
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "success": True,
        "message": "Attendance recorded successfully",
        "attendance": {
            "id": 1,
            "roll_number": "101",
            "student_name": "Rahul Sharma",
            "course_name": "CS101",
            "session_id": 1,
            "scanned_at": "2026-11-07T12:00:00Z",
            "status": "Present",
            "source": "SIMULATOR",
        },
    }
    mock_post.return_value = mock_resp

    exit_code = main(["--uid", "a1b2c3d4"])

    assert exit_code == 0
    assert mock_post.call_count == 1

    # Verify endpoint and payload
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://127.0.0.1:5000/api/scans"
    assert call_args[1]["json"] == {
        "card_uid": "A1B2C3D4",  # normalized to uppercase
        "source": DEFAULT_SOURCE,
    }

    captured = capsys.readouterr()
    assert "[ SUCCESS ] Attendance Recorded!" in captured.out
    assert "Rahul Sharma" in captured.out


@patch("requests.post")
def test_17_one_shot_mode_failure_returns_exit_code_1(mock_post, capsys):
    """One-shot mode should return exit code 1 when the scan fails."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {
        "success": False,
        "error": "Student Not Found",
        "message": "This RFID card is not registered.",
    }
    mock_post.return_value = mock_resp

    exit_code = main(["--uid", "UNKNOWN99"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[ ERROR ] Unknown RFID Card!" in captured.out


def test_18_one_shot_mode_invalid_uid_returns_exit_code_1_without_network_call(capsys):
    """Invalid UID in one-shot mode should reject locally without sending an HTTP request."""
    with patch("requests.post") as mock_post:
        exit_code = main(["--uid", "AB"])  # too short
        assert exit_code == 1
        mock_post.assert_not_called()

    captured = capsys.readouterr()
    assert "[ VALIDATION ERROR ]" in captured.out
    assert "between 4 and 50 characters" in captured.out


@patch("requests.post")
def test_19_custom_url_override_used_in_request(mock_post):
    """The --url argument should override the default backend URL cleanly."""
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "success": True,
        "attendance": {"student_name": "Test", "roll_number": "1", "status": "Present", "source": "SIMULATOR"},
    }
    mock_post.return_value = mock_resp

    exit_code = main(["--url", "http://192.168.1.50:8080/", "--uid", "CARD1234"])
    assert exit_code == 0

    call_args = mock_post.call_args
    assert call_args[0][0] == "http://192.168.1.50:8080/api/scans"


@patch("requests.post")
@patch("builtins.input")
def test_20_interactive_mode_processes_multiple_scans_and_exits(mock_input, mock_post, capsys):
    """Interactive mode should process multiple user inputs in a loop and exit cleanly on EXIT."""
    mock_input.side_effect = ["CARD0001", "CARD0002", "exit"]

    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 201
    mock_resp_1.json.return_value = {
        "success": True,
        "attendance": {"student_name": "Student One", "roll_number": "101", "status": "Present", "source": "SIMULATOR"},
    }

    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 409
    mock_resp_2.json.return_value = {
        "success": False,
        "error": "Duplicate Attendance",
        "message": "Attendance has already been recorded for this student in this session.",
    }

    mock_post.side_effect = [mock_resp_1, mock_resp_2]

    exit_code = main([])  # default interactive mode
    assert exit_code == 0
    assert mock_post.call_count == 2

    # Check payload UIDs
    first_call_payload = mock_post.call_args_list[0][1]["json"]
    second_call_payload = mock_post.call_args_list[1][1]["json"]
    assert first_call_payload["card_uid"] == "CARD0001"
    assert second_call_payload["card_uid"] == "CARD0002"

    captured = capsys.readouterr()
    assert "RFID SCANNER SIMULATOR" in captured.out
    assert "[ SUCCESS ] Attendance Recorded!" in captured.out
    assert "Student One" in captured.out
    assert "[ DUPLICATE ATTENDANCE ]" in captured.out
    assert "Exiting simulator. Goodbye!" in captured.out


@patch("builtins.input")
def test_21_interactive_mode_handles_keyboard_interrupt(mock_input, capsys):
    """Interactive mode should handle KeyboardInterrupt (Ctrl+C) gracefully without crashing."""
    mock_input.side_effect = KeyboardInterrupt()

    exit_code = main([])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Simulator stopped by user. Goodbye!" in captured.out


@patch("builtins.input")
def test_22_interactive_mode_handles_empty_input_gracefully(mock_input, capsys):
    """Interactive mode should prompt again when empty input is submitted without making an HTTP call."""
    mock_input.side_effect = ["", "   ", "QUIT"]

    with patch("requests.post") as mock_post:
        exit_code = main([])
        assert exit_code == 0
        mock_post.assert_not_called()

    captured = capsys.readouterr()
    assert "[ VALIDATION ERROR ] RFID UID cannot be empty" in captured.out
    assert "Exiting simulator. Goodbye!" in captured.out


@patch("requests.post")
def test_23_payload_does_not_contain_client_timestamp(mock_post):
    """The request payload must never contain a client-generated timestamp."""
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "success": True,
        "attendance": {"student_name": "Test", "roll_number": "1", "status": "Present", "source": "SIMULATOR"},
    }
    mock_post.return_value = mock_resp

    send_scan(DEFAULT_SERVER_URL, "A1B2C3D4")

    sent_payload = mock_post.call_args[1]["json"]
    assert "card_uid" in sent_payload
    assert "source" in sent_payload
    assert "scanned_at" not in sent_payload
    assert "timestamp" not in sent_payload
