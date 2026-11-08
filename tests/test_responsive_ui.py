"""
Automated unit and integration tests for Responsive Design & UI Improvements (Milestone 8).
Verifies:
1. All dashboard pages render successfully with authenticated sessions.
2. Base layout contains responsive meta, accessible mobile drawer toggle, and backdrop overlay.
3. Master stylesheet (style.css) contains all required responsive breakpoints (320px to 1920px).
4. Accessibility features: prefers-reduced-motion, :focus-visible, and high-contrast color tokens.
5. Core JavaScript (app.js) implements accessible mobile drawer controller with Escape key and focus return.
6. Reusable components (components.css) standardize buttons, badges, forms, and responsive tables.
7. Page stylesheets (dashboard, students, sessions, attendance, reports, auth) include mobile rules.
8. Topbar elements retain all verified element IDs and DOM hooks.
9. Administrator login interface renders responsive cards and accessible controls.
10. Authentication and CSRF protection on logout remain intact.
"""

import pytest
from app import create_app
from app.database import init_db


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_responsive_ui.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def auth_client(app_instance):
    """Provides Flask test client with an authenticated administrator session."""
    client = app_instance.test_client()
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_user_id"] = 1
        sess["admin_username"] = "admin"
    return client


def test_1_all_dashboard_views_render_successfully_with_auth(auth_client):
    """Verify all 6 core dashboard views render HTTP 200 with valid HTML structure."""
    endpoints = ["/", "/dashboard", "/students", "/sessions", "/attendance", "/reports", "/settings"]
    for url in endpoints:
        res = auth_client.get(url)
        assert res.status_code == 200
        assert "text/html" in res.content_type
        html = res.get_data(as_text=True)
        assert "<!DOCTYPE html>" in html
        assert 'id="sidebar"' in html
        assert 'id="mobile-menu-btn"' in html
        assert 'id="sidebar-backdrop"' in html


def test_2_base_template_contains_responsive_viewport_and_mobile_drawer_elements(auth_client):
    """Verify base template provides mobile viewport, drawer backdrop, and accessible ARIA attributes."""
    res = auth_client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Responsive meta viewport tag
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in html

    # Mobile menu toggle with accessibility attributes
    assert 'id="mobile-menu-btn"' in html
    assert 'aria-label="Toggle navigation menu"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="sidebar"' in html

    # Backdrop overlay element
    assert 'id="sidebar-backdrop"' in html
    assert 'class="sidebar-backdrop"' in html

    # CSRF-protected POST logout form
    assert 'id="topbar-logout-form"' in html
    assert 'method="POST"' in html
    assert 'id="btn-topbar-logout"' in html


def test_3_master_stylesheet_contains_target_responsive_breakpoints(auth_client):
    """Verify style.css implements comprehensive responsive breakpoints spanning 320px to 1920px."""
    res = auth_client.get("/static/css/style.css")
    assert res.status_code == 200
    css = res.get_data(as_text=True)

    # Required device targets: 320px, 375px, 430px, 768px, 992px, 1024px, 1440px, 1920px
    assert "@media (min-width: 1920px)" in css
    assert "@media (min-width: 1440px)" in css
    assert "@media (max-width: 1024px)" in css
    assert "@media (max-width: 992px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 576px)" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 375px)" in css
    assert "@media (max-width: 320px)" in css


def test_4_master_stylesheet_contains_accessibility_and_contrast_features(auth_client):
    """Verify accessibility features in style.css: reduced motion, focus indicators, and backdrop styling."""
    res = auth_client.get("/static/css/style.css")
    assert res.status_code == 200
    css = res.get_data(as_text=True)

    # Reduced motion media query
    assert "@media (prefers-reduced-motion: reduce)" in css

    # Visible focus ring
    assert ":focus-visible" in css

    # Mobile sidebar drawer backdrop styles
    assert ".sidebar-backdrop" in css
    assert ".sidebar-backdrop.active" in css
    assert "body.sidebar-open" in css

    # High-contrast WCAG AA text muted variable
    assert "--color-text-muted: #475569;" in css


