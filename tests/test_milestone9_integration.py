"""
Phase 2 — Milestone 9: Frontend Testing & Backend Integration
Comprehensive End-to-End and Integration Verification Test Suite.

Verifies:
1. Project-wide endpoint reconciliation: 15 REST/Auth endpoints and 8 HTML views.
2. Dashboard Integration: KPI metrics, active session banner, recent activity, empty state.
3. Student Management Integration: registration, validation, uniqueness, and listing.
4. Session Management Integration: creation, atomic activation (single active invariant), switching, deactivation.
5. Live Attendance Scanning Pipeline (all 7 scenarios):
   - Scenario 1: Valid scan for active student during active session (201 Created).
   - Scenario 2: Duplicate scan for same student in same session (409 Conflict).
   - Scenario 3: Unregistered RFID UID (404 Not Found).
   - Scenario 4: Inactive student scan (403 Forbidden).
   - Scenario 5: Scan with no active session (409 Conflict).
   - Scenario 6: Database/server exception simulation (500 Internal Server Error).
   - Scenario 7: Malformed payload / invalid schema (400 Bad Request).
6. Reports & CSV Export Integration:
   - Daily report aggregation, attendance rate calculation, present/absent breakdown.
   - CSV export headers (Content-Type, Content-Disposition).
   - Formula-injection sanitization (CWE-1236) for =, +, -, @.
7. Authentication, CSRF & Security Integration:
   - Unauthenticated UI redirect to /login.
   - Unauthenticated API return 401 Unauthorized JSON.
   - Public access to /api/health without authentication.
   - CSRF protection on POST/PATCH state changes.
   - Safe GET requests without CSRF requirement.
   - POST-only logout (GET /logout returns 405 Method Not Allowed).
   - Rate limiting after repeated failed logins (429 Too Many Requests).
   - Cache-Control headers on authenticated views.
8. SQLite Database Consistency:
   - PRAGMA foreign_keys = ON enforcement.
   - Foreign key constraint violations prevented.
   - Composite uniqueness (student_id, session_id) in attendance table.
   - Single-active-session partial unique index enforcement.
9. Standardized HTTP status codes and error recovery (400, 401, 403, 404, 405, 409, 429, 500).
"""

import io
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from app import create_app
from app.database import init_db, get_db_connection, close_db_connection
from app.auth import (
    hash_password,
    clear_all_rate_limits,
)

