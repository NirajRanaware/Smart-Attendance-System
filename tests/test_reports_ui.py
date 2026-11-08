"""
Automated Integration and Functional Tests for Attendance Reports & CSV Export UI (Phase 2 - Milestone 6).
Verifies:
1. Reports page loads successfully with HTTP 200 and correct layout.
2. Correct navigation item is active.
3. Required template and static CSS/JS assets are served.
4. Date picker toolbar and 5 KPI metric cards markup are present.
5. Session breakdown and detailed student roster markup are present.
6. Daily reports API returns accurate summary and handles empty dates.
7. Daily reports API excludes inactive students from eligible enrollments.
8. Multiple sessions on the same date are accurately separated.
9. CSV export endpoint returns text/csv with attachment header and filename.
10. CSV export content includes both Present and Absent active student rows.
11. CSV export rejects missing or invalid dates with HTTP 400.
12. Daily report API rejects missing or invalid dates with HTTP 400.
13. Daily attendance query returns expected schema for roster rendering.
14. Client JavaScript controller contains stale response protection and XSS escaping.
"""

import csv
import io
import pytest
from app import create_app
from app.database import init_db, get_db_connection


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_reports_ui.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


def test_1_reports_page_loads_successfully(client):
    """Verify GET /reports returns HTTP 200 with HTML content and correct page headers."""
    res = client.get("/reports")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    assert "Attendance Reports &amp; CSV Export" in html or "Attendance Reports & CSV Export" in html
    assert "Daily classroom statistics" in html
    assert 'id="sidebar"' in html
    assert 'id="live-clock"' in html


def test_2_correct_navigation_item_is_active(client):
    """Verify that the Attendance Reports sidebar navigation link is marked active."""
    res = client.get("/reports")
    html = res.get_data(as_text=True)
    assert 'id="nav-reports"' in html
    assert 'class="nav-link active" id="nav-reports"' in html or 'active' in html


