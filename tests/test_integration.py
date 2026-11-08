"""
End-to-End Integration Tests for the IoT-Based Smart Attendance Management System.

Covers complete multi-step workflows verifying end-to-end reliability across
student registration, session management, RFID scanning, reporting, and CSV export.
Runs on an isolated test database.
"""

import csv
import io
import pytest
from app import create_app
from app.database import init_db, get_db_connection, close_db_connection


@pytest.fixture
def app(tmp_path):
    """Creates a test Flask app instance with an isolated SQLite database."""
    test_db_path = tmp_path / "integration_test.db"
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's test client."""
    return app.test_client()


# ==============================================================================
# WORKFLOW A: SUCCESSFUL ATTENDANCE LIFECYCLE
# ==============================================================================

def test_workflow_a_successful_attendance(client, app):
    """
    Workflow A:
    1. Initialize test database.
    2. Register a student.
    3. Create a class session.
    4. Activate the session.
    5. Submit an RFID scan.
    6. Verify HTTP 201.
    7. Verify the attendance record exists in SQLite.
    8. Request the daily attendance report.
    9. Verify the student is marked Present.
    10. Export the CSV.
    11. Verify the student appears as Present.
    """
    date_str = "2026-11-08"

    # 1. Register student
    res_student = client.post("/api/students", json={
        "roll_number": "CS-101",
        "full_name": "Aarav Sharma",
        "card_uid": "A1B2C3D4"
    })
    assert res_student.status_code == 201
    student = res_student.get_json()["student"]
    assert student["roll_number"] == "CS-101"

    # 2. Create class session
    res_sess = client.post("/api/sessions", json={
        "course_name": "Embedded Systems",
        "session_date": date_str,
        "start_time": "09:30", "end_time": "23:59"})
    assert res_sess.status_code == 201
    sess_id = res_sess.get_json()["session"]["id"]

    # 3. Activate class session
    res_act = client.patch(f"/api/sessions/{sess_id}/activate")
    assert res_act.status_code == 200
    assert res_act.get_json()["session"]["active"] is True

    # 4. Submit RFID scan
    res_scan = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "SIMULATOR"
    })
    assert res_scan.status_code == 201
    scan_data = res_scan.get_json()
    assert scan_data["success"] is True
    assert scan_data["attendance"]["student_name"] == "Aarav Sharma"
    assert scan_data["attendance"]["status"] == "Present"

    # 5. Direct SQLite verification
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM attendance WHERE session_id = ?;", (sess_id,))
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["student_id"] == student["id"]
    assert rows[0]["status"] == "Present"
    close_db_connection(conn)

    # 6. Request daily attendance report
    res_report = client.get(f"/api/reports/daily?date={date_str}")
    assert res_report.status_code == 200
    report = res_report.get_json()
    assert report["summary"]["total_present"] == 1
    assert report["summary"]["total_absent"] == 0
    assert report["summary"]["overall_attendance_percentage"] == 100.0

    # 7. Export CSV
    res_csv = client.get(f"/api/reports/export.csv?date={date_str}")
    assert res_csv.status_code == 200
    reader = csv.DictReader(io.StringIO(res_csv.get_data(as_text=True)))
    csv_rows = list(reader)
    assert len(csv_rows) == 1
    assert csv_rows[0]["Roll Number"] == "CS-101"
    assert csv_rows[0]["Student Name"] == "Aarav Sharma"
    assert csv_rows[0]["Attendance Status"] == "Present"
    assert csv_rows[0]["Source"] == "SIMULATOR"


# ==============================================================================
# WORKFLOW B: DUPLICATE SCAN PREVENTION
# ==============================================================================