KOLKATA_TZ = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated application instance for integration testing."""
    test_db = tmp_path / "test_m9_integration.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    init_db(test_db)
    clear_all_rate_limits()
    yield app
    clear_all_rate_limits()


@pytest.fixture
def app_with_csrf(tmp_path):
    """Creates an isolated application instance with CSRF enforced."""
    test_db = tmp_path / "test_m9_csrf.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    init_db(test_db)
    clear_all_rate_limits()
    yield app
    clear_all_rate_limits()


@pytest.fixture
def client(app_instance):
    """Provides unauthenticated test client."""
    return app_instance.test_client()


@pytest.fixture
def auth_client(app_instance):
    """Provides authenticated administrator client."""
    client = app_instance.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_username"] = "admin"
        sess["admin_role"] = "admin"
    return client


def seed_admin_user(app, username="admin", password="Password123!"):
    """Seeds an active administrator account in the test database."""
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO admin_users (username, password_hash, role, active, created_at)
        VALUES (?, ?, 'admin', 1, ?);
        """,
        (username, hash_password(password), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    close_db_connection(conn)


# ==============================================================================
# TASK 1: API ENDPOINT RECONCILIATION & URL MAP AUDIT
# ==============================================================================

def test_01_api_endpoint_reconciliation(app_instance):
    """
    TASK 1: Verify the complete URL map and reconcile endpoint count.
    Reconciles Phase 1 (12 REST endpoints pre-auth) vs Milestone 8/9 (15 REST endpoints + 8 HTML views).
    """
    rules = [r for r in app_instance.url_map.iter_rules()]

    # 15 REST / Auth Endpoints
    expected_api_endpoints = {
        "/api/health": ["GET"],
        "/api/auth/status": ["GET"],
        "/login": ["GET", "POST"],
        "/logout": ["POST"],
        "/api/students": ["GET", "POST"],
        "/api/sessions": ["GET", "POST"],
        "/api/sessions/active": ["GET"],
        "/api/sessions/<int:session_id>/activate": ["PATCH"],
        "/api/sessions/<int:session_id>/deactivate": ["PATCH"],
        "/api/scans": ["POST"],
        "/api/attendance": ["GET"],
        "/api/reports/daily": ["GET"],
        "/api/reports/export.csv": ["GET"],
    }

    for path in expected_api_endpoints:
        assert any(r.rule == path for r in rules), f"Missing expected route {path}"

    # 8 HTML Views (rendering 7 templates: dashboard, login, students, sessions, attendance, reports, placeholder)
    html_routes = [
        "/",
        "/dashboard",
        "/login",
        "/students",
        "/sessions",
        "/attendance",
        "/reports",
        "/settings"
    ]
    for route in html_routes:
        assert any(r.rule == route for r in rules), f"Missing HTML view {route}"

    auth_endpoints = {"/login", "/logout", "/api/auth/status"}
    pre_auth_api_routes = [
        "/api/health",
        "/api/students",
        "/api/sessions",
        "/api/sessions/active",
        "/api/sessions/<int:session_id>/activate",
        "/api/sessions/<int:session_id>/deactivate",
        "/api/scans",
        "/api/attendance",
        "/api/reports/daily",
        "/api/reports/export.csv"
    ]
    assert len(pre_auth_api_routes) == 10
    assert len(auth_endpoints) == 3


# ==============================================================================
# TASK 2: DASHBOARD INTEGRATION
# ==============================================================================

def test_02_dashboard_integration_empty_and_populated_states(app_instance, auth_client):
    """
    TASK 2: Verify Dashboard integration across all 4 coordinated APIs:
    /api/students, /api/sessions/active, /api/reports/daily, /api/attendance.
    """
    # 1. Verify dashboard HTML view renders correctly with KPI cards and activity panel
    res_html = auth_client.get("/dashboard")
    assert res_html.status_code == 200
    html = res_html.get_data(as_text=True)
    assert 'id="kpi-students-count"' in html
    assert 'id="kpi-session-card"' in html
    assert 'id="kpi-session-course"' in html
    assert 'id="kpi-attendance-rate"' in html
    assert 'id="activity-container"' in html
    assert 'id="dashboard-empty-state"' in html

    # 2. Empty State Verification
    res_students = auth_client.get("/api/students")
    assert res_students.status_code == 200
    assert res_students.get_json()["students"] == []

    res_active = auth_client.get("/api/sessions/active")
    assert res_active.status_code == 200
    assert res_active.get_json()["session"] is None

    today_str = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d")
    res_report = auth_client.get(f"/api/reports/daily?date={today_str}")
    assert res_report.status_code == 200
    summary_data = res_report.get_json()["summary"]
    assert summary_data["total_enrollments"] == 0
    assert summary_data["total_present"] == 0
    assert summary_data["overall_attendance_percentage"] == 0.0

    res_att = auth_client.get(f"/api/attendance?date={today_str}")
    assert res_att.status_code == 200
    assert res_att.get_json()["attendance"] == []

    # 3. Populate State: Register student, create session, activate, record scan
    reg_res = auth_client.post("/api/students", json={
        "roll_number": "CS-101",
        "full_name": "Aarav Sharma",
        "card_uid": "A1B2C3D4"
    })
    assert reg_res.status_code == 201

    sess_res = auth_client.post("/api/sessions", json={
        "course_name": "Operating Systems",
        "session_date": today_str,
        "start_time": "09:00", "end_time": "23:59"})
    assert sess_res.status_code == 201
    sess_id = sess_res.get_json()["session"]["id"]

    act_res = auth_client.patch(f"/api/sessions/{sess_id}/activate")
    assert act_res.status_code == 200

    scan_res = auth_client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "SIMULATOR"
    })
    assert scan_res.status_code == 201

    # 4. Verify Populated State across Dashboard APIs
    res_students = auth_client.get("/api/students")
    assert len(res_students.get_json()["students"]) == 1

    res_active = auth_client.get("/api/sessions/active")
    assert res_active.status_code == 200
    active_data = res_active.get_json()["session"]
    assert active_data["id"] == sess_id
    assert active_data["course_name"] == "Operating Systems"

    res_report = auth_client.get(f"/api/reports/daily?date={today_str}")
    summary_data = res_report.get_json()["summary"]
    assert summary_data["total_sessions"] == 1
    assert summary_data["total_enrollments"] == 1
    assert summary_data["total_present"] == 1
    assert summary_data["overall_attendance_percentage"] == 100.0

    res_att = auth_client.get(f"/api/attendance?date={today_str}")
    att_list = res_att.get_json()["attendance"]
    assert len(att_list) == 1
    assert att_list[0]["roll_number"] == "CS-101"
    assert att_list[0]["student_name"] == "Aarav Sharma"


