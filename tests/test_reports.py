"""
Automated unit and integration tests for Attendance Reports and CSV Export API.
Runs against an isolated temporary SQLite database.
"""

import csv
import io
import pytest
from app import create_app
from app.database import init_db, get_db_connection, close_db_connection


@pytest.fixture
def app(tmp_path):
    """
    Creates an instance of the Flask application configured for testing.
    Uses an isolated temporary SQLite database for each test function.
    """
    test_db_path = tmp_path / "test_reports_app.db"
    
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's test client."""
    return app.test_client()


@pytest.fixture
def populated_day(client):
    """
    Sets up a realistic test scenario for 2026-11-07:
    - 3 registered active students:
      1. Rahul Sharma (Roll: 101, UID: CARD_101)
      2. Priya Patel (Roll: 102, UID: CARD_102)
      3. Amit Kumar (Roll: 103, UID: CARD_103)
    - 1 registered inactive student:
      4. Inactive Student (Roll: 104, UID: CARD_104)
    - 2 sessions on 2026-11-07:
      Session 1: Engineering Mathematics at 10:00
      Session 2: Physics at 12:00
    - Attendance on 2026-11-07:
      Session 1: Rahul and Priya present (2 present, 1 absent)
      Session 2: Rahul and Amit present (2 present, 1 absent)
    """
    # 1. Register active students
    s1 = client.post("/api/students", json={"roll_number": "101", "full_name": "Rahul Sharma", "card_uid": "CARD_101"}).get_json()["student"]
    s2 = client.post("/api/students", json={"roll_number": "102", "full_name": "Priya Patel", "card_uid": "CARD_102"}).get_json()["student"]
    s3 = client.post("/api/students", json={"roll_number": "103", "full_name": "Amit Kumar", "card_uid": "CARD_103"}).get_json()["student"]
    s4 = client.post("/api/students", json={"roll_number": "104", "full_name": "Inactive Student", "card_uid": "CARD_104"}).get_json()["student"]
    
    # Deactivate student 4 directly in database
    from app.database import get_db_connection, close_db_connection
    conn = get_db_connection(client.application.config["DATABASE_PATH"])
    conn.execute("UPDATE students SET active = 0 WHERE id = ?;", (s4["id"],))
    conn.commit()
    close_db_connection(conn)
    
    # 2. Create Session 1 (Morning)
    sess1 = client.post("/api/sessions", json={
        "course_name": "Engineering Mathematics",
        "session_date": "2026-11-07",
        "start_time": "10:00", "end_time": "23:59"}).get_json()["session"]
    
    # 3. Create Session 2 (Noon)
    sess2 = client.post("/api/sessions", json={
        "course_name": "Physics",
        "session_date": "2026-11-07",
        "start_time": "12:00", "end_time": "23:59"}).get_json()["session"]
    
    # 4. Record attendance for Session 1
    client.patch(f"/api/sessions/{sess1['id']}/activate")
    client.post("/api/scans", json={"card_uid": "CARD_101", "source": "ESP32"})
    client.post("/api/scans", json={"card_uid": "CARD_102", "source": "ESP32"})
    
    # 5. Record attendance for Session 2
    client.patch(f"/api/sessions/{sess2['id']}/activate")
    client.post("/api/scans", json={"card_uid": "CARD_101", "source": "ESP32"})
    client.post("/api/scans", json={"card_uid": "CARD_103", "source": "SIMULATOR"})
    
    return {
        "active_students": [s1, s2, s3],
        "inactive_student": s4,
        "session1": sess1,
        "session2": sess2
    }


# ==============================================================================
# SECTION A: DAILY ATTENDANCE (GET /api/attendance)
# ==============================================================================

def test_1_daily_attendance_retrieval_succeeds(client, populated_day):
    """TEST 1: Verify GET /api/attendance?date=YYYY-MM-DD returns HTTP 200."""
    response = client.get("/api/attendance?date=2026-11-07")
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_2_correct_attendance_records_returned(client, populated_day):
    """TEST 2: Verify total count of attendance records across all sessions for that date."""
    response = client.get("/api/attendance?date=2026-11-07")
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 4  # 2 in session 1 + 2 in session 2
    assert len(data["attendance"]) == 4


def test_3_student_details_included(client, populated_day):
    """TEST 3: Verify student name and roll number are included in records."""
    response = client.get("/api/attendance?date=2026-11-07")
    records = response.get_json()["attendance"]
    assert all("roll_number" in r and "student_name" in r for r in records)
    roll_numbers = {r["roll_number"] for r in records}
    assert "101" in roll_numbers
    assert "102" in roll_numbers
    assert "103" in roll_numbers