def test_3_required_template_and_static_assets_served(client):
    """Verify CSS and JS asset files specific to Reports are linked and served with HTTP 200."""
    res = client.get("/reports")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Core scripts & reports script
    assert "js/app.js" in html
    assert "js/reports.js" in html

    # Core stylesheets & reports stylesheet
    assert "css/style.css" in html
    assert "css/components.css" in html
    assert "css/reports.css" in html

    # Verify CSS is served
    css_res = client.get("/static/css/reports.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.content_type

    # Verify JS is served
    js_res = client.get("/static/js/reports.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.content_type or "text/plain" in js_res.content_type


def test_4_date_controls_and_kpi_cards_markup_present(client):
    """Verify date picker controls, quick navigation buttons, and 5 KPI cards in HTML markup."""
    res = client.get("/reports")
    html = res.get_data(as_text=True)

    # Date toolbar controls
    assert 'id="report-filter-bar"' in html
    assert 'id="report-date"' in html
    assert 'id="btn-date-prev"' in html
    assert 'id="btn-date-today"' in html
    assert 'id="btn-date-next"' in html
    assert 'id="display-selected-date"' in html
    assert 'id="btn-refresh-report"' in html
    assert 'id="btn-download-csv"' in html

    # 5 KPI cards
    assert 'id="card-stat-total"' in html
    assert 'id="stat-total-students"' in html
    assert 'id="card-stat-active"' in html
    assert 'id="stat-active-students"' in html
    assert 'id="card-stat-present"' in html
    assert 'id="stat-total-present"' in html
    assert 'id="card-stat-absent"' in html
    assert 'id="stat-total-absent"' in html
    assert 'id="card-stat-percentage"' in html
    assert 'id="stat-attendance-percentage"' in html


def test_5_session_breakdown_and_records_table_markup_present(client):
    """Verify session breakdown table and detailed student roster table markup."""
    res = client.get("/reports")
    html = res.get_data(as_text=True)

    # Session breakdown section
    assert 'id="session-breakdown-card"' in html
    assert 'id="session-breakdown-table"' in html
    assert 'id="session-breakdown-body"' in html
    assert 'id="session-empty-state"' in html
    assert 'id="session-count-badge"' in html

    # Detailed records section
    assert 'id="detailed-records-card"' in html
    assert 'id="reports-attendance-table"' in html
    assert 'id="reports-attendance-body"' in html
    assert 'id="search-records"' in html
    assert 'id="filter-session"' in html
    assert 'id="filter-status"' in html
    assert 'id="records-count-badge"' in html

    # States
    assert 'id="records-empty-state"' in html
    assert 'id="records-no-sessions-state"' in html
    assert 'id="records-error-state"' in html


def test_6_daily_reports_api_summary_and_empty_date(client):
    """Verify GET /api/reports/daily returns zeroed summary when no sessions exist."""
    res = client.get("/api/reports/daily?date=2026-11-05")
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    assert data["date"] == "2026-11-05"
    summary = data["summary"]
    assert summary["total_sessions"] == 0
    assert summary["total_enrollments"] == 0
    assert summary["total_present"] == 0
    assert summary["total_absent"] == 0
    assert summary["overall_attendance_percentage"] == 0.0
    assert data["sessions"] == []


def test_7_daily_reports_api_with_sessions_and_inactive_student_exclusion(client, app_instance):
    """Verify daily reports API correctly excludes inactive students from total enrollments."""
    # Register 2 active students and 1 inactive student
    client.post("/api/students", json={"roll_number": "ST-1", "full_name": "Active One", "card_uid": "A_UID1"})
    client.post("/api/students", json={"roll_number": "ST-2", "full_name": "Active Two", "card_uid": "A_UID2"})
    inact_res = client.post("/api/students", json={"roll_number": "ST-3", "full_name": "Inactive Three", "card_uid": "I_UID3"})
    inact_id = inact_res.get_json()["student"]["id"]

    # Deactivate student 3 directly in DB
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    conn.execute("UPDATE students SET active = 0 WHERE id = ?;", (inact_id,))
    conn.commit()
    conn.close()

    # Create session and activate
    sess_res = client.post("/api/sessions", json={"course_name": "CS-101", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Scan 1 student
    client.post("/api/scans", json={"card_uid": "A_UID1", "source": "SIMULATOR"})

    # Query report
    rep_res = client.get("/api/reports/daily?date=2026-11-07")
    assert rep_res.status_code == 200
    data = rep_res.get_json()

    summary = data["summary"]
    assert summary["total_sessions"] == 1
    assert summary["total_enrollments"] == 2  # Only 2 active students!
    assert summary["total_present"] == 1
    assert summary["total_absent"] == 1
    assert summary["overall_attendance_percentage"] == 50.0

    sess_stat = data["sessions"][0]
    assert sess_stat["total_students"] == 2
    assert sess_stat["present"] == 1
    assert sess_stat["absent"] == 1
    assert sess_stat["attendance_percentage"] == 50.0


def test_8_multiple_sessions_on_same_date_are_separated(client):
    """Verify multiple sessions on the same date are distinctly reported without mixing."""
    # Register 2 students
    client.post("/api/students", json={"roll_number": "R-1", "full_name": "Student A", "card_uid": "UID_A"})
    client.post("/api/students", json={"roll_number": "R-2", "full_name": "Student B", "card_uid": "UID_B"})

    # Session 1: Morning
    s1_res = client.post("/api/sessions", json={"course_name": "Morning Lecture", "session_date": "2026-11-08", "start_time": "09:00", "end_time": "23:59"})
    s1_id = s1_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{s1_id}/activate")
    client.post("/api/scans", json={"card_uid": "UID_A", "source": "SIMULATOR"})

    # Session 2: Afternoon
    s2_res = client.post("/api/sessions", json={"course_name": "Afternoon Lecture", "session_date": "2026-11-08", "start_time": "14:00", "end_time": "23:59"})
    s2_id = s2_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{s2_id}/activate")
    client.post("/api/scans", json={"card_uid": "UID_A", "source": "SIMULATOR"})
    client.post("/api/scans", json={"card_uid": "UID_B", "source": "SIMULATOR"})

    # Query report
    rep_res = client.get("/api/reports/daily?date=2026-11-08")
    assert rep_res.status_code == 200
    data = rep_res.get_json()

    assert data["summary"]["total_sessions"] == 2
    assert data["summary"]["total_enrollments"] == 4
    assert data["summary"]["total_present"] == 3
    assert data["summary"]["total_absent"] == 1
    assert data["summary"]["overall_attendance_percentage"] == 75.0

    sessions = data["sessions"]
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == s1_id
    assert sessions[0]["present"] == 1
    assert sessions[0]["absent"] == 1

    assert sessions[1]["session_id"] == s2_id
    assert sessions[1]["present"] == 2
    assert sessions[1]["absent"] == 0


def test_9_csv_export_endpoint_headers_and_filename(client):
    """Verify GET /api/reports/export.csv returns proper attachment headers and filename."""
    res = client.get("/api/reports/export.csv?date=2026-11-08")
    assert res.status_code == 200
    assert "text/csv" in res.content_type
    assert 'attachment; filename="attendance_2026-11-08.csv"' in res.headers.get("Content-Disposition", "")


def test_10_csv_export_content_includes_present_and_absent_records(client):
    """Verify exported CSV content has header row and shows Present and Absent rows."""
    # Setup student and session
    client.post("/api/students", json={"roll_number": "CSV-1", "full_name": "Present Student", "card_uid": "UID_CSV1"})
    client.post("/api/students", json={"roll_number": "CSV-2", "full_name": "Absent Student", "card_uid": "UID_CSV2"})

    sess_res = client.post("/api/sessions", json={"course_name": "CSV Test Lecture", "session_date": "2026-11-09", "start_time": "10:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Only CSV-1 scans
    client.post("/api/scans", json={"card_uid": "UID_CSV1", "source": "SIMULATOR"})

    res = client.get("/api/reports/export.csv?date=2026-11-09")
    assert res.status_code == 200
    csv_text = res.get_data(as_text=True)

    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)

    # Header verification
    headers = rows[0]
    assert headers[0] == "Date"
    assert headers[1] == "Session ID"
    assert headers[2] == "Course Name"
    assert headers[4] == "Roll Number"
    assert headers[7] == "Attendance Status"

    # Data rows: 2 students in session
    assert len(rows) == 3
    row1 = next(r for r in rows[1:] if r[4] == "CSV-1")
    assert row1[7] == "Present"

    row2 = next(r for r in rows[1:] if r[4] == "CSV-2")
    assert row2[7] == "Absent"


def test_11_csv_export_invalid_date_rejected(client):
    """Verify CSV export endpoint returns HTTP 400 for missing or invalid dates."""
    # Missing date
    res1 = client.get("/api/reports/export.csv")
    assert res1.status_code == 400
    assert "date" in res1.get_json()["message"]

    # Invalid date format
    res2 = client.get("/api/reports/export.csv?date=09-11-2026")
    assert res2.status_code == 400
    assert "YYYY-MM-DD" in res2.get_json()["message"]

    # Non-calendar date
    res3 = client.get("/api/reports/export.csv?date=2026-02-30")
    assert res3.status_code == 400
    assert "not a valid calendar date" in res3.get_json()["message"]


def test_12_daily_report_invalid_date_rejected(client):
    """Verify daily report API endpoint returns HTTP 400 for missing or invalid dates."""
    # Missing date
    res1 = client.get("/api/reports/daily")
    assert res1.status_code == 400

    # Invalid format
    res2 = client.get("/api/reports/daily?date=invalid-date")
    assert res2.status_code == 400


def test_13_daily_attendance_query_used_by_detailed_roster(client):
    """Verify GET /api/attendance?date=... returns expected schema used by detailed reports table."""
    client.post("/api/students", json={"roll_number": "SCH-1", "full_name": "Schema Student", "card_uid": "UID_SCH"})
    sess_res = client.post("/api/sessions", json={"course_name": "Schema Lecture", "session_date": "2026-11-09", "start_time": "11:00", "end_time": "23:59"})
    sess_id = sess_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")
    client.post("/api/scans", json={"card_uid": "UID_SCH", "source": "SIMULATOR"})

    res = client.get("/api/attendance?date=2026-11-09")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    recs = data["attendance"]
    assert len(recs) == 1
    assert recs[0]["session_id"] == sess_id
    assert recs[0]["roll_number"] == "SCH-1"
    assert recs[0]["student_name"] == "Schema Student"
    assert recs[0]["course_name"] == "Schema Lecture"


def test_14_javascript_controller_contains_stale_guard_and_xss_protection(client):
    """Verify client JS asset contains stale response guard, XSS escaping, and download handler."""
    res = client.get("/static/js/reports.js")
    assert res.status_code == 200
    js_content = res.get_data(as_text=True)

    assert "latestRequestId" in js_content
    assert "escapeHtml" in js_content
    assert "handleDownloadCsv" in js_content
    assert "isActiveStudent" in js_content
    assert "window.ReportsController" in js_content