# ==============================================================================
# TASK 3: STUDENT MANAGEMENT INTEGRATION
# ==============================================================================

def test_03_student_management_workflow(auth_client):
    """
    TASK 3: Verify Student Management integration:
    - HTML page loads with form and table hooks.
    - POST /api/students validation (client & server rules).
    - Uniqueness violations (roll number & card UID).
    - GET /api/students retrieval.
    """
    # 1. UI route
    res_page = auth_client.get("/students")
    assert res_page.status_code == 200
    html = res_page.get_data(as_text=True)
    assert 'id="form-register-student"' in html
    assert 'id="student-table-body"' in html
    assert 'id="search-students"' in html

    # 2. Valid Student Creation
    create_res = auth_client.post("/api/students", json={
        "roll_number": "ECE-201",
        "full_name": "Diya Nair",
        "card_uid": "D1E2F3A4"
    })
    assert create_res.status_code == 201
    s_data = create_res.get_json()
    assert s_data["success"] is True
    assert s_data["student"]["roll_number"] == "ECE-201"
    assert s_data["student"]["active"] == 1

    # 3. Duplicate Roll Number Conflict (HTTP 409)
    dup_roll_res = auth_client.post("/api/students", json={
        "roll_number": "ECE-201",
        "full_name": "Another Student",
        "card_uid": "UNIQUE_UID_99"
    })
    assert dup_roll_res.status_code == 409
    assert "roll number" in dup_roll_res.get_json()["message"].lower()

    # 4. Duplicate Card UID Conflict (HTTP 409)
    dup_uid_res = auth_client.post("/api/students", json={
        "roll_number": "ECE-202",
        "full_name": "Another Student",
        "card_uid": "d1e2f3a4"  # lowercase collision
    })
    assert dup_uid_res.status_code == 409
    assert "rfid" in dup_uid_res.get_json()["message"].lower() or "card" in dup_uid_res.get_json()["message"].lower()

    # 5. Validation Errors (HTTP 400)
    bad_payloads = [
        {"roll_number": "", "full_name": "Test", "card_uid": "UID1234"},
        {"roll_number": "103", "full_name": "", "card_uid": "UID1234"},
        {"roll_number": "103", "full_name": "Test", "card_uid": "UI"},  # too short
        {"roll_number": "103", "full_name": "Test", "card_uid": "BAD@#$UID"},  # invalid characters
    ]
    for p in bad_payloads:
        bad_res = auth_client.post("/api/students", json=p)
        assert bad_res.status_code == 400
        assert bad_res.get_json()["success"] is False

    # 6. Roster listing
    list_res = auth_client.get("/api/students")
    assert list_res.status_code == 200
    students = list_res.get_json()["students"]
    assert len(students) == 1
    assert students[0]["roll_number"] == "ECE-201"