def test_workflow_b_duplicate_scan_prevention(client, app):
    """
    Workflow B:
    1. Register a student.
    2. Create and activate a session.
    3. Submit the first scan (201).
    4. Submit the same scan again (409 Conflict).
    5. Verify the second scan is rejected.
    6. Verify only one attendance record exists in SQLite.
    """
    client.post("/api/students", json={
        "roll_number": "CS-102",
        "full_name": "Diya Patel",
        "card_uid": "E5F6A7B8"
    })
    res_sess = client.post("/api/sessions", json={
        "course_name": "Operating Systems",
        "session_date": "2026-11-08",
        "start_time": "11:00", "end_time": "23:59"})
    sess_id = res_sess.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Scan 1: Must succeed
    res_scan1 = client.post("/api/scans", json={"card_uid": "E5F6A7B8", "source": "SIMULATOR"})
    assert res_scan1.status_code == 201

    # Scan 2: Must be rejected as duplicate
    res_scan2 = client.post("/api/scans", json={"card_uid": "E5F6A7B8", "source": "SIMULATOR"})
    assert res_scan2.status_code == 409
    data2 = res_scan2.get_json()
    assert data2["success"] is False
    assert data2["error"] == "Duplicate Attendance"

    # SQLite check: exactly 1 attendance row exists
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM attendance WHERE session_id = ?;", (sess_id,))
    assert cursor.fetchone()["total"] == 1
    close_db_connection(conn)


# ==============================================================================
# WORKFLOW C: ABSENT STUDENT ROSTER INCLUSION
# ==============================================================================

def test_workflow_c_absent_student_roster(client):
    """
    Workflow C:
    1. Register two students.
    2. Create and activate a session.
    3. Scan only the first student's card.
    4. Request the daily report: verify 1 Present, 1 Absent, 50% attendance.
    5. Verify the CSV contains both students (one Present with timestamp, one Absent with blank timestamp).
    """
    date_str = "2026-11-09"
    client.post("/api/students", json={"roll_number": "CS-201", "full_name": "Present Student", "card_uid": "CARD_PRES"})
    client.post("/api/students", json={"roll_number": "CS-202", "full_name": "Absent Student", "card_uid": "CARD_ABS"})

    res_sess = client.post("/api/sessions", json={
        "course_name": "Data Structures",
        "session_date": date_str,
        "start_time": "14:00", "end_time": "23:59"})
    sess_id = res_sess.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Scan only Student 1
    res_scan = client.post("/api/scans", json={"card_uid": "CARD_PRES", "source": "SIMULATOR"})
    assert res_scan.status_code == 201

    # Check daily report
    res_rep = client.get(f"/api/reports/daily?date={date_str}")
    stats = res_rep.get_json()["summary"]
    assert stats["total_present"] == 1
    assert stats["total_absent"] == 1
    assert stats["overall_attendance_percentage"] == 50.0

    # Check CSV export
    res_csv = client.get(f"/api/reports/export.csv?date={date_str}")
    reader = csv.DictReader(io.StringIO(res_csv.get_data(as_text=True)))
    rows = list(reader)
    assert len(rows) == 2

    pres_row = next(r for r in rows if r["Roll Number"] == "CS-201")
    assert pres_row["Attendance Status"] == "Present"
    assert pres_row["Scanned At (UTC)"] != ""

    abs_row = next(r for r in rows if r["Roll Number"] == "CS-202")
    assert abs_row["Attendance Status"] == "Absent"
    assert abs_row["Scanned At (UTC)"] == ""
    assert abs_row["Source"] == ""


# ==============================================================================
# WORKFLOW D: INACTIVE STUDENT ATTENDANCE REJECTION & EXCLUSION
# ==============================================================================

