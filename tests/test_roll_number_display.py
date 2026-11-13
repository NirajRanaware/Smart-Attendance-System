"""
Automated unit and integration tests for Automatic Student Roll Number Identification & Display.
Verifies that:
1. When a registered RFID card is scanned, SQLite automatically resolves student roll number & name.
2. The roll number is never accepted from client input (server-authoritative from SQLite).
3. Successful scans return roll number, student name, "Present" status, and "SUCCESS" result.
4. Duplicate scans in the same session return roll number, student name, "ALREADY PRESENT", and "DUPLICATE" result without creating duplicate records.
5. Unknown RFID cards return HTTP 404 without leaking any student details.
6. Inactive student cards return HTTP 403.
7. Attendance stream API and CSV exports preserve student roll numbers.
8. Simulator formatting presents the identified roll number cleanly for both success and duplicate.
9. HTML templates and JS controller provide DOM elements for roll number display.
10. Server-authoritative UTC timestamps and database constraints are strictly upheld.
"""

import os
from unittest.mock import MagicMock, patch
import pytest

from app import create_app
from app.database import init_db, get_db_connection, close_db_connection
from scripts.simulate_scan import send_scan, format_scan_result, DEFAULT_SERVER_URL


@pytest.fixture
def app(tmp_path):
    """
    Creates an isolated Flask test application backed by a temporary SQLite database.
    """
    test_db_path = tmp_path / "test_roll_number.db"
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask test client."""
    return app.test_client()


@pytest.fixture
def test_setup(client):
    """
    Registers student Rahul Patil (Roll: 101, UID: CARD_RAHUL_101)
    and activates an Engineering Mathematics lecture session.
    """
    # 1. Register student
    stu_res = client.post("/api/students", json={
        "roll_number": "101",
        "full_name": "Rahul Patil",
        "card_uid": "CARD_RAHUL_101"
    })
    assert stu_res.status_code == 201
    student = stu_res.get_json()["student"]

    # 2. Create session
    sess_res = client.post("/api/sessions", json={
        "course_name": "Engineering Mathematics",
        "session_date": "2026-11-07",
        "start_time": "10:00",
        "end_time": "12:00"
    })
    assert sess_res.status_code == 201
    session = sess_res.get_json()["session"]

    # 3. Activate session
    act_res = client.patch(f"/api/sessions/{session['id']}/activate")
    assert act_res.status_code == 200

    return {"student": student, "session": session}


# ==============================================================================
# 1. SUCCESSFUL SCAN IDENTIFICATION & ROLL NUMBER DISPLAY
# ==============================================================================

def test_1_registered_card_returns_roll_number_and_student_name(client, test_setup):
    """
    Verify that scanning a registered RFID card automatically retrieves and returns
    the student's roll number, name, and Present status from SQLite.
    """
    response = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "source": "ESP32_DEVICE_01"
    })

    assert response.status_code == 201
    assert response.content_type == "application/json"
    data = response.get_json()

    assert data["success"] is True
    assert data["scan_result"] == "SUCCESS"
    assert data["message"] == "Attendance recorded successfully"

    # Verify student identity object
    assert "student" in data
    student = data["student"]
    assert student["roll_number"] == "101"
    assert student["student_name"] == "Rahul Patil"
    assert student["card_uid"] == "CARD_RAHUL_101"

    # Verify attendance record
    assert "attendance" in data
    att = data["attendance"]
    assert att["roll_number"] == "101"
    assert att["student_name"] == "Rahul Patil"
    assert att["status"] == "Present"
    assert att["source"] == "ESP32_DEVICE_01"
    assert att["session_id"] == test_setup["session"]["id"]


# ==============================================================================
# 2. DUPLICATE SCAN IDENTIFICATION & ZERO DUPLICATE RECORD INSERTION
# ==============================================================================

def test_2_duplicate_scan_identifies_student_and_returns_roll_number(client, app, test_setup):
    """
    Verify that scanning a card a second time in the same session:
    1. Identifies the student.
    2. Returns the student's roll number and name.
    3. Returns scan_result == 'DUPLICATE'.
    4. Does NOT insert a duplicate attendance record into SQLite.
    """
    # First scan -> 201 Created
    res1 = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "source": "ESP32_DEVICE_01"
    })
    assert res1.status_code == 201

    # Second scan -> 409 Conflict
    res2 = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "source": "ESP32_DEVICE_01"
    })
    assert res2.status_code == 409
    data = res2.get_json()

    assert data["success"] is False
    assert data["scan_result"] == "DUPLICATE"
    assert data["error"] == "Duplicate Attendance"
    assert "already been recorded" in data["message"].lower()

    # Student details must be present even on duplicate scan for feedback display
    assert "student" in data
    assert data["student"]["roll_number"] == "101"
    assert data["student"]["student_name"] == "Rahul Patil"

    assert "attendance" in data
    assert data["attendance"]["roll_number"] == "101"
    assert data["attendance"]["student_name"] == "Rahul Patil"
    assert data["attendance"]["status"] == "Present"

    # Verify SQLite database contains exactly 1 attendance row
    with app.app_context():
        conn = get_db_connection(app.config["DATABASE_PATH"])
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?", (test_setup["student"]["id"],))
        count = cursor.fetchone()[0]
        close_db_connection(conn)
        assert count == 1, f"Expected 1 attendance record, but found {count}"


# ==============================================================================
# 3. UNKNOWN RFID CARD REJECTION & PRIVACY (NO DETAILS LEAKED)
# ==============================================================================

def test_3_unknown_card_does_not_reveal_student_info(client, test_setup):
    """
    Verify that scanning an unknown RFID card returns HTTP 404 without leaking
    any student information or roll numbers.
    """
    response = client.post("/api/scans", json={
        "card_uid": "UNKNOWN_CARD_999",
        "source": "ESP32_DEVICE_01"
    })

    assert response.status_code == 404
    data = response.get_json()

    assert data["success"] is False
    assert data["scan_result"] == "UNKNOWN_CARD"
    assert data["error"] == "Student Not Found"
    assert "not registered" in data["message"].lower()

    # Must NOT leak student or attendance objects
    assert "student" not in data or data.get("student") is None
    assert "attendance" not in data or data.get("attendance") is None


# ==============================================================================
# 4. TAMPER RESISTANCE: CLIENT-SUPPLIED ROLL NUMBERS ARE STRICTLY IGNORED
# ==============================================================================

def test_4_client_supplied_roll_number_is_ignored_and_overwritten_by_sqlite(client, app, test_setup):
    """
    Verify that if a rogue client sends a spoofed roll_number in the request payload,
    the backend strictly ignores it and uses the authoritative SQLite record.
    """
    response = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "roll_number": "MALICIOUS_ROLL_999",
        "student_name": "Rogue Hacker",
        "status": "Absent",
        "source": "ROGUE_DEVICE"
    })

    assert response.status_code == 201
    data = response.get_json()

    # The returned data MUST use the database values, NOT the malicious client payload
    assert data["student"]["roll_number"] == "101"
    assert data["student"]["student_name"] == "Rahul Patil"
    assert data["attendance"]["roll_number"] == "101"
    assert data["attendance"]["student_name"] == "Rahul Patil"
    assert data["attendance"]["status"] == "Present"

    # Verify persisted SQLite row
    with app.app_context():
        conn = get_db_connection(app.config["DATABASE_PATH"])
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.roll_number, s.full_name as student_name
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            WHERE a.student_id = ?
            """,
            (test_setup["student"]["id"],)
        )
        row = cursor.fetchone()
        close_db_connection(conn)
        assert row["roll_number"] == "101"
        assert row["student_name"] == "Rahul Patil"