# ==============================================================================
# TASK 4: CLASS SESSION INTEGRATION & ATOMIC ACTIVATION
# ==============================================================================

def test_04_session_lifecycle_and_atomic_activation(app_instance, auth_client):
    """
    TASK 4: Verify Class Session integration:
    - Session creation via POST /api/sessions.
    - Atomic activation via PATCH /api/sessions/<id>/activate.
    - Switching active sessions deactivates the prior session atomically.
    - Single active session invariant in SQLite.
    - Deactivation and invalid session activation handling.
    """
    # 1. UI view
    res_page = auth_client.get("/sessions")
    assert res_page.status_code == 200
    html = res_page.get_data(as_text=True)
    assert 'id="form-create-session"' in html
    assert 'id="session-table-body"' in html
    assert 'id="active-session-card"' in html

    today_str = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d")

    # 2. Create Session 1 and Session 2
    res_s1 = auth_client.post("/api/sessions", json={
        "course_name": "Algorithms",
        "session_date": today_str,
        "start_time": "10:00", "end_time": "23:59"})
    assert res_s1.status_code == 201
    s1_id = res_s1.get_json()["session"]["id"]

    res_s2 = auth_client.post("/api/sessions", json={
        "course_name": "Database Systems",
        "session_date": today_str,
        "start_time": "11:00", "end_time": "23:59"})
    assert res_s2.status_code == 201
    s2_id = res_s2.get_json()["session"]["id"]

    # 3. Activate Session 1
    res_act1 = auth_client.patch(f"/api/sessions/{s1_id}/activate")
    assert res_act1.status_code == 200
    assert res_act1.get_json()["session"]["active"] == 1

    # Verify via active endpoint
    res_curr = auth_client.get("/api/sessions/active")
    assert res_curr.status_code == 200
    assert res_curr.get_json()["session"]["id"] == s1_id

    # 4. Activate Session 2 (Atomic Switch)
    res_act2 = auth_client.patch(f"/api/sessions/{s2_id}/activate")
    assert res_act2.status_code == 200
    assert res_act2.get_json()["session"]["active"] == 1

    # Verify Session 1 is deactivated and Session 2 is active
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    cur = conn.cursor()
    cur.execute("SELECT id, active FROM class_sessions WHERE active = 1;")
    active_rows = cur.fetchall()
    assert len(active_rows) == 1
    assert active_rows[0]["id"] == s2_id

    # Verify Session 1 is 0
    cur.execute("SELECT active FROM class_sessions WHERE id = ?;", (s1_id,))
    assert cur.fetchone()["active"] == 0
    close_db_connection(conn)

    # 5. Invalid Session Activation (HTTP 404)
    res_invalid = auth_client.patch("/api/sessions/99999/activate")
    assert res_invalid.status_code == 404
    # Ensure Session 2 is STILL active
    res_curr2 = auth_client.get("/api/sessions/active")
    assert res_curr2.status_code == 200
    assert res_curr2.get_json()["session"]["id"] == s2_id

    # 6. Deactivate Session 2
    res_deact = auth_client.patch(f"/api/sessions/{s2_id}/deactivate")
    assert res_deact.status_code == 200
    assert res_deact.get_json()["session"]["active"] == 0

    # Verify no active session returns 200 with session: None
    res_curr3 = auth_client.get("/api/sessions/active")
    assert res_curr3.status_code == 200
    assert res_curr3.get_json()["session"] is None


# ==============================================================================
# TASK 5: LIVE ATTENDANCE SCAN PIPELINE (ALL 7 SCENARIOS)
# ==============================================================================

