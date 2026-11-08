"""
Automated Integration and Functional Tests for Class Session Management UI (Milestone 4).
Verifies:
1. Session management page loads successfully.
2. Required template and assets are included.
3. Create session form elements are present.
4. Session table structure is present.
5. Active session overview card is present.
6. Session API integration uses the correct endpoint.
7. Create session sends the correct request fields.
8. Create session success handling.
9. Invalid session input handling.
10. Activation request uses the correct endpoint.
11. Deactivation request uses the correct endpoint.
12. Activation API failure handling.
13. Deactivation API failure handling.
14. No active session state is displayed appropriately.
15. Existing student and attendance functionality remain unaffected.
"""

from unittest.mock import patch
import sqlite3
import pytest
from app import create_app
from app.database import init_db, get_db_connection


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_sessions_ui.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


def test_1_session_management_page_loads_successfully(client):
    """Verify GET /sessions returns HTTP 200 with HTML content and proper headings."""
    res = client.get("/sessions")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    assert "Class Session Management" in html
    assert "Create, monitor and manage classroom sessions" in html
    assert 'id="sidebar"' in html
    assert 'id="live-clock"' in html
    assert 'id="topbar-session-pill"' in html


def test_2_required_template_and_assets_included(client):
    """Verify that CSS and JS asset files specific to Class Session Management are linked and served."""
    res = client.get("/sessions")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Core scripts & session script
    assert "js/app.js" in html
    assert "js/sessions.js" in html

    # Core stylesheets & session stylesheet
    assert "css/style.css" in html
    assert "css/components.css" in html
    assert "css/sessions.css" in html

    # Verify session CSS is served with HTTP 200
    css_res = client.get("/static/css/sessions.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.content_type

    # Verify session JS is served with HTTP 200
    js_res = client.get("/static/js/sessions.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.content_type or "text/plain" in js_res.content_type


def test_3_create_session_form_elements_are_present(client):
    """Verify create session form markup, input elements, buttons, and alert container."""
    res = client.get("/sessions")
    html = res.get_data(as_text=True)

    # Form card and form tag
    assert 'id="session-create-card"' in html
    assert 'id="form-create-session"' in html

    # Input fields
    assert 'id="session-course-name"' in html
    assert 'id="session-date"' in html
    assert 'id="session-start-time"' in html

    # Action buttons and alert container
    assert 'id="btn-submit-session"' in html
    assert 'id="btn-reset-session-form"' in html
    assert 'id="session-form-alert"' in html


def test_4_session_table_structure_is_present(client):
    """Verify session table markup, column headers, search, and filter controls."""
    res = client.get("/sessions")
    html = res.get_data(as_text=True)

    # Table card and table tag
    assert 'id="session-roster-card"' in html
    assert 'id="session-table"' in html
    assert 'id="session-table-body"' in html

    # Table column headers
    assert "ID" in html
    assert "Course Name" in html
    assert "Session Date" in html
    assert "Start Time" in html
    assert "Status" in html
    assert "Actions" in html

    # Search and Filter elements
    assert 'id="search-sessions"' in html
    assert 'id="filter-session-status"' in html
    assert 'id="session-count-badge"' in html

    # Empty and Error states
    assert 'id="session-table-empty-state"' in html
    assert 'id="session-search-empty-state"' in html
    assert 'id="session-table-error-state"' in html


def test_5_active_session_overview_card_is_present(client):
    """Verify active session hero banner markup and dynamic placeholder elements."""
    res = client.get("/sessions")
    html = res.get_data(as_text=True)

    # Hero card structure
    assert 'id="active-session-card"' in html
    assert 'id="active-course-name"' in html
    assert 'id="active-session-date"' in html
    assert 'id="active-start-time"' in html
    assert 'id="active-session-id"' in html
    assert 'id="active-session-badge"' in html
    assert 'id="btn-deactivate-active-session"' in html


def test_6_session_api_integration_uses_correct_endpoints(client):
    """Verify that sessions listing and active session endpoints respond correctly."""
    # 1. GET /api/sessions responds with success and empty list initially
    res_list = client.get("/api/sessions")
    assert res_list.status_code == 200
    data_list = res_list.get_json()
    assert data_list["success"] is True
    assert data_list["count"] == 0
    assert data_list["sessions"] == []

    # 2. GET /api/sessions/active responds with success and null session initially
    res_active = client.get("/api/sessions/active")
    assert res_active.status_code == 200
    data_active = res_active.get_json()
    assert data_active["success"] is True
    assert data_active["session"] is None


def test_7_create_session_sends_correct_request_fields(client):
    """Verify POST /api/sessions accepts expected fields and returns newly created session."""
    payload = {
        "course_name": "CS-301 Algorithms",
        "session_date": "2026-11-08",
        "start_time": "09:30",
        "end_time": "11:00"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 201
    data = res.get_json()

    assert data["success"] is True
    assert "created successfully" in data["message"].lower()
    session = data["session"]
    assert session["id"] > 0
    assert session["course_name"] == "CS-301 Algorithms"
    assert session["session_date"] == "2026-11-08"
    assert session["start_time"] == "09:30"
    assert session["active"] == 0


def test_8_create_session_success_handling(client, app_instance):
    """Verify session is persisted accurately into SQLite database."""
    payload = {
        "course_name": "CS-401 Machine Learning",
        "session_date": "2026-11-09",
        "start_time": "14:00",
        "end_time": "15:30"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 201
    session_id = res.get_json()["session"]["id"]

    # Verify directly in SQLite database
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, course_name, session_date, start_time, active FROM class_sessions WHERE id = ?;", (session_id,))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row["id"] == session_id
    assert row["course_name"] == "CS-401 Machine Learning"
    assert row["session_date"] == "2026-11-09"
    assert row["start_time"] == "14:00"
    assert row["active"] == 0


def test_9_invalid_session_input_handling(client):
    """Verify client/server validation rejects invalid session payloads with HTTP 400."""
    # 1. Missing course_name
    res = client.post("/api/sessions", json={"session_date": "2026-11-08", "start_time": "09:00", "end_time": "10:00"})
    assert res.status_code == 400
    assert "course_name" in res.get_json()["message"]

    # 2. Too short course_name
    res = client.post("/api/sessions", json={"course_name": "A", "session_date": "2026-11-08", "start_time": "09:00", "end_time": "10:00"})
    assert res.status_code == 400
    assert "between 2 and 100" in res.get_json()["message"]

    # 3. Invalid date format
    res = client.post("/api/sessions", json={"course_name": "Valid Course", "session_date": "08-11-2026", "start_time": "09:00", "end_time": "10:00"})
    assert res.status_code == 400
    assert "YYYY-MM-DD" in res.get_json()["message"]

    # 4. Non-calendar date (February 30)
    res = client.post("/api/sessions", json={"course_name": "Valid Course", "session_date": "2026-02-30", "start_time": "09:00", "end_time": "10:00"})
    assert res.status_code == 400
    assert "not a valid calendar date" in res.get_json()["message"]

    # 5. Invalid time format
    res = client.post("/api/sessions", json={"course_name": "Valid Course", "session_date": "2026-11-08", "start_time": "25:70", "end_time": "10:00"})
    assert res.status_code == 400
    assert "24-hour" in res.get_json()["message"]


def test_10_activation_request_uses_correct_endpoint(client):
    """Verify PATCH /api/sessions/<id>/activate activates the session and deactivates prior ones."""
    # Create two sessions
    res1 = client.post("/api/sessions", json={"course_name": "Session 1", "session_date": "2026-11-08", "start_time": "09:00", "end_time": "10:00"})
    id1 = res1.get_json()["session"]["id"]

    res2 = client.post("/api/sessions", json={"course_name": "Session 2", "session_date": "2026-11-08", "start_time": "11:00", "end_time": "12:00"})
    id2 = res2.get_json()["session"]["id"]

    # Activate session 1
    act1 = client.patch(f"/api/sessions/{id1}/activate")
    assert act1.status_code == 200
    assert act1.get_json()["success"] is True

    # Verify session 1 is active
    active_res = client.get("/api/sessions/active")
    assert active_res.status_code == 200
    assert active_res.get_json()["session"]["id"] == id1

    # Activate session 2 - should atomically deactivate session 1
    act2 = client.patch(f"/api/sessions/{id2}/activate")
    assert act2.status_code == 200
    assert act2.get_json()["success"] is True

    # Verify session 2 is now the active session
    active_res2 = client.get("/api/sessions/active")
    assert active_res2.get_json()["session"]["id"] == id2


def test_11_deactivation_request_uses_correct_endpoint(client):
    """Verify PATCH /api/sessions/<id>/deactivate deactivates the session."""
    # Create and activate session
    res = client.post("/api/sessions", json={"course_name": "Physics 101", "session_date": "2026-11-08", "start_time": "10:00", "end_time": "11:00"})
    sess_id = res.get_json()["session"]["id"]

    client.patch(f"/api/sessions/{sess_id}/activate")

    # Deactivate session
    deact = client.patch(f"/api/sessions/{sess_id}/deactivate")
    assert deact.status_code == 200
    assert deact.get_json()["success"] is True
    assert "deactivated" in deact.get_json()["message"].lower()

    # Verify no active session
    active_res = client.get("/api/sessions/active")
    assert active_res.get_json()["session"] is None


def test_12_activation_api_failure_handling(client):
    """Verify activation endpoint returns 404 for non-existent session and 400 for invalid ID."""
    # Non-existent session ID -> 404
    res_404 = client.patch("/api/sessions/99999/activate")
    assert res_404.status_code == 404
    assert res_404.get_json()["success"] is False
    assert "does not exist" in res_404.get_json()["message"]

    # Invalid ID (0 or negative) -> 400
    res_400 = client.patch("/api/sessions/0/activate")
    assert res_400.status_code == 400
    assert res_400.get_json()["success"] is False


def test_13_deactivation_api_failure_handling(client):
    """Verify deactivation endpoint returns 404 for non-existent session and 400 for invalid ID."""
    # Non-existent session ID -> 404
    res_404 = client.patch("/api/sessions/99999/deactivate")
    assert res_404.status_code == 404
    assert res_404.get_json()["success"] is False
    assert "does not exist" in res_404.get_json()["message"]

    # Invalid ID (0) -> 400
    res_400 = client.patch("/api/sessions/0/deactivate")
    assert res_400.status_code == 400
    assert res_400.get_json()["success"] is False
    assert "positive integer" in res_400.get_json()["message"]


def test_14_no_active_session_state_displayed_appropriately(client):
    """Verify that when no session is active, the active endpoint returns null and view renders fallback structure."""
    res_active = client.get("/api/sessions/active")
    assert res_active.status_code == 200
    assert res_active.get_json()["session"] is None

    # Verify page contains default fallback markup
    res_view = client.get("/sessions")
    html = res_view.get_data(as_text=True)
    assert 'id="active-session-card"' in html
    assert 'id="active-status-label"' in html
    assert 'id="btn-deactivate-active-session"' in html
    # The deactivate button has inline style="display: none;" by default
    assert 'id="btn-deactivate-active-session" style="display: none;"' in html


def test_15_existing_student_and_attendance_functionality_unaffected(client):
    """Verify student registration, session creation, activation, and attendance scan work seamlessly together."""
    # 1. Register a student
    student_payload = {
        "roll_number": "CS-999",
        "full_name": "Regression Test Student",
        "card_uid": "A9B8C7D6"
    }
    res_student = client.post("/api/students", json=student_payload)
    assert res_student.status_code == 201

    # 2. Create and activate a session
    session_payload = {
        "course_name": "Integrated Systems",
        "session_date": "2026-11-08",
        "start_time": "15:00",
        "end_time": "16:00"
    }
    res_sess = client.post("/api/sessions", json=session_payload)
    assert res_sess.status_code == 201
    sess_id = res_sess.get_json()["session"]["id"]

    res_act = client.patch(f"/api/sessions/{sess_id}/activate")
    assert res_act.status_code == 200

    # 3. Record attendance scan
    scan_res = client.post("/api/scans", json={"card_uid": "A9B8C7D6", "source": "test_scanner"})
    assert scan_res.status_code == 201
    assert scan_res.get_json()["success"] is True

    # 4. Check duplicate scan is handled correctly (returns 409 Conflict)
    dup_res = client.post("/api/scans", json={"card_uid": "A9B8C7D6", "source": "test_scanner"})
    assert dup_res.status_code == 409
    assert dup_res.get_json()["success"] is False
    assert "already been recorded" in dup_res.get_json()["message"]

    # 5. Check daily attendance query for session date
    att_res = client.get("/api/attendance?date=2026-11-08")
    assert att_res.status_code == 200
    assert att_res.get_json()["count"] == 1

    # 6. Check daily report
    rep_res = client.get("/api/reports/daily?date=2026-11-08")
    assert rep_res.status_code == 200
    report_data = rep_res.get_json()
    assert report_data["success"] is True
    assert report_data["summary"]["total_present"] == 1
    assert len(report_data["sessions"]) == 1