def test_4_session_details_included(client, populated_day):
    """TEST 4: Verify course name, session ID, and start time are included."""
    response = client.get("/api/attendance?date=2026-11-07")
    records = response.get_json()["attendance"]
    courses = {r["course_name"] for r in records}
    assert "Engineering Mathematics" in courses
    assert "Physics" in courses
    assert all("session_id" in r and "start_time" in r for r in records)


def test_5_empty_attendance_returns_empty_list(client):
    """TEST 5: Verify valid date with no attendance returns count 0 and empty list."""
    client.post("/api/sessions", json={"course_name": "Empty Course", "session_date": "2026-11-08", "start_time": "10:00", "end_time": "23:59"})
    response = client.get("/api/attendance?date=2026-11-08")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["attendance"] == []


def test_6_missing_date_is_rejected(client):
    """TEST 6: Verify missing date query parameter returns HTTP 400."""
    response = client.get("/api/attendance")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Bad Request"


def test_7_invalid_date_format_is_rejected(client):
    """TEST 7: Verify malformed date (e.g. DD-MM-YYYY) returns HTTP 400."""
    response = client.get("/api/attendance?date=07-11-2026")
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.get_json()["message"]


def test_8_impossible_calendar_date_is_rejected(client):
    """TEST 8: Verify impossible calendar date (e.g. 2026-02-30) returns HTTP 400."""
    response = client.get("/api/attendance?date=2026-02-30")
    assert response.status_code == 400
    assert "not a valid calendar date" in response.get_json()["message"]


def test_9_records_from_another_date_are_excluded(client, populated_day):
    """TEST 9: Verify querying 2026-11-08 does not return 2026-11-07 records."""
    response = client.get("/api/attendance?date=2026-11-08")
    data = response.get_json()
    assert data["count"] == 0
    assert data["attendance"] == []


def test_10_multiple_sessions_on_same_date_included(client, populated_day):
    """TEST 10: Verify both 10:00 and 12:00 sessions on 2026-11-07 appear in results."""
    response = client.get("/api/attendance?date=2026-11-07")
    records = response.get_json()["attendance"]
    session_ids = {r["session_id"] for r in records}
    assert len(session_ids) == 2


# ==============================================================================
# SECTION B: DAILY ATTENDANCE REPORT (GET /api/reports/daily)
# ==============================================================================

def test_11_daily_report_returns_correct_statistics(client, populated_day):
    """TEST 11: Verify GET /api/reports/daily returns summary and session stats."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "summary" in data
    assert "sessions" in data
    assert len(data["sessions"]) == 2


def test_12_present_count_is_correct(client, populated_day):
    """TEST 12: Verify present count for Session 1 and Session 2 is 2."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    sessions = response.get_json()["sessions"]
    for sess in sessions:
        assert sess["present"] == 2


def test_13_absent_count_is_correct(client, populated_day):
    """TEST 13: Verify absent count for Session 1 and Session 2 is 1 (3 active - 2 present)."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    sessions = response.get_json()["sessions"]
    for sess in sessions:
        assert sess["absent"] == 1


def test_14_attendance_percentage_is_correct(client, populated_day):
    """TEST 14: Verify attendance percentage calculation: (2 / 3) * 100 = 66.67%."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    sessions = response.get_json()["sessions"]
    for sess in sessions:
        assert sess["attendance_percentage"] == 66.67


def test_15_multiple_sessions_are_calculated_separately(client, populated_day):
    """TEST 15: Verify Session 1 and Session 2 have separate reporting objects."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    sessions = response.get_json()["sessions"]
    assert sessions[0]["course_name"] == "Engineering Mathematics"
    assert sessions[1]["course_name"] == "Physics"


def test_16_inactive_students_excluded_from_active_roster(client, populated_day):
    """TEST 16: Verify inactive student is NOT counted in total_students (should be 3 active, not 4)."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    sessions = response.get_json()["sessions"]
    assert sessions[0]["total_students"] == 3


def test_17_zero_student_calculation_does_not_divide_by_zero(client):
    """TEST 17: Verify 0 registered students calculates 0.0% without ZeroDivisionError."""
    # Create session with zero students in database
    client.post("/api/sessions", json={"course_name": "Zero Class", "session_date": "2026-11-09", "start_time": "10:00", "end_time": "23:59"})
    response = client.get("/api/reports/daily?date=2026-11-09")
    assert response.status_code == 200
    data = response.get_json()
    sess = data["sessions"][0]
    assert sess["total_students"] == 0
    assert sess["attendance_percentage"] == 0.0