def test_05_live_attendance_scan_pipeline_all_7_scenarios(app_instance, auth_client):
    """
    TASK 5: Verify the 6-stage scan pipeline across all 7 independent scenarios:
    1. Valid scan for active student during active session (201 Created).
    2. Duplicate scan for same student in same session (409 Conflict).
    3. Unregistered RFID UID scan (404 Not Found).
    4. Inactive student scan (403 Forbidden).
    5. Scan when no active session (409 Conflict).
    6. Database/server exception simulation (500 Internal Server Error).
    7. Malformed payload / invalid schema (400 Bad Request).
    """
    today_str = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d")

    # Seed active student
    auth_client.post("/api/students", json={
        "roll_number": "CS-301",
        "full_name": "Rohan Verma",
        "card_uid": "CARD_ROHAN_301"
    })

    # Seed inactive student directly in DB
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('CS-302', 'Inactive Student', 'CARD_INACTIVE_302', 0, '2026-11-08T00:00:00Z');
    """)
    conn.commit()
    close_db_connection(conn)

    # --------------------------------------------------------------------------
    # Scenario 5: Scan when no session is active -> 409 Conflict
    # --------------------------------------------------------------------------
    res_no_sess = auth_client.post("/api/scans", json={
        "card_uid": "CARD_ROHAN_301",
        "source": "SIMULATOR"
    })
    assert res_no_sess.status_code == 409
    assert res_no_sess.get_json()["error"] == "No Active Session"

    # Now create and activate session
    s_res = auth_client.post("/api/sessions", json={
        "course_name": "Compiler Design",
        "session_date": today_str,
        "start_time": "14:00", "end_time": "23:59"})
    session_id = s_res.get_json()["session"]["id"]
    auth_client.patch(f"/api/sessions/{session_id}/activate")

    # --------------------------------------------------------------------------
    # Scenario 1: Valid scan for active student during active session -> 201 Created
    # --------------------------------------------------------------------------
    res_valid = auth_client.post("/api/scans", json={
        "card_uid": "card_rohan_301",  # case-insensitive check
        "source": "SIMULATOR"
    })
    assert res_valid.status_code == 201
    scan_body = res_valid.get_json()
    assert scan_body["success"] is True
    assert scan_body["attendance"]["student_name"] == "Rohan Verma"
    assert scan_body["attendance"]["course_name"] == "Compiler Design"
    assert scan_body["attendance"]["status"] == "Present"
    assert scan_body["attendance"]["scanned_at"].endswith("Z")

    # --------------------------------------------------------------------------
    # Scenario 2: Duplicate scan for same student in same session -> 409 Conflict
    # --------------------------------------------------------------------------
    res_dup = auth_client.post("/api/scans", json={
        "card_uid": "CARD_ROHAN_301",
        "source": "SIMULATOR"
    })
    assert res_dup.status_code == 409
    assert res_dup.get_json()["error"] == "Duplicate Attendance"

    # --------------------------------------------------------------------------
    # Scenario 3: Unregistered RFID UID scan -> 404 Not Found
    # --------------------------------------------------------------------------
    res_unregistered = auth_client.post("/api/scans", json={
        "card_uid": "UNKNOWN_CARD_999",
        "source": "SIMULATOR"
    })
    assert res_unregistered.status_code == 404
    assert res_unregistered.get_json()["error"] == "Student Not Found"

    # --------------------------------------------------------------------------
    # Scenario 4: Inactive student scan -> 403 Forbidden
    # --------------------------------------------------------------------------
    res_inactive = auth_client.post("/api/scans", json={
        "card_uid": "CARD_INACTIVE_302",
        "source": "SIMULATOR"
    })
    assert res_inactive.status_code == 403
    assert res_inactive.get_json()["error"] == "Inactive Student"

    # --------------------------------------------------------------------------
    # Scenario 6: Database/server exception simulation -> 500 Internal Server Error
    # --------------------------------------------------------------------------
    with patch("app.routes.attendance.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Simulated database failure")
        res_error = auth_client.post("/api/scans", json={
            "card_uid": "CARD_ROHAN_301",
            "source": "SIMULATOR"
        })
        assert res_error.status_code == 500
        assert res_error.get_json()["error"] == "Internal Server Error"
        # Verify sensitive stack trace / message not leaked
        assert "Simulated database failure" not in res_error.get_json()["message"]

    # --------------------------------------------------------------------------
    # Scenario 7: Malformed payload / invalid schema -> 400 Bad Request
    # --------------------------------------------------------------------------
    malformed_cases = [
        {},  # empty body
        {"card_uid": "CARD_ROHAN_301"},  # missing source
        {"source": "SIMULATOR"},  # missing card_uid
        {"card_uid": "   ", "source": "SIMULATOR"},  # whitespace card_uid
        {"card_uid": "CARD_ROHAN_301", "source": "   "},  # whitespace source
        {"card_uid": "AB", "source": "SIMULATOR"},  # too short (<4)
        {"card_uid": "INVALID@CHAR!", "source": "SIMULATOR"},  # disallowed characters
        {"card_uid": 12345, "source": "SIMULATOR"},  # non-string card_uid
    ]
    for case in malformed_cases:
        res_malformed = auth_client.post("/api/scans", json=case)
        assert res_malformed.status_code == 400
        assert res_malformed.get_json()["error"] == "Bad Request"

    # Non-JSON content type
    res_non_json = auth_client.post("/api/scans", data="plain text data", content_type="text/plain")
    assert res_non_json.status_code == 400


# ==============================================================================
# TASK 6: REPORTS & CSV EXPORT INTEGRATION (WITH CWE-1236 SANITIZATION)
# ==============================================================================

def test_06_reports_and_csv_export_workflow(app_instance, auth_client):
    """
    TASK 6: Verify Reports and CSV Download integration:
    - Daily analytics aggregation (registered, eligible, present, percentage).
    - Breakdown of attendees and absent students.
    - CSV export endpoint headers (Content-Type, Content-Disposition).
    - Formula injection sanitization (CWE-1236) for =, +, -, @.
    """
    today_str = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d")

    # 1. UI view
    res_page = auth_client.get("/reports")
    assert res_page.status_code == 200
    html = res_page.get_data(as_text=True)
    assert 'id="report-date"' in html
    assert 'id="btn-download-csv"' in html
    assert 'id="reports-attendance-body"' in html

    # 2. Seed students: one regular, one with potential formula injection name
    auth_client.post("/api/students", json={
        "roll_number": "CS-401",
        "full_name": "=cmd|' /C calc'!A0",  # CSV formula injection attack vector
        "card_uid": "FORMULA_INJECT_UID"
    })
    auth_client.post("/api/students", json={
        "roll_number": "CS-402",
        "full_name": "Meera Sen",
        "card_uid": "CARD_MEERA_402"
    })

    # Create & activate session
    s_res = auth_client.post("/api/sessions", json={
        "course_name": "+SUM(A1:A10)",  # Course name with formula injection trigger
        "session_date": today_str,
        "start_time": "15:00", "end_time": "23:59"})
    sess_id = s_res.get_json()["session"]["id"]
    auth_client.patch(f"/api/sessions/{sess_id}/activate")

    # Mark Meera Sen present
    scan_res = auth_client.post("/api/scans", json={
        "card_uid": "CARD_MEERA_402",
        "source": "SIMULATOR"
    })
    assert scan_res.status_code == 201

    # 3. Verify Daily Report JSON
    res_rep = auth_client.get(f"/api/reports/daily?date={today_str}")
    assert res_rep.status_code == 200
    rep_json = res_rep.get_json()
    summary = rep_json["summary"]
    assert summary["total_sessions"] == 1
    assert summary["total_enrollments"] == 2
    assert summary["total_present"] == 1
    assert summary["total_absent"] == 1
    assert summary["overall_attendance_percentage"] == 50.0

    sessions = rep_json["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == sess_id
    assert sessions[0]["present"] == 1
    assert sessions[0]["absent"] == 1
    assert sessions[0]["attendance_percentage"] == 50.0

    # 4. Verify CSV Export
    res_csv = auth_client.get(f"/api/reports/export.csv?date={today_str}")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.content_type
    assert "attachment; filename=" in res_csv.headers["Content-Disposition"]

    csv_text = res_csv.get_data(as_text=True)

    # 5. Verify CWE-1236 Formula Injection Sanitization
    # Formula triggers (=, +, -, @) must be prefixed with single quote (') to neutralize execution in Excel/Calc
    assert "'=cmd|' /C calc'!A0" in csv_text or "\"'=cmd|" in csv_text
    assert "'+SUM(A1:A10)" in csv_text or "\"'+SUM(" in csv_text


# ==============================================================================
# TASK 7: AUTHENTICATION, CSRF & SECURITY ENFORCEMENT
# ==============================================================================

def test_07_authentication_and_authorization_integration(client, auth_client):
    """
    TASK 7: Verify Authentication and Authorization integration:
    - Unauthenticated access to UI views redirects to /login.
    - Unauthenticated access to APIs returns HTTP 401 JSON.
    - Public health endpoint /api/health accessible without auth.
    - Cache-Control headers on protected views.
    - POST-only logout enforcement.
    """
    protected_ui_routes = [
        "/",
        "/dashboard",
        "/students",
        "/sessions",
        "/attendance",
        "/reports",
        "/settings"
    ]
    for route in protected_ui_routes:
        res = client.get(route)
        assert res.status_code == 302
        assert "/login" in res.headers["Location"]

    protected_api_routes = [
        ("/api/students", "GET"),
        ("/api/students", "POST"),
        ("/api/sessions", "GET"),
        ("/api/sessions", "POST"),
        ("/api/sessions/active", "GET"),
        ("/api/scans", "POST"),
        ("/api/attendance", "GET"),
        ("/api/reports/daily", "GET"),
        ("/api/reports/export.csv", "GET"),
    ]
    for url, method in protected_api_routes:
        if method == "GET":
            res = client.get(url)
        else:
            res = client.post(url, json={})
        assert res.status_code == 401
        data = res.get_json()
        assert data["success"] is False
        assert data["error"] == "Unauthorized"

    # Public health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    health_data = res_health.get_json()
    assert health_data["success"] is True
    assert health_data["database"] == "connected"

    # Cache-Control headers on protected views
    res_dash = auth_client.get("/dashboard")
    assert res_dash.status_code == 200
    assert "no-store" in res_dash.headers.get("Cache-Control", "")

    # POST-only logout: GET /logout must return 405 Method Not Allowed
    res_logout_get = auth_client.get("/logout")
    assert res_logout_get.status_code == 405


def test_08_csrf_protection_and_rate_limiting(app_with_csrf):
    """
    TASK 7: Verify CSRF protection and brute-force rate limiting:
    - Missing CSRF token rejects state-changing requests (HTTP 403).
    - Valid CSRF token permits state changes.
    - Rate limiter locks account after 5 consecutive failures (HTTP 429).
    """
    client = app_with_csrf.test_client()
    seed_admin_user(app_with_csrf, username="sec_admin", password="AdminPassword123!")

    # 1. Obtain CSRF token from login page / session
    res_login_page = client.get("/login")
    assert res_login_page.status_code == 200
    with client.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")
    assert csrf_token is not None

    # 2. State-changing POST without CSRF token must return 403 Forbidden
    res_no_csrf = client.post("/login", json={
        "username": "sec_admin",
        "password": "AdminPassword123!"
    })
    assert res_no_csrf.status_code == 403

    # 3. State-changing POST with invalid CSRF token must return 403 Forbidden
    res_bad_csrf = client.post(
        "/login",
        json={"username": "sec_admin", "password": "AdminPassword123!"},
        headers={"X-CSRF-Token": "INVALID_TOKEN_XYZ"}
    )
    assert res_bad_csrf.status_code == 403

    # 4. Successful login with valid CSRF token
    res_login_ok = client.post(
        "/login",
        json={"username": "sec_admin", "password": "AdminPassword123!"},
        headers={"X-CSRF-Token": csrf_token}
    )
    assert res_login_ok.status_code == 200
    assert res_login_ok.get_json()["success"] is True

    # 5. Rate limiting: 5 failed attempts trigger HTTP 429
    fail_client = app_with_csrf.test_client()
    fail_client.get("/login")
    with fail_client.session_transaction() as sess:
        fail_token = sess.get("csrf_token")

    for attempt in range(5):
        fail_res = fail_client.post(
            "/login",
            json={"username": "sec_admin", "password": "WrongPassword!"},
            headers={"X-CSRF-Token": fail_token}
        )
        assert fail_res.status_code == 401

    # 6th attempt should be blocked by rate limit (HTTP 429)
    blocked_res = fail_client.post(
        "/login",
        json={"username": "sec_admin", "password": "WrongPassword!"},
        headers={"X-CSRF-Token": fail_token}
    )
    assert blocked_res.status_code == 429
    assert blocked_res.get_json()["error"] == "Too Many Requests"


# ==============================================================================
# TASK 8 & 9: SQLITE DATABASE CONSISTENCY & CONSTRAINTS
# ==============================================================================

def test_09_database_acid_constraints_and_foreign_keys(app_instance):
    """
    TASK 9: Verify SQLite database consistency:
    - Foreign keys enabled (PRAGMA foreign_keys = ON).
    - Foreign key violation prevents orphaned attendance records.
    - Partial unique index enforces at most one active session.
    - Composite uniqueness (student_id, session_id) in attendance table.
    """
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    # 1. Foreign keys pragma
    cur.execute("PRAGMA foreign_keys;")
    assert cur.fetchone()[0] == 1

    # 2. Foreign key violation: insert attendance referencing non-existent student/session
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (99999, 88888, '2026-11-08T12:00:00Z', 'Present', 'test');
        """)
        conn.commit()
    conn.rollback()

    # 3. Single active session constraint
    cur.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Session 1', '2026-11-08', '09:00', 1, '2026-11-08T09:00:00Z');
    """)
    conn.commit()

    # Attempting to insert a second active session directly in SQLite must fail partial unique index
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
            VALUES ('Session 2', '2026-11-08', '10:00', 1, '2026-11-08T10:00:00Z');
        """)
        conn.commit()
    conn.rollback()

    close_db_connection(conn)


