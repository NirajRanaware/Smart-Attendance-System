"""
End-to-End Tests for Attendance Workflows.
Validates Scenarios A, B, C, and D:
- Scenario A: Successful attendance
- Scenario B: Duplicate attendance
- Scenario C: Unknown RFID card
- Scenario D: Inactive student
"""

import sqlite3
from .conftest import (
    run_firmware_client,
    seed_student,
    seed_session,
    get_attendance_count,
    get_sync_queue_records,
    TEST_DEVICE_ID,
    TEST_API_KEY
)


def test_e2e_scenario_a_successful_attendance(live_server):
    """
    SCENARIO A — Successful Attendance Scan:
    1. Seed student with card UID 'A1B2C3D4' (Rahul Sharma, Roll 101).
    2. Seed and activate session 'Embedded Systems'.
    3. Run C++ firmware client sendScan('A1B2C3D4').
    4. Assert HTTP 201 Created and SCAN_OUTCOME_SUCCESS.
    5. Assert parsed student details match database.
    6. Assert 1 attendance record in temporary SQLite with status 'Present' and source 'ESP32_ATTENDANCE_01'.
    7. Assert OLED display feedback contains 'ATTENDANCE' and student details.
    8. Assert Buzzer pattern is BUZZER_BEEP_SUCCESS (240ms duration).
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="101", full_name="Rahul Sharma", card_uid="A1B2C3D4")
    session_id = seed_session(db, course_name="Embedded Systems", active=1)

    # Database state before
    assert get_attendance_count(db, student_id) == 0

    # Execute production C++ firmware client
    res = run_firmware_client(port=port, card_uid="A1B2C3D4")

    # 1. C++ Outcome verification
    assert res["outcome"] == "SCAN_OUTCOME_SUCCESS"
    assert res["outcomeName"] == "SUCCESS"
    assert res["httpCode"] == 201
    assert res["scanResult"] == "SUCCESS"
    assert res["rollNumber"] == "101"
    assert res["studentName"] == "Rahul Sharma"
    assert res["attendanceStatus"] == "Present"

    # 2. SQLite Database state verification
    assert get_attendance_count(db, student_id) == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM attendance WHERE student_id = ?;", (student_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["session_id"] == session_id
    assert row["status"] == "Present"
    assert row["source"] == TEST_DEVICE_ID
    assert "T" in row["scanned_at"] and row["scanned_at"].endswith("Z")

    # 3. Simulated OLED display verification
    assert "STUDENT IDENTIFIED" in res["displayText"]
    assert "Rahul Sharma" in res["displayText"]
    assert "101" in res["displayText"]

    # 4. Simulated Buzzer verification
    assert res["buzzerPattern"] == "BUZZER_BEEP_SUCCESS"
    assert res["buzzerDurationMs"] == 240


def test_e2e_scenario_b_duplicate_attendance(live_server):
    """
    SCENARIO B — Duplicate Attendance Scan:
    1. Perform initial successful scan for Rahul Sharma.
    2. Resubmit the exact same card UID in the same active session.
    3. Assert HTTP 409 Conflict and SCAN_OUTCOME_DUPLICATE.
    4. Assert attendance count remains strictly 1 (no duplicate row).
    5. Assert OLED display feedback contains 'ALREADY MARKED'.
    6. Assert Buzzer pattern is BUZZER_BEEP_DUPLICATE (380ms duration).
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="102", full_name="Sneha Patil", card_uid="B2C3D4E5")
    seed_session(db, course_name="Digital Signal Processing", active=1)

    # First scan -> Success
    res1 = run_firmware_client(port=port, card_uid="B2C3D4E5")
    assert res1["httpCode"] == 201
    assert res1["outcome"] == "SCAN_OUTCOME_SUCCESS"
    assert get_attendance_count(db, student_id) == 1
    assert len(get_sync_queue_records(db)) == 1

    # Second scan -> Duplicate (same student UID in same active session)
    res2 = run_firmware_client(port=port, card_uid="B2C3D4E5")
    assert res2["httpCode"] == 409
    assert res2["scanResult"] == "DUPLICATE"
    assert res2["outcome"] == "SCAN_OUTCOME_DUPLICATE"
    assert res2["outcomeName"] == "DUPLICATE"
    assert res2["rollNumber"] == "102"
    assert res2["studentName"] == "Sneha Patil"

    # Confirm attendance row count and sync queue count remain strictly 1
    assert get_attendance_count(db, student_id) == 1
    assert len(get_sync_queue_records(db)) == 1

    # Simulated Hardware Feedback verification
    assert "DUPLICATE SCAN" in res2["displayText"]
    assert "ALREADY PRESENT" in res2["displayText"]
    assert res2["buzzerPattern"] == "BUZZER_BEEP_DUPLICATE"
    assert res2["buzzerDurationMs"] == 380


def test_e2e_scenario_c_unknown_rfid_card(live_server):
    """
    SCENARIO C — Unknown RFID Card:
    1. Ensure no student is registered with UID 'DEADBEEF'.
    2. Submit card scan.
    3. Assert HTTP 404 Not Found and SCAN_OUTCOME_UNKNOWN_CARD.
    4. Assert 0 attendance records created.
    5. Assert OLED display shows 'UNKNOWN CARD' and 'Card not registered!'.
    6. Assert Buzzer pattern is BUZZER_BEEP_ERROR (280ms duration).
    """
    db = live_server["db_path"]
    port = live_server["port"]

    seed_session(db, course_name="Robotics Engineering", active=1)

    res = run_firmware_client(port=port, card_uid="DEADBEEF")

    assert res["outcome"] == "SCAN_OUTCOME_UNKNOWN_CARD"
    assert res["outcomeName"] == "UNKNOWN_CARD"
    assert res["httpCode"] == 404
    assert res["scanResult"] == "UNKNOWN_CARD"
    assert get_attendance_count(db) == 0

    assert "UNKNOWN CARD" in res["displayText"]
    assert "Card not registered!" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"
    assert res["buzzerDurationMs"] == 280


def test_e2e_scenario_d_inactive_student(live_server):
    """
    SCENARIO D — Inactive Student Account:
    1. Register student with active=0 (deactivated).
    2. Submit card scan.
    3. Assert HTTP 403 Forbidden and SCAN_OUTCOME_INACTIVE_STUDENT.
    4. Assert 0 attendance records created.
    5. Assert OLED display shows 'ACCOUNT INACTIVE' and 'Student account'.
    6. Assert Buzzer pattern is BUZZER_BEEP_ERROR (280ms duration).
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="103", full_name="Aarav Mehta", card_uid="C3D4E5F6", active=0)
    seed_session(db, course_name="Robotics Engineering", active=1)

    res = run_firmware_client(port=port, card_uid="C3D4E5F6")

    assert res["outcome"] == "SCAN_OUTCOME_INACTIVE_STUDENT"
    assert res["outcomeName"] == "INACTIVE_STUDENT"
    assert res["httpCode"] == 403
    assert res["scanResult"] == "INACTIVE_STUDENT"
    assert get_attendance_count(db, student_id) == 0

    assert "ACCOUNT INACTIVE" in res["displayText"]
    assert "Student account" in res["displayText"]
    assert res["buzzerPattern"] == "BUZZER_BEEP_ERROR"
    assert res["buzzerDurationMs"] == 280