def test_18_date_with_no_sessions_returns_zero_totals(client):
    """TEST 18: Verify date with no sessions returns total_sessions = 0 and empty sessions list."""
    response = client.get("/api/reports/daily?date=2026-12-25")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["total_sessions"] == 0
    assert data["summary"]["total_enrollments"] == 0
    assert data["summary"]["overall_attendance_percentage"] == 0.0
    assert data["sessions"] == []


def test_19_overall_totals_represent_student_session_opportunities(client, populated_day):
    """TEST 19: Verify total_enrollments = 3 active students * 2 sessions = 6."""
    response = client.get("/api/reports/daily?date=2026-11-07")
    summary = response.get_json()["summary"]
    assert summary["total_sessions"] == 2
    assert summary["total_enrollments"] == 6
    assert summary["total_present"] == 4
    assert summary["total_absent"] == 2
    assert summary["overall_attendance_percentage"] == 66.67


# ==============================================================================
# SECTION C: CSV EXPORT (GET /api/reports/export.csv)
# ==============================================================================

def test_20_csv_export_succeeds(client, populated_day):
    """TEST 20: Verify GET /api/reports/export.csv?date=YYYY-MM-DD returns HTTP 200."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    assert response.status_code == 200


def test_21_correct_csv_content_type_returned(client, populated_day):
    """TEST 21: Verify Content-Type contains text/csv."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    assert "text/csv" in response.content_type


def test_22_content_disposition_includes_meaningful_filename(client, populated_day):
    """TEST 22: Verify Content-Disposition header specifies attendance_2026-11-07.csv."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    assert 'attachment; filename="attendance_2026-11-07.csv"' in response.headers["Content-Disposition"]


def test_23_csv_contains_expected_headers(client, populated_day):
    """TEST 23: Verify CSV header matches the 10 required column names."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    reader = csv.reader(io.StringIO(response.get_data(as_text=True)))
    headers = next(reader)
    expected_headers = [
        "Date", "Session ID", "Course Name", "Start Time",
        "Roll Number", "Student Name", "RFID UID",
        "Attendance Status", "Scanned At (UTC)", "Source"
    ]
    assert headers == expected_headers


def test_24_present_students_included(client, populated_day):
    """TEST 24: Verify present students have Attendance Status == 'Present'."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    rows = list(reader)
    present_rows = [r for r in rows if r["Attendance Status"] == "Present"]
    assert len(present_rows) == 4  # 2 in session 1 + 2 in session 2


def test_25_absent_students_included(client, populated_day):
    """TEST 25: Verify absent active students have Attendance Status == 'Absent' via LEFT JOIN."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    rows = list(reader)
    absent_rows = [r for r in rows if r["Attendance Status"] == "Absent"]
    assert len(absent_rows) == 2  # Amit in session 1 + Priya in session 2


