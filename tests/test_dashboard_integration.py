"""
Automated Integration and Data Accuracy Tests for Dashboard APIs (Milestone 2).
Verifies:
1. Dashboard loads successfully with all KPI DOM hooks.
2. Student count obtained accurately from real API (active vs inactive).
3. Active class session reflects actual database status (null vs active).
4. Today's attendance percentage accurately reflects backend denominator.
5. Backend and SQLite health status accurately reflected.
6. Recent attendance records are retrieved accurately with student details.
7. Empty states work cleanly when no scans or sessions exist.
8. API error handling for malformed or missing parameters.
9. Asia/Kolkata date resolution and timezone alignment.
10. Preservation of existing backend contracts and schema.
"""

from datetime import datetime, timezone, timedelta
import pytest
from app import create_app
from app.database import init_db, get_db_connection

# Asia/Kolkata is UTC +05:30
KOLKATA_TZ = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_dashboard.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


def get_kolkata_today_str() -> str:
    """Returns today's date formatted as YYYY-MM-DD in Asia/Kolkata timezone (+05:30)."""
    return datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d")


def test_1_dashboard_page_loads_with_all_kpi_hooks(client):
    """Verify GET / and /dashboard load with all required KPI IDs, badges, and activity containers."""
    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Header and controls
    assert 'id="today-date-badge"' in html
    assert 'id="btn-refresh-dashboard"' in html

    # KPI Card 1 (Students)
    assert 'id="kpi-students-card"' in html
    assert 'id="kpi-students-count"' in html
    assert 'id="kpi-students-subtext"' in html

    # KPI Card 2 (Active Session)
    assert 'id="kpi-session-card"' in html
    assert 'id="kpi-session-course"' in html
    assert 'id="kpi-session-time"' in html

    # KPI Card 3 (Attendance Rate)
    assert 'id="kpi-attendance-card"' in html
    assert 'id="kpi-attendance-rate"' in html
    assert 'id="kpi-attendance-counts"' in html

    # KPI Card 4 (Storage & Health Status)
    assert 'id="kpi-status-card"' in html
    assert 'id="kpi-db-badge"' in html
    assert 'id="kpi-api-badge"' in html

    # Activity Stream Container & Empty State
    assert 'id="activity-container"' in html
    assert 'id="dashboard-empty-state"' in html
    assert 'id="activity-stream-badge"' in html


def test_2_student_count_accurate_and_distinguishes_active_inactive(client, app_instance):
    """Verify GET /api/students returns actual student records and distinguishes active vs inactive."""
    # Initially 0 students
    res = client.get("/api/students")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["students"] == []

    # Register 2 active students
    res1 = client.post("/api/students", json={
        "roll_number": "CS-01",
        "full_name": "Aarav Sharma",
        "card_uid": "A1B2C3D4"
    })
    assert res1.status_code == 201

    res2 = client.post("/api/students", json={
        "roll_number": "CS-02",
        "full_name": "Priya Patel",
        "card_uid": "E5F6A7B8"
    })
    assert res2.status_code == 201

    # Insert an inactive student directly into SQLite
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES (?, ?, ?, 0, ?)",
        ("CS-03", "Rohan Verma", "99887766", "2026-11-07T00:00:00Z")
    )
    conn.commit()
    conn.close()

    # Query API
    res3 = client.get("/api/students")
    assert res3.status_code == 200
    data3 = res3.get_json()
    assert data3["count"] == 3
    students = data3["students"]
    active_students = [s for s in students if s["active"] is True]
    inactive_students = [s for s in students if s["active"] is False]

    assert len(active_students) == 2
    assert len(inactive_students) == 1
    assert active_students[0]["full_name"] == "Aarav Sharma"
    assert active_students[1]["full_name"] == "Priya Patel"
    assert inactive_students[0]["full_name"] == "Rohan Verma"


