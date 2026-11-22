"""
End-to-End Tests for Hardware Device Authentication.
Validates Scenario E:
- Scenario E1: Invalid device API key
- Scenario E2: Missing device API key
- Scenario E3: Missing device ID
- Scenario E4: Short device API key (< 32 characters)
- Scenario E5: Unknown device ID
- Scenario E6: Hardware device authentication bypasses CSRF on POST /api/scans
"""

from .conftest import (
    run_firmware_client,
    seed_student,
    seed_session,
    get_attendance_count,
    TEST_DEVICE_ID,
    TEST_API_KEY
)


def test_e2e_scenario_e1_invalid_device_api_key(live_server):
    """
    SCENARIO E1 — Invalid Device API Key:
    1. Send scan with valid device ID but wrong 32-character key.
    2. Flask server rejects request with HTTP 401 Unauthorized.
    3. Production C++ maps outcome to SCAN_OUTCOME_DEVICE_AUTH_ERROR.
    4. Assert OLED display feedback shows 'Device Auth Error'.
    5. Assert Buzzer pattern is BUZZER_BEEP_ERROR.
    6. Assert 0 attendance records created.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_student(db, roll_number="104", full_name="Pooja Rao", card_uid="D4E5F6A1")
    seed_session(db, course_name="Data Structures", active=1)

    wrong_key = "wrong-device-key-32-chars-long-!"
    res = run_firmware_client(port=port, card_uid="D4E5F6A1", api_key=wrong_key)

    # 1. Transport verification: Request actually reached Flask over TCP socket
    assert res["httpTransmitted"] is True
    assert res["postCallCount"] == 1

    # 2. Backend response verification: Real HTTP 401 Unauthorized from Flask
    assert res["httpCode"] == 401
    assert res["error"] == "Unauthorized"
    assert res["outcome"] == "SCAN_OUTCOME_SERVER_ERROR"  # SCAN_OUTCOME_DEVICE_AUTH_ERROR is aliased to SERVER_ERROR
    assert get_attendance_count(db) == 0

    assert "Device Auth Error" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_e2_missing_device_api_key(live_server):
    """
    SCENARIO E2 — Missing Device API Key:
    1. Configure firmware client with empty API key ("").
    2. Production C++ pre-check aborts locally before opening a network socket.
    3. Assert that NO HTTP request was transmitted (postCallCount == 0).
    4. Assert outcome is SCAN_OUTCOME_DEVICE_AUTH_ERROR with local message.
    5. Assert OLED display feedback shows 'Device Auth Error'.
    6. Assert 0 attendance records created.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_student(db, roll_number="104", full_name="Pooja Rao", card_uid="D4E5F6A1")
    seed_session(db, course_name="Data Structures", active=1)

    res = run_firmware_client(port=port, card_uid="D4E5F6A1", api_key="")

    # 1. Transport verification: Aborted locally before transport begins
    assert res["httpTransmitted"] is False
    assert res["postCallCount"] == 0

    # 2. Firmware local error synthesis (no backend packet sent)
    assert res["httpCode"] == 401
    assert res["scanResult"] == "DEVICE_AUTH_ERROR"
    assert res["message"] == "Missing DEVICE_ID or DEVICE_API_KEY in firmware config"
    assert res["error"] == "Device Auth Error"
    assert get_attendance_count(db) == 0
    assert "Device Auth Error" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_e3_missing_device_id(live_server):
    """
    SCENARIO E3 — Missing Device ID:
    1. Configure firmware client with empty device ID ("").
    2. Production C++ pre-check aborts locally before opening a network socket.
    3. Assert that NO HTTP request was transmitted (postCallCount == 0).
    4. Assert outcome is SCAN_OUTCOME_DEVICE_AUTH_ERROR with local message.
    5. Assert OLED display feedback shows 'Device Auth Error'.
    6. Assert 0 attendance records created.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_student(db, roll_number="104", full_name="Pooja Rao", card_uid="D4E5F6A1")
    seed_session(db, course_name="Data Structures", active=1)

    res = run_firmware_client(port=port, card_uid="D4E5F6A1", device_id="")

    # 1. Transport verification: Aborted locally before transport begins
    assert res["httpTransmitted"] is False
    assert res["postCallCount"] == 0

    # 2. Firmware local error synthesis (no backend packet sent)
    assert res["httpCode"] == 401
    assert res["scanResult"] == "DEVICE_AUTH_ERROR"
    assert res["message"] == "Missing DEVICE_ID or DEVICE_API_KEY in firmware config"
    assert res["error"] == "Device Auth Error"
    assert get_attendance_count(db) == 0
    assert "Device Auth Error" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_e4_short_device_api_key(live_server):
    """
    SCENARIO E4 — Short Device API Key:
    1. Pass API key shorter than 32 characters (e.g. 'short_token_123').
    2. Request is transmitted over HTTP to Flask server.
    3. Backend device authentication rejects it with HTTP 401 Unauthorized.
    4. Production C++ maps outcome to SCAN_OUTCOME_DEVICE_AUTH_ERROR.
    5. Assert 0 attendance records created.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_student(db, roll_number="104", full_name="Pooja Rao", card_uid="D4E5F6A1")
    seed_session(db, course_name="Data Structures", active=1)

    res = run_firmware_client(port=port, card_uid="D4E5F6A1", api_key="short_token_123")

    # 1. Transport verification: Request actually transmitted to backend
    assert res["httpTransmitted"] is True
    assert res["postCallCount"] == 1

    # 2. Real HTTP 401 from Flask backend
    assert res["httpCode"] == 401
    assert res["error"] == "Unauthorized"
    assert get_attendance_count(db) == 0
    assert "Device Auth Error" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_e5_unknown_device_id(live_server):
    """
    SCENARIO E5 — Unknown Device ID:
    1. Pass unregistered device ID ('ESP32_UNKNOWN_99').
    2. Request is transmitted over HTTP to Flask server.
    3. Backend device authentication rejects it with HTTP 401.
    4. Production C++ maps outcome to SCAN_OUTCOME_DEVICE_AUTH_ERROR.
    5. Assert 0 attendance records created.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_student(db, roll_number="104", full_name="Pooja Rao", card_uid="D4E5F6A1")
    seed_session(db, course_name="Data Structures", active=1)

    res = run_firmware_client(port=port, card_uid="D4E5F6A1", device_id="ESP32_UNKNOWN_99")

    # 1. Transport verification: Request actually transmitted to backend
    assert res["httpTransmitted"] is True
    assert res["postCallCount"] == 1

    # 2. Real HTTP 401 from Flask backend
    assert res["httpCode"] == 401
    assert res["error"] == "Unauthorized"
    assert get_attendance_count(db) == 0
    assert "Device Auth Error" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_e6_device_auth_bypasses_csrf_for_scans(live_server):
    """
    SCENARIO E6 — Device Auth Bypasses CSRF on POST /api/scans:
    1. With CSRF_ENABLED=True, valid device credentials succeed without providing a CSRF token.
    2. Assert HTTP 201 Created and request transmitted over HTTP.
    3. Assert 1 attendance record created in temporary SQLite.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="105", full_name="Karan Verma", card_uid="E5F6A1B2")
    seed_session(db, course_name="Microprocessors", active=1)

    res = run_firmware_client(port=port, card_uid="E5F6A1B2")

    assert res["httpTransmitted"] is True
    assert res["postCallCount"] == 1
    assert res["httpCode"] == 201
    assert res["outcome"] == "SCAN_OUTCOME_SUCCESS"
    assert get_attendance_count(db, student_id) == 1
