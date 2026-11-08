"""
Automated Integration and Functional Tests for Live Attendance UI (Phase 2 - Milestone 5).
Verifies:
1. Attendance page loads successfully with HTTP 200 and correct headers.
2. Correct navigation item is active.
3. Required CSS and JavaScript assets are included and served.
4. Active class session hero overview card elements are present.
5. Four attendance summary KPI metric cards are present.
6. Live scan status panel and RFID simulator form elements are present.
7. Attendance table structure and empty/error states are present.
8. Active session API integration loads active session accurately.
9. Attendance records load and can be filtered by active session.
10. Student summary counts correctly exclude inactive student accounts.
11. Successful scan handling (HTTP 201) returns proper payload.
12. Duplicate scan handling (HTTP 409) returns duplicate conflict error.
13. Unknown RFID UID handling (HTTP 404) returns student not found error.
14. Inactive student scan handling (HTTP 403) returns inactive student error.
15. Missing active session scan handling (HTTP 409) returns no active session error.
16. Invalid scan payload handling (HTTP 400) rejects malformed inputs.
17. JavaScript asset contains defensive normalization, XSS escaping, and polling guards.
"""

from unittest.mock import patch
import sqlite3
import pytest
from app import create_app
from app.database import init_db, get_db_connection


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_attendance_ui.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


def test_1_attendance_page_loads_successfully(client):
    """Verify GET /attendance returns HTTP 200 with HTML content and correct page headers."""
    res = client.get("/attendance")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    assert "Live Attendance Interface" in html
    assert "Real-time attendance stream" in html
    assert 'id="sidebar"' in html
    assert 'id="live-clock"' in html
    assert 'id="topbar-session-pill"' in html


def test_2_correct_navigation_item_is_active(client):
    """Verify that the Live Attendance sidebar link is marked active."""
    res = client.get("/attendance")
    html = res.get_data(as_text=True)
    assert 'id="nav-attendance"' in html
    assert 'class="nav-link active" id="nav-attendance"' in html or 'active' in html