def test_26_absent_students_have_blank_scanned_at_and_source(client, populated_day):
    """TEST 26: Verify absent rows have empty string for Scanned At and Source."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    for r in reader:
        if r["Attendance Status"] == "Absent":
            assert r["Scanned At (UTC)"] == ""
            assert r["Source"] == ""
        elif r["Attendance Status"] == "Present":
            assert r["Scanned At (UTC)"] != ""
            assert r["Source"] in ("ESP32", "SIMULATOR")


def test_27_multiple_sessions_exported_separately(client, populated_day):
    """TEST 27: Verify CSV contains rows grouped by each session ID."""
    response = client.get("/api/reports/export.csv?date=2026-11-07")
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    rows = list(reader)
    # 3 active students * 2 sessions = 6 total rows
    assert len(rows) == 6
    session_courses = {r["Course Name"] for r in rows}
    assert session_courses == {"Engineering Mathematics", "Physics"}


def test_28_csv_escaping_works_for_commas_and_quotes(client):
    """TEST 28: Verify course names and student names with commas and quotes are escaped correctly."""
    client.post("/api/students", json={
        "roll_number": "201",
        "full_name": 'Dr. Roy, Jr. "Rob"',
        "card_uid": "CARD_ROY"
    })
    client.post("/api/sessions", json={
        "course_name": "Calculus, Differential & Integral",
        "session_date": "2026-11-10",
        "start_time": "10:00", "end_time": "23:59"})
    response = client.get("/api/reports/export.csv?date=2026-11-10")
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    row = next(reader)
    assert row["Student Name"] == 'Dr. Roy, Jr. "Rob"'
    assert row["Course Name"] == "Calculus, Differential & Integral"


def test_29_invalid_dates_rejected_for_csv(client):
    """TEST 29: Verify CSV endpoint rejects invalid or impossible dates with HTTP 400."""
    res1 = client.get("/api/reports/export.csv")
    assert res1.status_code == 400
    res2 = client.get("/api/reports/export.csv?date=2026-13-45")
    assert res2.status_code == 400


def test_30_date_with_no_sessions_returns_valid_csv_with_headers(client):
    """TEST 30: Verify date with no sessions returns 200 and a valid CSV with only headers."""
    response = client.get("/api/reports/export.csv?date=2026-12-25")
    assert response.status_code == 200
    reader = csv.reader(io.StringIO(response.get_data(as_text=True)))
    rows = list(reader)
    assert len(rows) == 1  # Header row only
    assert rows[0][0] == "Date"


# ==============================================================================
# SECTION D: TRANSACTION SAFETY
# ==============================================================================

def test_31_atomic_transaction_safety_during_scan(client):
    """
    TEST 31: Verify atomic transaction safety during scan:
    If active session is deactivated before insert, the scan transaction cleanly rolls back
    and returns 409 without creating an orphaned attendance record.
    """
    client.post("/api/students", json={"roll_number": "301", "full_name": "Test Race", "card_uid": "CARD_RACE"})
    res_sess = client.post("/api/sessions", json={"course_name": "Race Session", "session_date": "2026-11-07", "start_time": "15:00", "end_time": "23:59"})
    sess_id = res_sess.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")
    
    # Deactivate session
    client.patch(f"/api/sessions/{sess_id}/deactivate")
    
    # Attempt scan
    res_scan = client.post("/api/scans", json={"card_uid": "CARD_RACE", "source": "ESP32"})
    assert res_scan.status_code == 409
    assert res_scan.get_json()["error"] == "No Active Session"
    
    # Check that attendance table has 0 records
    att_res = client.get("/api/attendance?date=2026-11-07")
    assert att_res.get_json()["count"] == 0


def test_32_csv_formula_injection_mitigation(client):
    """
    TEST 32: Verify CSV Formula Injection (CWE-1236) mitigation:
    User-controlled fields starting with '=', '+', '-', '@' must be escaped with a leading single quote (')
    to prevent spreadsheet applications from executing formulas.
    """
    # 1. Register student with formula triggers in name and roll number
    client.post("/api/students", json={
        "roll_number": "+919876543210",
        "full_name": "=SUM(1+1)",
        "card_uid": "CARD_FORMULA_1"
    })
    client.post("/api/students", json={
        "roll_number": "-999",
        "full_name": "@ADMINISTRATOR",
        "card_uid": "CARD_FORMULA_2"
    })
    
    # 2. Create session with formula trigger in course name
    client.post("/api/sessions", json={
        "course_name": '=HYPERLINK("http://attacker.com", "Malicious Link")',
        "session_date": "2026-11-15",
        "start_time": "09:00", "end_time": "23:59"})
    
    # 3. Export CSV
    response = client.get("/api/reports/export.csv?date=2026-11-15")
    assert response.status_code == 200
    
    reader = csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    rows = list(reader)
    assert len(rows) == 2
    
    # Row 1 check
    row1 = next(r for r in rows if "919876543210" in r["Roll Number"])
    assert row1["Roll Number"].startswith("'+919876543210")
    assert row1["Student Name"].startswith("'=SUM(1+1)")
    assert row1["Course Name"].startswith("''=HYPERLINK") or row1["Course Name"].startswith("'=HYPERLINK")
    
    # Row 2 check
    row2 = next(r for r in rows if "999" in r["Roll Number"])
    assert row2["Roll Number"].startswith("'-999")
    assert row2["Student Name"].startswith("'@ADMINISTRATOR")


def test_33_csv_sanitize_cell_unit_function():
    """TEST 33: Unit test sanitize_csv_cell for all trigger and non-trigger characters."""
    from app.routes.reports import sanitize_csv_cell
    
    # Trigger characters
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+44123") == "'+44123"
    assert sanitize_csv_cell("-50") == "'-50"
    assert sanitize_csv_cell("@root") == "'@root"
    assert sanitize_csv_cell("\ttab") == "'\ttab"
    assert sanitize_csv_cell("\rcarriage") == "'\rcarriage"
    
    # Safe values
    assert sanitize_csv_cell("Rahul Sharma") == "Rahul Sharma"
    assert sanitize_csv_cell("101") == "101"
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell(123) == "123"
    assert sanitize_csv_cell(0) == "0"