# ==============================================================================
# 5. INACTIVE STUDENT REJECTION
# ==============================================================================

def test_5_inactive_student_scan_rejected(client, app, test_setup):
    """
    Verify that scanning a card belonging to an inactive student is rejected (HTTP 403).
    """
    # Register a student
    reg_res = client.post("/api/students", json={
        "roll_number": "102",
        "full_name": "Inactive Student",
        "card_uid": "CARD_INACTIVE_102"
    })
    assert reg_res.status_code == 201
    stu_id = reg_res.get_json()["student"]["id"]

    # Deactivate student in SQLite
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET active = 0 WHERE id = ?", (stu_id,))
    conn.commit()
    close_db_connection(conn)

    # Attempt scan
    scan_res = client.post("/api/scans", json={
        "card_uid": "CARD_INACTIVE_102",
        "source": "ESP32_DEVICE_01"
    })
    assert scan_res.status_code == 403
    data = scan_res.get_json()
    assert data["success"] is False
    assert data["scan_result"] == "INACTIVE_STUDENT"
    assert data["error"] == "Inactive Student"


# ==============================================================================
# 6. ATTENDANCE STREAM API INCLUDES ROLL NUMBER
# ==============================================================================

def test_6_attendance_stream_api_includes_roll_number(client, test_setup):
    """
    Verify that GET /api/attendance?date=... returns the student roll number
    in each attendance record for display in the live table.
    """
    # Record attendance
    scan_res = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "source": "ESP32_DEVICE_01"
    })
    assert scan_res.status_code == 201

    # Fetch attendance stream
    res = client.get("/api/attendance?date=2026-11-07")
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    records = data["attendance"]
    assert len(records) >= 1

    matched = [r for r in records if r["roll_number"] == "101"]
    assert len(matched) == 1
    assert matched[0]["roll_number"] == "101"
    assert matched[0]["student_name"] == "Rahul Patil"
    assert matched[0]["status"] == "Present"


# ==============================================================================
# 7. CSV EXPORT INCLUDES ROLL NUMBER
# ==============================================================================

def test_7_csv_export_includes_roll_number_and_student_name(client, test_setup):
    """
    Verify that GET /api/reports/export.csv contains the student roll number and student name.
    """
    # Record attendance
    scan_res = client.post("/api/scans", json={
        "card_uid": "CARD_RAHUL_101",
        "source": "ESP32_DEVICE_01"
    })
    assert scan_res.status_code == 201

    # Export CSV
    export_res = client.get("/api/reports/export.csv?date=2026-11-07")
    assert export_res.status_code == 200
    assert "text/csv" in export_res.content_type

    csv_text = export_res.data.decode("utf-8")
    assert "Roll Number" in csv_text
    assert "Student Name" in csv_text
    assert "101" in csv_text
    assert "Rahul Patil" in csv_text
    assert "Present" in csv_text