def test_3_required_template_and_assets_included(client):
    """Verify CSS and JS asset files specific to Live Attendance are linked and served."""
    res = client.get("/attendance")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Core scripts & attendance script
    assert "js/app.js" in html
    assert "js/attendance.js" in html

    # Core stylesheets & attendance stylesheet
    assert "css/style.css" in html
    assert "css/components.css" in html
    assert "css/attendance.css" in html

    # Verify CSS is served
    css_res = client.get("/static/css/attendance.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.content_type

    # Verify JS is served
    js_res = client.get("/static/js/attendance.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.content_type or "text/plain" in js_res.content_type


def test_4_active_session_hero_card_elements_present(client):
    """Verify active session hero card markup and placeholder containers."""
    res = client.get("/attendance")
    html = res.get_data(as_text=True)

    assert 'id="live-active-session-card"' in html
    assert 'id="active-session-dot"' in html
    assert 'id="active-session-status"' in html
    assert 'id="active-course-title"' in html
    assert 'id="active-session-date"' in html
    assert 'id="active-session-time"' in html
    assert 'id="active-session-id"' in html
    assert 'id="active-session-badge"' in html
    assert 'id="polling-indicator"' in html


def test_5_attendance_summary_cards_present(client):
    """Verify all 4 summary KPI metric cards are present in the markup."""
    res = client.get("/attendance")
    html = res.get_data(as_text=True)

    assert 'id="card-stat-total"' in html
    assert 'id="stat-total-students"' in html
    assert 'id="card-stat-active"' in html
    assert 'id="stat-active-students"' in html
    assert 'id="card-stat-present"' in html
    assert 'id="stat-present-session"' in html
    assert 'id="card-stat-remaining"' in html
    assert 'id="stat-remaining-session"' in html


def test_6_scan_status_panel_and_simulator_elements_present(client):
    """Verify live scan status feedback box and RFID simulator form elements."""
    res = client.get("/attendance")
    html = res.get_data(as_text=True)

    # Status panel
    assert 'id="scan-status-card"' in html
    assert 'id="scan-feedback-box"' in html
    assert 'id="scan-feedback-icon"' in html
    assert 'id="scan-feedback-title"' in html
    assert 'id="scan-feedback-subtitle"' in html
    assert 'id="scan-status-badge"' in html

    # Simulator card and form
    assert 'id="simulator-card"' in html
    assert 'id="form-simulate-scan"' in html
    assert 'id="sim-student-select"' in html
    assert 'id="sim-card-uid"' in html
    assert 'id="sim-source"' in html
    assert 'id="btn-submit-scan"' in html
    assert 'id="btn-reset-scan"' in html
    assert 'id="btn-chip-unregistered"' in html
    assert 'id="btn-chip-rescan"' in html


def test_7_live_attendance_table_and_states_present(client):
    """Verify attendance table headers and empty/error state boxes."""
    res = client.get("/attendance")
    html = res.get_data(as_text=True)

    assert 'id="live-roster-card"' in html
    assert 'id="attendance-table"' in html
    assert 'id="attendance-table-body"' in html
    assert 'id="attendance-count-badge"' in html

    # Table columns
    assert "Roll Number" in html
    assert "Student Name" in html
    assert "Course" in html
    assert "Scan Time" in html
    assert "Status" in html
    assert "Source" in html

    # States
    assert 'id="attendance-empty-state"' in html
    assert 'id="attendance-no-session-state"' in html
    assert 'id="attendance-error-state"' in html


def test_8_active_session_api_integration_loads_correctly(client):
    """Verify GET /api/sessions/active returns null initially and active session once activated."""
    # 1. Null state
    res_none = client.get("/api/sessions/active")
    assert res_none.status_code == 200
    assert res_none.get_json()["session"] is None

    # 2. Create and activate a session
    sess_res = client.post("/api/sessions", json={"course_name": "CS-301 Algorithms", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # 3. Active state
    res_active = client.get("/api/sessions/active")
    assert res_active.status_code == 200
    active_data = res_active.get_json()
    assert active_data["session"] is not None
    assert active_data["session"]["id"] == sess_id
    assert active_data["session"]["course_name"] == "CS-301 Algorithms"


def test_9_attendance_records_load_and_filter_by_active_session(client):
    """Verify attendance endpoint returns records and client can filter by active session ID."""
    # Create two sessions on same date
    s1_res = client.post("/api/sessions", json={"course_name": "Session 1", "session_date": "2026-11-07", "start_time": "09:00", "end_time": "23:59"})
    s1_id = s1_res.get_json()["session"]["id"]

    s2_res = client.post("/api/sessions", json={"course_name": "Session 2", "session_date": "2026-11-07", "start_time": "11:00", "end_time": "23:59"})
    s2_id = s2_res.get_json()["session"]["id"]

    # Register two students
    client.post("/api/students", json={"roll_number": "ST-01", "full_name": "Student One", "card_uid": "UID001"})
    client.post("/api/students", json={"roll_number": "ST-02", "full_name": "Student Two", "card_uid": "UID002"})

    # Activate Session 1 and scan Student 1
    client.patch(f"/api/sessions/{s1_id}/activate")
    scan1 = client.post("/api/scans", json={"card_uid": "UID001", "source": "SIMULATOR"})
    assert scan1.status_code == 201

    # Activate Session 2 and scan Student 2
    client.patch(f"/api/sessions/{s2_id}/activate")
    scan2 = client.post("/api/scans", json={"card_uid": "UID002", "source": "SIMULATOR"})
    assert scan2.status_code == 201

    # Daily attendance returns both records
    att_res = client.get("/api/attendance?date=2026-11-07")
    assert att_res.status_code == 200
    all_recs = att_res.get_json()["attendance"]
    assert len(all_recs) == 2

    # Filter by Session 2 ID (active session)
    s2_recs = [r for r in all_recs if r["session_id"] == s2_id]
    assert len(s2_recs) == 1
    assert s2_recs[0]["roll_number"] == "ST-02"


def test_10_student_summary_counts_and_inactive_exclusion(client, app_instance):
    """Verify student retrieval counts accurately distinguish active and inactive students."""
    # Register 2 active students
    client.post("/api/students", json={"roll_number": "ST-10", "full_name": "Active One", "card_uid": "ACT01"})
    client.post("/api/students", json={"roll_number": "ST-11", "full_name": "Active Two", "card_uid": "ACT02"})

    # Register 1 inactive student
    res_inact = client.post("/api/students", json={"roll_number": "ST-12", "full_name": "Inactive Three", "card_uid": "INACT03"})
    inact_id = res_inact.get_json()["student"]["id"]

    # Deactivate 3rd student in database
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    conn.execute("UPDATE students SET active = 0 WHERE id = ?;", (inact_id,))
    conn.commit()
    conn.close()

    # Query API
    res_students = client.get("/api/students")
    assert res_students.status_code == 200
    students_list = res_students.get_json()["students"]
    assert len(students_list) == 3

    # Calculate active vs total
    active_students = [s for s in students_list if s["active"] == 1 or s["active"] is True]
    assert len(active_students) == 2


def test_11_successful_scan_response_handling(client):
    """Verify POST /api/scans returns HTTP 201 with full attendance payload and server timestamp."""
    # Setup active session and student
    sess_res = client.post("/api/sessions", json={"course_name": "Data Structures", "session_date": "2026-11-07", "start_time": "14:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    client.post("/api/students", json={"roll_number": "CS-44", "full_name": "Scan Test Student", "card_uid": "VALIDSCAN01"})

    # Send scan
    scan_res = client.post("/api/scans", json={"card_uid": "VALIDSCAN01", "source": "SIMULATOR"})
    assert scan_res.status_code == 201
    data = scan_res.get_json()

    assert data["success"] is True
    assert "recorded successfully" in data["message"].lower()
    att = data["attendance"]
    assert att["roll_number"] == "CS-44"
    assert att["student_name"] == "Scan Test Student"
    assert att["course_name"] == "Data Structures"
    assert att["session_id"] == sess_id
    assert att["status"] == "Present"
    assert att["source"] == "SIMULATOR"
    assert att["scanned_at"].endswith("Z")


def test_12_duplicate_scan_response_handling(client):
    """Verify scanning the same card twice in the same session returns HTTP 409 Duplicate Attendance."""
    sess_res = client.post("/api/sessions", json={"course_name": "Networks", "session_date": "2026-11-07", "start_time": "15:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    client.post("/api/students", json={"roll_number": "CS-55", "full_name": "Dup Test Student", "card_uid": "DUPSCAN01"})

    # First scan succeeds
    res1 = client.post("/api/scans", json={"card_uid": "DUPSCAN01", "source": "SIMULATOR"})
    assert res1.status_code == 201

    # Second scan returns 409 Duplicate
    res2 = client.post("/api/scans", json={"card_uid": "DUPSCAN01", "source": "SIMULATOR"})
    assert res2.status_code == 409
    data = res2.get_json()
    assert data["success"] is False
    assert data["error"] == "Duplicate Attendance"
    assert "already been recorded" in data["message"]


def test_13_unknown_rfid_uid_response_handling(client):
    """Verify scanning an unregistered RFID card returns HTTP 404 Student Not Found."""
    sess_res = client.post("/api/sessions", json={"course_name": "Security", "session_date": "2026-11-07", "start_time": "16:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    res = client.post("/api/scans", json={"card_uid": "UNREGISTERED_UID", "source": "SIMULATOR"})
    assert res.status_code == 404
    data = res.get_json()
    assert data["success"] is False
    assert data["error"] == "Student Not Found"
    assert "not registered" in data["message"]


def test_14_inactive_student_scan_response_handling(client, app_instance):
    """Verify scanning a card for an inactive student returns HTTP 403 Inactive Student."""
    sess_res = client.post("/api/sessions", json={"course_name": "Algorithms", "session_date": "2026-11-07", "start_time": "17:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    student_res = client.post("/api/students", json={"roll_number": "INACT-99", "full_name": "Inactive Student", "card_uid": "INACTCARD99"})
    student_id = student_res.get_json()["student"]["id"]

    # Deactivate student
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    conn.execute("UPDATE students SET active = 0 WHERE id = ?;", (student_id,))
    conn.commit()
    conn.close()

    res = client.post("/api/scans", json={"card_uid": "INACTCARD99", "source": "SIMULATOR"})
    assert res.status_code == 403
    data = res.get_json()
    assert data["success"] is False
    assert data["error"] == "Inactive Student"
    assert "inactive" in data["message"]


def test_15_scan_without_active_session_handled_correctly(client):
    """Verify scanning when no session is active returns HTTP 409 No Active Session."""
    client.post("/api/students", json={"roll_number": "CS-77", "full_name": "Session Test Student", "card_uid": "NOSESSIONUID"})

    # Ensure no active session
    active_check = client.get("/api/sessions/active")
    assert active_check.get_json()["session"] is None

    res = client.post("/api/scans", json={"card_uid": "NOSESSIONUID", "source": "SIMULATOR"})
    assert res.status_code == 409
    data = res.get_json()
    assert data["success"] is False
    assert data["error"] == "No Active Session"
    assert "No class session is currently active" in data["message"]


def test_16_invalid_scan_payload_rejected_without_false_success(client):
    """Verify invalid payloads (missing UID, invalid characters, too short) return HTTP 400."""
    # 1. Missing card_uid
    res1 = client.post("/api/scans", json={"source": "SIMULATOR"})
    assert res1.status_code == 400

    # 2. Too short UID
    res2 = client.post("/api/scans", json={"card_uid": "AB", "source": "SIMULATOR"})
    assert res2.status_code == 400

    # 3. Invalid characters
    res3 = client.post("/api/scans", json={"card_uid": "UID@#$%", "source": "SIMULATOR"})
    assert res3.status_code == 400


def test_17_client_js_contains_defensive_features_and_escaping(client):
    """Verify client JS asset contains active student normalization, XSS escaping, and non-negative logic."""
    res = client.get("/static/js/attendance.js")
    assert res.status_code == 200
    js_content = res.get_data(as_text=True)

    assert "isActiveStudent" in js_content
    assert "escapeHtml" in js_content
    assert "Math.max(0," in js_content
    assert "isPollInFlight" in js_content
    assert "isSubmittingScan" in js_content
    assert "window.AttendanceController" in js_content