def test_3_active_session_accurate_representation(client):
    """Verify GET /api/sessions/active returns null when none is active and session details when activated."""
    # When no session exists
    res = client.get("/api/sessions/active")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data.get("session") is None

    # Create session (inactive by default)
    today = get_kolkata_today_str()
    create_res = client.post("/api/sessions", json={
        "course_name": "Embedded Systems IoT",
        "session_date": today,
        "start_time": "10:30",
        "end_time": "23:59",
    })
    assert create_res.status_code == 201
    session_id = create_res.get_json()["session"]["id"]

    # Still null because it was created inactive
    res2 = client.get("/api/sessions/active")
    assert res2.get_json()["session"] is None

    # Activate the session
    act_res = client.patch(f"/api/sessions/{session_id}/activate")
    assert act_res.status_code == 200

    # Now active session is returned accurately
    res3 = client.get("/api/sessions/active")
    assert res3.status_code == 200
    session_data = res3.get_json()["session"]
    assert session_data is not None
    assert session_data["id"] == session_id
    assert session_data["course_name"] == "Embedded Systems IoT"
    assert session_data["session_date"] == today
    assert session_data["start_time"] == "10:30"
    assert session_data["active"] is True


def test_4_today_attendance_rate_matches_backend_calculation(client):
    """Verify GET /api/reports/daily returns exact percentage matching present / enrolled."""
    today = get_kolkata_today_str()

    # Case A: No sessions today -> 0.0%
    res = client.get(f"/api/reports/daily?date={today}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["summary"]["total_sessions"] == 0
    assert data["summary"]["overall_attendance_percentage"] == 0.0

    # Register 2 active students
    client.post("/api/students", json={"roll_number": "ROLL-01", "full_name": "Student One", "card_uid": "CARD01"})
    client.post("/api/students", json={"roll_number": "ROLL-02", "full_name": "Student Two", "card_uid": "CARD02"})

    # Create and activate session for today
    s_res = client.post("/api/sessions", json={
        "course_name": "Network Security",
        "session_date": today,
        "start_time": "09:00",
        "end_time": "23:59",
    })
    session_id = s_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{session_id}/activate")

    # Before scans: 0 present out of 2 enrolled -> 0.0%
    res_before = client.get(f"/api/reports/daily?date={today}")
    assert res_before.status_code == 200
    summary_before = res_before.get_json()["summary"]
    assert summary_before["total_sessions"] == 1
    assert summary_before["total_enrollments"] == 2
    assert summary_before["total_present"] == 0
    assert summary_before["total_absent"] == 2
    assert summary_before["overall_attendance_percentage"] == 0.0

    # Scan 1 student
    scan_res = client.post("/api/scans", json={"card_uid": "CARD01", "source": "simulated"})
    assert scan_res.status_code == 201

    # After 1 scan: 1 present out of 2 enrolled -> 50.0%
    res_after = client.get(f"/api/reports/daily?date={today}")
    assert res_after.status_code == 200
    summary_after = res_after.get_json()["summary"]
    assert summary_after["total_present"] == 1
    assert summary_after["total_absent"] == 1
    assert summary_after["overall_attendance_percentage"] == 50.0


def test_5_backend_health_status_reporting(client, app_instance, monkeypatch):
    """Verify GET /api/health accurately indicates connected vs disconnected."""
    # Healthy status
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["database"] == "connected"

    # Simulate database failure
    def mock_broken_db():
        raise Exception("Database disk I/O failure simulated")

    monkeypatch.setattr("app.routes.health.get_db", mock_broken_db)
    res_fail = client.get("/api/health")
    assert res_fail.status_code == 503
    data_fail = res_fail.get_json()
    assert data_fail["success"] is False
    assert data_fail["database"] == "disconnected"


def test_6_recent_attendance_records_with_student_and_session_details(client):
    """Verify GET /api/attendance?date=YYYY-MM-DD returns actual records with student name, roll number, course."""
    today = get_kolkata_today_str()

    # Initially empty
    res_empty = client.get(f"/api/attendance?date={today}")
    assert res_empty.status_code == 200
    assert res_empty.get_json()["count"] == 0
    assert res_empty.get_json()["attendance"] == []

    # Register students
    client.post("/api/students", json={"roll_number": "IT-101", "full_name": "Dev Sharma", "card_uid": "DEVA101"})
    client.post("/api/students", json={"roll_number": "IT-102", "full_name": "Diya Sen", "card_uid": "DIYA102"})

    # Schedule and activate session
    s_res = client.post("/api/sessions", json={
        "course_name": "Distributed Systems",
        "session_date": today,
        "start_time": "14:00",
        "end_time": "23:59",
    })
    session_id = s_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{session_id}/activate")

    # Perform scans
    client.post("/api/scans", json={"card_uid": "DEVA101", "source": "simulated"})
    client.post("/api/scans", json={"card_uid": "DIYA102", "source": "esp32-hardware"})

    # Query daily attendance
    res = client.get(f"/api/attendance?date={today}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 2
    records = data["attendance"]

    assert records[0]["student_name"] == "Dev Sharma"
    assert records[0]["roll_number"] == "IT-101"
    assert records[0]["course_name"] == "Distributed Systems"
    assert records[0]["status"] == "Present"
    assert records[0]["source"] == "simulated"

    assert records[1]["student_name"] == "Diya Sen"
    assert records[1]["roll_number"] == "IT-102"
    assert records[1]["source"] == "esp32-hardware"


def test_7_empty_states_handled_gracefully(client):
    """Verify empty responses for days without scans or sessions."""
    future_date = "2099-01-01"

    # Attendance empty state
    res_att = client.get(f"/api/attendance?date={future_date}")
    assert res_att.status_code == 200
    data_att = res_att.get_json()
    assert data_att["count"] == 0
    assert data_att["attendance"] == []

    # Daily report empty state
    res_rep = client.get(f"/api/reports/daily?date={future_date}")
    assert res_rep.status_code == 200
    data_rep = res_rep.get_json()
    assert data_rep["summary"]["total_sessions"] == 0
    assert data_rep["summary"]["total_present"] == 0
    assert data_rep["summary"]["overall_attendance_percentage"] == 0.0
    assert data_rep["sessions"] == []


def test_8_api_failure_modes_and_validation(client):
    """Verify error responses for invalid or missing date parameters in dashboard endpoints."""
    # Missing date parameter
    res1 = client.get("/api/attendance")
    assert res1.status_code == 400
    assert res1.get_json()["error"] == "Bad Request"

    res2 = client.get("/api/reports/daily")
    assert res2.status_code == 400
    assert res2.get_json()["error"] == "Bad Request"

    # Malformed date parameter
    res3 = client.get("/api/attendance?date=not-a-date")
    assert res3.status_code == 400
    assert "YYYY-MM-DD format" in res3.get_json()["message"]

    res4 = client.get("/api/reports/daily?date=2026-02-31")
    assert res4.status_code == 400
    assert "not a valid calendar date" in res4.get_json()["message"]


def test_9_asia_kolkata_date_resolution(client):
    """Verify that Asia/Kolkata date format aligns perfectly with backend expectation."""
    kolkata_now = datetime.now(KOLKATA_TZ)
    kolkata_date_str = kolkata_now.strftime("%Y-%m-%d")

    # Must be valid YYYY-MM-DD
    assert len(kolkata_date_str) == 10
    year, month, day = kolkata_date_str.split("-")
    assert len(year) == 4 and len(month) == 2 and len(day) == 2

    # Query with Asia/Kolkata date must succeed
    res = client.get(f"/api/reports/daily?date={kolkata_date_str}")
    assert res.status_code == 200
    assert res.get_json()["date"] == kolkata_date_str


def test_10_client_scripts_contain_timezone_and_polling_features(client):
    """Verify client JS files contain Asia/Kolkata timezone handling, skeleton logic, and polling intervals."""
    # Test app.js
    res_app = client.get("/static/js/app.js")
    assert res_app.status_code == 200
    app_js = res_app.get_data(as_text=True)
    assert "Asia/Kolkata" in app_js
    assert "getKolkataTodayDateString" in app_js
    assert "formatScanTime" in app_js
    assert "checkServerHealth" in app_js
    assert "checkActiveSession" in app_js
    assert "isHealthCheckInFlight" in app_js
    assert "document.hidden" in app_js

    # Test dashboard.js
    res_dash = client.get("/static/js/dashboard.js")
    assert res_dash.status_code == 200
    dash_js = res_dash.get_data(as_text=True)
    assert "loadStudentCount" in dash_js
    assert "loadActiveSessionCard" in dash_js
    assert "loadTodayStats" in dash_js
    assert "loadRecentAttendance" in dash_js
    assert "refreshDashboard" in dash_js
    assert "skeleton" in dash_js
    assert "isStudentsInFlight" in dash_js
    assert "activity-item" in dash_js


def test_11_active_student_counting_normalization_and_api_contract(client, app_instance):
    """
    Verify active/inactive student representation:
    1. The API contract returns JSON boolean true/false.
    2. SQLite stores INTEGER 1/0.
    3. Normalization logic handles both boolean true/false and integer 1/0.
    4. Roster with multiple active and inactive students produces exact counts.
    5. dashboard.js contains defensive normalization for Number(s.active) === 1.
    """
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)

    # Insert a roster with 3 active and 2 inactive students directly into SQLite
    test_roster = [
        ("NORM-01", "Active Student 1", "UID001", 1, "2026-11-07T00:00:00Z"),
        ("NORM-02", "Active Student 2", "UID002", 1, "2026-11-07T00:00:00Z"),
        ("NORM-03", "Active Student 3", "UID003", 1, "2026-11-07T00:00:00Z"),
        ("NORM-04", "Inactive Student 1", "UID004", 0, "2026-11-07T00:00:00Z"),
        ("NORM-05", "Inactive Student 2", "UID005", 0, "2026-11-07T00:00:00Z"),
    ]
    conn.executemany(
        "INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES (?, ?, ?, ?, ?)",
        test_roster
    )
    conn.commit()
    conn.close()

    # Call GET /api/students
    res = client.get("/api/students")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] == 5

    # Verify API contract: Flask serializes bool(row["active"]) -> JSON boolean true/false
    raw_json = res.get_data(as_text=True)
    assert '"active": true' in raw_json
    assert '"active": false' in raw_json

    # Test client-side counting logic with both booleans and raw integers
    def client_is_active(student):
        val = student.get("active")
        return val is True or val == 1

    active_count = len([s for s in data["students"] if client_is_active(s)])
    inactive_count = len([s for s in data["students"] if not client_is_active(s)])
    assert active_count == 3
    assert inactive_count == 2

    # Verify normalization on mixed payload with both integer and boolean representations
    mixed_students = [
        {"id": 1, "active": True},
        {"id": 2, "active": 1},
        {"id": 3, "active": "1"},
        {"id": 4, "active": False},
        {"id": 5, "active": 0},
        {"id": 6, "active": None},
    ]
    def normalized_is_active(s):
        val = s.get("active")
        if val is None:
            return False
        return val is True or str(val) == "1"

    mixed_active = len([s for s in mixed_students if normalized_is_active(s)])
    mixed_inactive = len(mixed_students) - mixed_active
    assert mixed_active == 3
    assert mixed_inactive == 3

    # Verify dashboard.js file contains the defensive normalization logic
    res_dash = client.get("/static/js/dashboard.js")
    assert res_dash.status_code == 200
    dash_code = res_dash.get_data(as_text=True)
    assert "Number(s.active) === 1" in dash_code
    assert "s.active === true" in dash_code