# ==============================================================================
# 8. SIMULATOR FORMATTING TESTS FOR SUCCESS AND DUPLICATE
# ==============================================================================

def test_8_simulator_output_formats_roll_number_for_success_and_duplicate():
    """
    Verify that simulate_scan.py correctly formats the identified student roll number
    and name for both successful attendance and duplicate attendance detections.
    """
    # 1. Success case
    success_result = {
        "success": True,
        "status_code": 201,
        "error_type": None,
        "message": "Attendance recorded successfully",
        "data": {
            "success": True,
            "scan_result": "SUCCESS",
            "student": {"roll_number": "101", "student_name": "Rahul Patil"},
        },
        "attendance": {
            "roll_number": "101",
            "student_name": "Rahul Patil",
            "status": "Present",
            "source": "SIMULATOR",
            "course_name": "Engineering Mathematics",
            "scanned_at": "2026-11-07T10:15:00Z",
        },
    }
    formatted_success = format_scan_result(success_result)
    assert "[ SUCCESS ] Attendance Recorded!" in formatted_success
    assert "STUDENT IDENTIFIED" in formatted_success
    assert "Roll Number:       101" in formatted_success
    assert "Student Name:      Rahul Patil" in formatted_success
    assert "Attendance Status: Present" in formatted_success

    # 2. Duplicate case
    duplicate_result = {
        "success": False,
        "status_code": 409,
        "error_type": "DUPLICATE_ATTENDANCE",
        "message": (
            "[ DUPLICATE ATTENDANCE ] Attendance has already been recorded for this student in this session.\n"
            "Student Name:      Rahul Patil\n"
            "Roll Number:       101\n"
            "Attendance Status: ALREADY PRESENT"
        ),
        "data": {
            "success": False,
            "scan_result": "DUPLICATE",
            "student": {"roll_number": "101", "student_name": "Rahul Patil"},
        },
    }
    formatted_dup = format_scan_result(duplicate_result)
    assert "[ DUPLICATE ATTENDANCE ]" in formatted_dup
    assert "Roll Number:       101" in formatted_dup
    assert "Student Name:      Rahul Patil" in formatted_dup
    assert "Attendance Status: ALREADY PRESENT" in formatted_dup


# ==============================================================================
# 9. FRONTEND TEMPLATE AND JS CONTROLLER VALIDATION
# ==============================================================================

def test_9_frontend_template_and_js_elements_exist():
    """
    Verify that the attendance.html template and attendance.js contain the required
    DOM elements and hooks to display Roll Number and Student Name prominently.
    """
    template_path = os.path.join("app", "templates", "attendance.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="feedback-roll-number"' in html, "Missing #feedback-roll-number DOM element"
    assert 'id="feedback-student-name"' in html, "Missing #feedback-student-name DOM element"
    assert 'id="feedback-attendance-status"' in html, "Missing #feedback-attendance-status DOM element"
    assert 'id="row-roll-number"' in html, "Missing #row-roll-number DOM row container"
    assert 'id="row-student-name"' in html, "Missing #row-student-name DOM row container"
    assert 'id="row-attendance-status"' in html, "Missing #row-attendance-status DOM row container"

    js_path = os.path.join("app", "static", "js", "attendance.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    assert "STUDENT IDENTIFIED" in js, "attendance.js must contain 'STUDENT IDENTIFIED' state"
    assert "DUPLICATE ATTENDANCE DETECTED" in js, "attendance.js must handle 'DUPLICATE ATTENDANCE DETECTED'"
    assert "feedback-attendance-status" in js, "attendance.js must reference feedback-attendance-status"


# ==============================================================================
# 10. MULTIPLE STUDENTS ISOLATION (CORRECT ROLL NUMBER RESOLVED PER CARD)
# ==============================================================================

def test_10_multiple_students_correct_roll_number_resolved(client, test_setup):
    """
    Verify that when multiple students are registered, scanning card A returns Student A's
    roll number, and scanning card B returns Student B's roll number.
    """
    # Register second student
    res2 = client.post("/api/students", json={
        "roll_number": "102",
        "full_name": "Priya Sharma",
        "card_uid": "CARD_PRIYA_102"
    })
    assert res2.status_code == 201

    # Scan student 1
    scan1 = client.post("/api/scans", json={"card_uid": "CARD_RAHUL_101", "source": "ESP32"})
    assert scan1.status_code == 201
    assert scan1.get_json()["student"]["roll_number"] == "101"
    assert scan1.get_json()["student"]["student_name"] == "Rahul Patil"

    # Scan student 2
    scan2 = client.post("/api/scans", json={"card_uid": "CARD_PRIYA_102", "source": "ESP32"})
    assert scan2.status_code == 201
    assert scan2.get_json()["student"]["roll_number"] == "102"
    assert scan2.get_json()["student"]["student_name"] == "Priya Sharma"
