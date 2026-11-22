"""
End-to-End Tests for Attendance Session Constraints & Network Failures.
Validates Scenarios F, G, and H:
- Scenario F: No active class session
- Scenario G: Expired session (reached end_time)
- Scenario H: Server unavailable / connection refused
- Wi-Fi Offline Guard: Local offline scan suppression
"""

from .conftest import (
    run_firmware_client,
    seed_student,
    seed_session,
    get_attendance_count
)


def test_e2e_scenario_f_no_active_session(live_server):
    """
    SCENARIO F — No Active Class Session:
    1. Register student with card UID 'F6A1B2C3'.
    2. Ensure NO session has active = 1 in the database.
    3. Submit scan.
    4. Assert HTTP 409 Conflict and machine-readable 'NO_ACTIVE_SESSION'.
    5. Assert production C++ maps outcome to SCAN_OUTCOME_NO_SESSION.
    6. Assert 0 attendance records created.
    7. Assert OLED display shows 'NO ACTIVE SESSION'.
    8. Assert Buzzer pattern is BUZZER_BEEP_ERROR.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="106", full_name="Ananya Joshi", card_uid="F6A1B2C3")
    # Session exists but is inactive (active=0)
    seed_session(db, course_name="Control Systems", active=0)

    res = run_firmware_client(port=port, card_uid="F6A1B2C3")

    assert res["outcome"] == "SCAN_OUTCOME_NO_SESSION"
    assert res["outcomeName"] == "NO_SESSION"
    assert res["httpCode"] == 409
    assert res["scanResult"] == "NO_ACTIVE_SESSION"
    assert get_attendance_count(db, student_id) == 0

    assert "NO ACTIVE SESSION" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_g_expired_session(live_server):
    """
    SCENARIO G — Expired Class Session:
    1. Register student with card UID 'A2B3C4D5'.
    2. Create a session with end_time in the past (e.g. 00:01 on 2020-01-01).
    3. Submit scan.
    4. Assert HTTP 409 Conflict and machine-readable 'SESSION_EXPIRED'.
    5. Assert production C++ maps outcome to SCAN_OUTCOME_SESSION_EXPIRED.
    6. Assert 0 attendance records created.
    7. Assert OLED display shows 'SESSION EXPIRED'.
    8. Assert Buzzer pattern is BUZZER_BEEP_ERROR.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="107", full_name="Vikram Singh", card_uid="A2B3C4D5")
    seed_session(
        db,
        course_name="Machine Learning",
        session_date="2020-01-01",
        start_time="08:00",
        end_time="09:00",
        active=1
    )

    res = run_firmware_client(port=port, card_uid="A2B3C4D5")

    assert res["outcome"] == "SCAN_OUTCOME_SESSION_EXPIRED"
    assert res["outcomeName"] == "SESSION_EXPIRED"
    assert res["httpCode"] == 409
    assert res["scanResult"] == "SESSION_EXPIRED"
    assert get_attendance_count(db, student_id) == 0

    assert "SESSION ENDED" in res["displayText"]
    assert "The active session" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"


def test_e2e_scenario_h_server_unavailable():
    """
    SCENARIO H — Server Unavailable / Refused Connection:
    1. Direct the C++ firmware client to a port where NO server is listening (59999).
    2. C++ TCP socket connect fails with connection refused.
    3. Production C++ maps outcome to SCAN_OUTCOME_NETWORK_ERROR with httpCode = -1.
    4. Assert OLED display shows 'Server Offline'.
    5. Assert Buzzer pattern is BUZZER_BEEP_NETWORK_ERROR (400ms duration).
    """
    res = run_firmware_client(port=59999, card_uid="A1B2C3D4", extra_args=["--timeout", "1000"])

    assert res["outcome"] == "SCAN_OUTCOME_NETWORK_ERROR"
    assert res["outcomeName"] == "NETWORK_ERROR"
    assert res["httpCode"] == -1
    assert "connection refused" in res["message"]
    assert "Server Offline" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_NETWORK_ERROR"
    assert res["buzzerDurationMs"] == 400


def test_e2e_scenario_offline_wifi(live_server):
    """
    WI-FI OFFLINE GUARD:
    1. Simulate firmware when Wi-Fi link is disconnected (--offline).
    2. Production C++ sendScan() aborts locally before attempting HTTPClient transport.
    3. Assert outcome is SCAN_OUTCOME_NETWORK_ERROR with httpCode = -1.
    4. Assert OLED display shows 'Server Offline'.
    5. Assert Buzzer pattern is BUZZER_BEEP_NETWORK_ERROR.
    """
    port = live_server["port"]
    res = run_firmware_client(port=port, card_uid="A1B2C3D4", extra_args=["--offline"])

    assert res["outcome"] == "SCAN_OUTCOME_NETWORK_ERROR"
    assert res["httpCode"] == -1
    assert "Wi-Fi disconnected" in res["message"]
    assert "Server Offline" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_NETWORK_ERROR"