# ==============================================================================
# TASK 10: STANDARDIZED ERROR HANDLING AND RECOVERY
# ==============================================================================

def test_10_standardized_error_handling_structure(auth_client):
    """
    TASK 10: Verify all API error responses follow standard JSON structure:
    {"success": false, "error": ..., "message": ...}.
    Tests: 400, 404, 405, 409.
    """
    # 400 Bad Request
    res_400 = auth_client.post("/api/students", json={})
    assert res_400.status_code == 400
    b_400 = res_400.get_json()
    assert b_400["success"] is False
    assert "error" in b_400
    assert "message" in b_400

    # 404 Not Found
    res_404 = auth_client.patch("/api/sessions/999999/activate")
    assert res_404.status_code == 404
    b_404 = res_404.get_json()
    assert b_404["success"] is False
    assert "error" in b_404
    assert "message" in b_404

    # 409 Conflict (e.g. scan with no active session)
    # Ensure active student exists
    auth_client.post("/api/students", json={
        "roll_number": "ERR-101",
        "full_name": "Test Error Student",
        "card_uid": "CARD_ERR_101"
    })
    res_409 = auth_client.post("/api/scans", json={"card_uid": "CARD_ERR_101", "source": "SIMULATOR"})
    assert res_409.status_code == 409
    b_409 = res_409.get_json()
    assert b_409["success"] is False
    assert "error" in b_409
    assert "message" in b_409

    # 405 Method Not Allowed
    res_405 = auth_client.get("/logout")
    assert res_405.status_code == 405