def test_workflow_d_inactive_student_handling(client, app):
    """
    Workflow D:
    1. Register a student.
    2. Deactivate the student.
    3. Create and activate a session.
    4. Attempt an RFID scan -> HTTP 403 Forbidden.
    5. Verify the student is excluded from active roster calculations in reports and CSV.
    """
    date_str = "2026-11-10"
    res_st = client.post("/api/students", json={
        "roll_number": "CS-301",
        "full_name": "Withdrawn Student",
        "card_uid": "CARD_WITHDRAWN"
    })
    student_id = res_st.get_json()["student"]["id"]

    # Deactivate student in SQLite
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET active = 0 WHERE id = ?;", (student_id,))
    conn.commit()
    close_db_connection(conn)

    # Create & activate session
    res_sess = client.post("/api/sessions", json={
        "course_name": "Software Engineering",
        "session_date": date_str,
        "start_time": "10:00", "end_time": "23:59"})
    sess_id = res_sess.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Scan inactive student -> must be rejected with HTTP 403
    res_scan = client.post("/api/scans", json={"card_uid": "CARD_WITHDRAWN", "source": "SIMULATOR"})
    assert res_scan.status_code == 403
    assert res_scan.get_json()["error"] == "Inactive Student"

    # Check daily report: 0 active students enrolled, 0 present, 0 absent
    res_rep = client.get(f"/api/reports/daily?date={date_str}")
    stats = res_rep.get_json()["summary"]
    assert stats["total_enrollments"] == 0
    assert stats["total_present"] == 0
    assert stats["total_absent"] == 0

    # Check CSV export: 0 student rows (only header)
    res_csv = client.get(f"/api/reports/export.csv?date={date_str}")
    reader = csv.reader(io.StringIO(res_csv.get_data(as_text=True)))
    csv_rows = list(reader)
    assert len(csv_rows) == 1  # header only


# ==============================================================================
# WORKFLOW E: SESSION SWITCHING & MULTI-SESSION ISOLATION
# ==============================================================================

def test_workflow_e_session_switching_and_isolation(client):
    """
    Workflow E:
    1. Create two class sessions (Morning and Afternoon).
    2. Register a student.
    3. Activate Session 1 and record attendance.
    4. Activate Session 2 (Session 1 automatically deactivates).
    5. Record attendance for Session 2.
    6. Verify that each attendance record belongs to the correct session.
    7. Verify that duplicate attendance is prevented independently per session.
    """
    date_str = "2026-11-11"
    client.post("/api/students", json={"roll_number": "CS-401", "full_name": "Multi-Session Student", "card_uid": "CARD_MULTI"})

    # Session 1 (Morning)
    s1_res = client.post("/api/sessions", json={"course_name": "Morning Lecture", "session_date": date_str, "start_time": "09:00", "end_time": "23:59"})
    s1_id = s1_res.get_json()["session"]["id"]

    # Session 2 (Afternoon)
    s2_res = client.post("/api/sessions", json={"course_name": "Afternoon Lab", "session_date": date_str, "start_time": "14:00", "end_time": "23:59"})
    s2_id = s2_res.get_json()["session"]["id"]

    # Activate Session 1
    client.patch(f"/api/sessions/{s1_id}/activate")
    scan1 = client.post("/api/scans", json={"card_uid": "CARD_MULTI", "source": "SIMULATOR"})
    assert scan1.status_code == 201
    assert scan1.get_json()["attendance"]["session_id"] == s1_id

    # Duplicate scan in Session 1 is rejected
    dup1 = client.post("/api/scans", json={"card_uid": "CARD_MULTI", "source": "SIMULATOR"})
    assert dup1.status_code == 409

    # Switch to Session 2 (activating Session 2 deactivates Session 1)
    client.patch(f"/api/sessions/{s2_id}/activate")

    # Verify Session 2 is now active and Session 1 is deactivated
    act_sess = client.get("/api/sessions/active").get_json()["session"]
    assert act_sess["id"] == s2_id

    # Scan for Session 2 must succeed
    scan2 = client.post("/api/scans", json={"card_uid": "CARD_MULTI", "source": "SIMULATOR"})
    assert scan2.status_code == 201
    assert scan2.get_json()["attendance"]["session_id"] == s2_id

    # Duplicate scan in Session 2 is rejected
    dup2 = client.post("/api/scans", json={"card_uid": "CARD_MULTI", "source": "SIMULATOR"})
    assert dup2.status_code == 409

    # Total attendance records for this date is 2
    att_res = client.get(f"/api/attendance?date={date_str}")
    records = att_res.get_json()["attendance"]
    assert len(records) == 2
    sess_ids = {r["session_id"] for r in records}
    assert sess_ids == {s1_id, s2_id}