def test_5_core_app_js_contains_accessible_drawer_controller(auth_client):
    """Verify app.js includes accessible mobile menu toggle, Escape key listener, and focus management."""
    res = auth_client.get("/static/js/app.js")
    assert res.status_code == 200
    js = res.get_data(as_text=True)

    # Mobile menu backdrop and drawer logic
    assert "sidebar-backdrop" in js
    assert "aria-expanded" in js
    assert "openMobileSidebar" in js
    assert "closeMobileSidebar" in js

    # Escape key closes drawer and restores focus
    assert 'e.key === "Escape"' in js
    assert "restoreFocus" in js

    # Auto-close on link navigation
    assert "navLinks.forEach" in js


def test_6_components_stylesheet_contains_standardized_form_and_table_rules(auth_client):
    """Verify components.css provides standardized buttons, badges, form inputs, and responsive tables."""
    res = auth_client.get("/static/css/components.css")
    assert res.status_code == 200
    css = res.get_data(as_text=True)

    # Standard button variations
    assert ".btn-primary" in css
    assert ".btn-secondary" in css
    assert ".btn-danger" in css
    assert ".btn-success" in css

    # Standard form controls
    assert ".form-group" in css
    assert ".form-label" in css
    assert ".form-input" in css
    assert ".form-select" in css

    # Responsive table container with smooth scrolling
    assert ".table-responsive" in css
    assert "-webkit-overflow-scrolling: touch" in css


def test_7_page_stylesheets_contain_responsive_grid_and_mobile_rules(auth_client):
    """Verify all section stylesheets contain responsive breakpoints down to small mobile (320px)."""
    stylesheets = [
        "/static/css/dashboard.css",
        "/static/css/students.css",
        "/static/css/sessions.css",
        "/static/css/attendance.css",
        "/static/css/reports.css",
        "/static/css/auth.css",
    ]
    for url in stylesheets:
        res = auth_client.get(url)
        assert res.status_code == 200
        css = res.get_data(as_text=True)
        assert "@media" in css
        assert "320px" in css or "480px" in css


def test_8_topbar_elements_retain_required_hooks_and_ids(auth_client):
    """Verify topbar keeps all IDs referenced by client JavaScript and polling routines."""
    res = auth_client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    required_ids = [
        "topbar-session-pill",
        "topbar-session-text",
        "topbar-health-badge",
        "topbar-health-dot",
        "topbar-health-text",
        "live-clock",
        "topbar-admin-name",
        "topbar-logout-form",
        "btn-topbar-logout",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in html


def test_9_login_page_renders_with_responsive_card_and_accessible_toggle(auth_client):
    """Verify login template and auth.css render responsive cards and accessible password toggle."""
    res = auth_client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert 'id="auth-card"' in html
    assert 'id="login-form"' in html
    assert 'id="btn-toggle-password"' in html
    assert 'aria-label="Toggle password visibility"' in html

    css_res = auth_client.get("/static/css/auth.css")
    assert css_res.status_code == 200
    css = css_res.get_data(as_text=True)
    assert "@media (max-width: 480px)" in css
    assert "@media (max-width: 320px)" in css


def test_10_csrf_protection_and_post_only_logout_remain_fully_functional(app_instance, auth_client):
    """Verify GET /logout is rejected with 405 and POST /logout without CSRF returns 403 when CSRF enabled."""
    # GET /logout rejected
    res_get = auth_client.get("/logout")
    assert res_get.status_code == 405

    # Enable CSRF verification
    app_instance.config["CSRF_ENABLED"] = True
    app_instance.config["WTF_CSRF_ENABLED"] = True

    # POST /logout without CSRF rejected with 403
    res_post_nocsrf = auth_client.post("/logout", headers={"Accept": "application/json"})
    assert res_post_nocsrf.status_code == 403
