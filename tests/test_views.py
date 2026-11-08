"""
Automated unit and integration tests for Web Frontend Views (app/routes/views.py) and Static Assets.
Verifies that Flask properly serves HTML templates and static CSS/JS files.
"""

import pytest
from app import create_app
from app.database import init_db


@pytest.fixture
def app(tmp_path):
    """Creates a test Flask app configured with an isolated database."""
    test_db_path = tmp_path / "test_views_app.db"
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask test client."""
    return app.test_client()


def test_1_root_url_serves_dashboard_html(client):
    """Verify GET / returns HTTP 200 with text/html content-type and valid title."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.content_type
    html = response.get_data(as_text=True)
    assert "Smart Attendance Management System" in html
    assert "Dashboard Overview" in html


def test_2_dashboard_url_serves_dashboard_html(client):
    """Verify GET /dashboard returns HTTP 200 with dashboard overview."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.content_type
    html = response.get_data(as_text=True)
    assert "Classroom Attendance Overview" in html


def test_3_dashboard_contains_sidebar_and_topbar(client):
    """Verify layout elements: sidebar navigation links, status widget, live clock, topbar."""
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert 'id="sidebar"' in html
    assert "Dashboard Overview" in html
    assert "Student Management" in html
    assert "Class Sessions" in html
    assert "Live Attendance" in html
    assert "Attendance Reports" in html
    assert 'id="live-clock"' in html
    assert 'id="topbar-health-badge"' in html
    assert 'id="topbar-session-pill"' in html
    assert 'id="toast-container"' in html


def test_4_dashboard_contains_four_kpi_cards(client):
    """Verify all 4 KPI metric cards exist in the dashboard markup."""
    response = client.get("/dashboard")
    html = response.get_data(as_text=True)
    assert 'id="kpi-students-card"' in html
    assert 'id="kpi-session-card"' in html
    assert 'id="kpi-attendance-card"' in html
    assert 'id="kpi-status-card"' in html


def test_5_placeholder_views_render_successfully(client):
    """Verify future module section views return HTTP 200 with clear milestone markers."""
    endpoints = [
        ("/students", "Student Management", "Milestone 2"),
        ("/sessions", "Class Sessions", "Milestone 3"),
        ("/attendance", "Live Attendance", "Milestone 4"),
        ("/reports", "Attendance Reports", "Milestone 5"),
        ("/settings", "Settings & Admin", "Milestone 6"),
    ]
    for url, title, milestone in endpoints:
        res = client.get(url)
        assert res.status_code == 200
        assert "text/html" in res.content_type
        content = res.get_data(as_text=True)
        assert title in content
        assert milestone in content


def test_6_static_css_assets_are_served(client):
    """Verify that all core CSS stylesheets are served with HTTP 200 and text/css."""
    css_files = [
        "/static/css/style.css",
        "/static/css/components.css",
        "/static/css/dashboard.css",
    ]
    for css_url in css_files:
        res = client.get(css_url)
        assert res.status_code == 200
        assert "text/css" in res.content_type
        assert len(res.data) > 0


def test_7_static_js_assets_are_served(client):
    """Verify that core JavaScript files are served with HTTP 200 and javascript content type."""
    js_files = [
        "/static/js/app.js",
        "/static/js/dashboard.js",
    ]
    for js_url in js_files:
        res = client.get(js_url)
        assert res.status_code == 200
        assert "javascript" in res.content_type
        assert len(res.data) > 0
