"""
Automated Unit, Integration, and Security Tests for Administrator Authentication & Authorization (Milestone 7).

Verifies:
1. Login page loads successfully with HTTP 200.
2. Valid credentials authenticate successfully and set administrator session.
3. Invalid credentials return HTTP 401 with generic error message.
4. Unknown usernames return HTTP 401 with identical generic error message (no enumeration).
5. Empty username or password return HTTP 400.
6. Inactive administrator account (active = 0) is rejected with HTTP 401.
7. Passwords are never stored in plaintext and are securely hashed.
8. Logout terminates the session and redirects to /login.
9. Unauthenticated users attempting to access protected HTML pages are redirected to /login.
10. Authenticated administrator can access all protected HTML views.
11. Unauthenticated API requests return HTTP 401 JSON.
12. Health check endpoint (/api/health) remains publicly accessible without authentication.
13. Protected student, session, and report endpoints reject unauthenticated requests.
14. Authenticated administrator can record attendance scans via /api/scans.
15. CSRF protection rejects state-changing requests when missing or invalid token.
16. Valid CSRF token permits state changes when CSRF is enabled.
17. Repeated failed login attempts trigger HTTP 429 rate limiting.
18. Open redirects are rejected (safe internal redirect enforced).
19. Cache-Control headers prevent caching of authenticated responses.
20. No default administrator credentials exist automatically on fresh database initialization.
21. CLI command 'create-admin' creates a valid administrator account.
"""

import os
import threading
import time
from datetime import datetime, timezone
import pytest
from app import create_app
from app.config import get_config, ProductionConfig, DevelopmentConfig, TestingConfig
from app.auth import (
    hash_password,
    verify_password,
    validate_password_strength,
    is_safe_url,
    get_client_identifier,
    check_login_rate_limit,
    record_failed_login,
    reset_failed_logins,
    clear_all_rate_limits,
    _failed_attempts,
    RATE_LIMIT_WINDOW_SECONDS,
)
from app.database import init_db, get_db_connection


@pytest.fixture
def auth_app(tmp_path):
    """Creates an isolated test application with authentication enforced."""
    test_db_path = tmp_path / "test_auth_app.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db_path
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    # Clear any rate-limit state between tests
    clear_all_rate_limits()
    init_db(test_db_path)
    yield app
    clear_all_rate_limits()


@pytest.fixture
def auth_client(auth_app):
    """Provides test client for authentication tests."""
    return auth_app.test_client()


def create_test_admin(app, username="adminuser", password="AdminPassword123!", active=1):
    """Helper to insert an administrator account directly into SQLite."""
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    pwd_hash = hash_password(password)
    now_utc = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO admin_users (username, password_hash, role, active, created_at)
        VALUES (?, ?, 'admin', ?, ?);
        """,
        (username, pwd_hash, active, now_utc)
    )
    conn.commit()
    conn.close()


# ==============================================================================
# 1. AUTHENTICATION TESTS
# ==============================================================================

def test_1_login_page_loads_successfully(auth_client):
    """Verify GET /login returns HTTP 200 with HTML login form elements."""
    res = auth_client.get("/login")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    assert "Smart Attendance System" in html
    assert 'id="username"' in html
    assert 'id="password"' in html
    assert 'id="btn-login"' in html
    assert 'id="btn-toggle-password"' in html
    assert 'id="csrf-token-input"' in html


def test_2_valid_credentials_authenticate_successfully(auth_app, auth_client):
    """Verify valid credentials return HTTP 200 and set session cookies."""
    create_test_admin(auth_app, username="superadmin", password="SecretPassword123!")

    res = auth_client.post(
        "/login",
        json={"username": "superadmin", "password": "SecretPassword123!"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["message"] == "Login successful."
    assert data["redirect"] == "/dashboard"

    # Verify session cookie was set
    with auth_client.session_transaction() as sess:
        assert sess.get("admin_username") == "superadmin"
        assert sess.get("admin_role") == "admin"
        assert sess.get("admin_id") is not None


def test_3_invalid_password_is_rejected_with_generic_message(auth_app, auth_client):
    """Verify incorrect password returns HTTP 401 with generic error message."""
    create_test_admin(auth_app, username="superadmin", password="SecretPassword123!")

    res = auth_client.post(
        "/login",
        json={"username": "superadmin", "password": "WrongPassword999!"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False
    assert data["message"] == "Invalid username or password."


def test_4_nonexistent_username_returns_identical_generic_message(auth_app, auth_client):
    """Verify non-existent user returns identical 401 to prevent username enumeration."""
    res = auth_client.post(
        "/login",
        json={"username": "nonexistent_admin", "password": "SomePassword123!"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False
    assert data["message"] == "Invalid username or password."


def test_5_empty_credentials_rejected_with_http_400(auth_client):
    """Verify empty or missing fields return HTTP 400."""
    res1 = auth_client.post("/login", json={"username": "", "password": ""})
    assert res1.status_code == 400

    res2 = auth_client.post("/login", json={"username": "admin", "password": ""})
    assert res2.status_code == 400

    res3 = auth_client.post("/login", json={"username": "", "password": "Password123!"})
    assert res3.status_code == 400


def test_6_inactive_administrator_cannot_log_in(auth_app, auth_client):
    """Verify deactivated administrator account (active = 0) is rejected."""
    create_test_admin(auth_app, username="disabled_admin", password="Password123!", active=0)

    res = auth_client.post(
        "/login",
        json={"username": "disabled_admin", "password": "Password123!"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 401
    data = res.get_json()
    assert data["message"] == "Invalid username or password."


def test_7_password_is_stored_as_secure_hash(auth_app):
    """Verify database contains a cryptographic hash and not the plaintext password."""
    password = "MyComplexPassword123!"
    create_test_admin(auth_app, username="hash_check_admin", password=password)

    conn = get_db_connection(auth_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM admin_users WHERE username = ?;", ("hash_check_admin",))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    stored_hash = row["password_hash"]
    assert stored_hash != password
    assert len(stored_hash) > 30
    assert verify_password(stored_hash, password) is True
    assert verify_password(stored_hash, "WrongPassword") is False


def test_8_logout_clears_authentication(auth_app, auth_client):
    """Verify POST /logout removes session data and redirects to /login."""
    create_test_admin(auth_app, username="logout_admin", password="Password123!")

    # 1. Log in
    auth_client.post("/login", json={"username": "logout_admin", "password": "Password123!"})
    with auth_client.session_transaction() as sess:
        assert sess.get("admin_username") == "logout_admin"

    # 2. Log out via POST
    res = auth_client.post("/logout", follow_redirects=False)
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]

    # 3. Verify session is empty
    with auth_client.session_transaction() as sess:
        assert sess.get("admin_id") is None
        assert sess.get("admin_username") is None


# ==============================================================================
# 2. AUTHORIZATION TESTS
# ==============================================================================

def test_9_unauthenticated_users_redirected_from_protected_views(auth_client):
    """Verify unauthenticated requests to protected HTML views redirect to /login."""
    protected_urls = ["/", "/dashboard", "/students", "/sessions", "/attendance", "/reports", "/settings"]
    for url in protected_urls:
        res = auth_client.get(url, follow_redirects=False)
        assert res.status_code == 302, f"Expected 302 redirect for {url}, got {res.status_code}"
        assert "/login" in res.headers["Location"]


def test_10_authenticated_administrator_can_access_protected_views(auth_app, auth_client):
    """Verify authenticated administrator receives HTTP 200 on all protected views."""
    create_test_admin(auth_app, username="view_admin", password="Password123!")
    auth_client.post("/login", json={"username": "view_admin", "password": "Password123!"})

    protected_urls = ["/", "/dashboard", "/students", "/sessions", "/attendance", "/reports", "/settings"]
    for url in protected_urls:
        res = auth_client.get(url)
        assert res.status_code == 200, f"Expected 200 for {url}, got {res.status_code}"


def test_11_unauthenticated_api_requests_return_http_401_json(auth_client):
    """Verify unauthenticated API calls return HTTP 401 JSON and not HTML redirects."""
    api_endpoints = [
        ("GET", "/api/students"),
        ("POST", "/api/students"),
        ("GET", "/api/sessions"),
        ("POST", "/api/sessions"),
        ("GET", "/api/sessions/active"),
        ("PATCH", "/api/sessions/1/activate"),
        ("PATCH", "/api/sessions/1/deactivate"),
        ("GET", "/api/attendance?date=2026-11-07"),
        ("POST", "/api/scans"),
        ("GET", "/api/reports/daily?date=2026-11-07"),
        ("GET", "/api/reports/export.csv?date=2026-11-07"),
    ]

    for method, endpoint in api_endpoints:
        if method == "GET":
            res = auth_client.get(endpoint)
        elif method == "POST":
            res = auth_client.post(endpoint, json={})
        elif method == "PATCH":
            res = auth_client.patch(endpoint)

        assert res.status_code == 401, f"Expected 401 for {method} {endpoint}, got {res.status_code}"
        assert "application/json" in res.content_type
        data = res.get_json()
        assert data["success"] is False
        assert data["error"] == "Unauthorized"


def test_12_health_endpoint_remains_public_without_auth(auth_client):
    """Verify /api/health is accessible without administrator login."""
    res = auth_client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["database"] == "connected"


def test_13_authenticated_admin_can_use_rfid_scan_endpoint(auth_app, auth_client):
    """Verify authenticated administrator session can successfully scan cards via /api/scans."""
    create_test_admin(auth_app, username="scan_admin", password="Password123!")
    auth_client.post("/login", json={"username": "scan_admin", "password": "Password123!"})

    conn = get_db_connection(auth_app.config["DATABASE_PATH"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES ('SCAN01', 'Test Student', 'A1B2C3D4', 1, '2026-11-07T00:00:00Z');"
    )
    cur.execute(
        "INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at) VALUES ('CS101', '2026-11-07', '10:00', 1, '2026-11-07T00:00:00Z');"
    )
    conn.commit()
    conn.close()

    res = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": "SIMULATOR"}
    )
    assert res.status_code == 201
    data = res.get_json()
    assert data["success"] is True
    assert data["attendance"]["roll_number"] == "SCAN01"


# ==============================================================================
# 3. SECURITY & POLICY TESTS
# ==============================================================================

def test_14_csrf_protection_rejects_missing_token_when_enabled(auth_app, auth_client):
    """Verify CSRF protection blocks state-changing requests when CSRF is enabled and token missing."""
    auth_app.config["CSRF_ENABLED"] = True
    auth_app.config["WTF_CSRF_ENABLED"] = True

    create_test_admin(auth_app, username="csrf_admin", password="Password123!")

    # POST /login without CSRF token must fail with HTTP 403
    res = auth_client.post(
        "/login",
        json={"username": "csrf_admin", "password": "Password123!"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 403
    data = res.get_json()
    assert data["success"] is False
    assert "CSRF" in data["message"]


def test_15_csrf_protection_allows_request_with_valid_token(auth_app, auth_client):
    """Verify valid CSRF token in header allows legitimate state modifications."""
    auth_app.config["CSRF_ENABLED"] = True
    auth_app.config["WTF_CSRF_ENABLED"] = True

    create_test_admin(auth_app, username="csrf_admin2", password="Password123!")

    # 1. GET /login to initialize session CSRF token
    auth_client.get("/login")
    csrf_token = ""
    with auth_client.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")
    assert csrf_token != ""

    # 2. POST /login with X-CSRF-Token header
    res = auth_client.post(
        "/login",
        json={"username": "csrf_admin2", "password": "Password123!"},
        headers={
            "Accept": "application/json",
            "X-CSRF-Token": csrf_token
        }
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_16_repeated_failed_login_attempts_trigger_rate_limiting(auth_app, auth_client):
    """Verify 5 failed login attempts trigger HTTP 429 rate limiting."""
    create_test_admin(auth_app, username="rate_admin", password="CorrectPassword123!")

    # Execute 5 invalid attempts
    for _ in range(5):
        res = auth_client.post(
            "/login",
            json={"username": "rate_admin", "password": "WrongPassword!"},
            headers={"Accept": "application/json"}
        )
        assert res.status_code == 401

    # 6th attempt should be blocked by rate limiting with HTTP 429
    blocked_res = auth_client.post(
        "/login",
        json={"username": "rate_admin", "password": "WrongPassword!"},
        headers={"Accept": "application/json"}
    )
    assert blocked_res.status_code == 429
    data = blocked_res.get_json()
    assert data["success"] is False
    assert "Too many failed login attempts" in data["message"]


def test_17_open_redirect_is_prevented(auth_app, auth_client):
    """Verify malicious external redirect URLs are sanitized and defaulted to /dashboard."""
    create_test_admin(auth_app, username="redirect_admin", password="Password123!")

    malicious_targets = [
        "https://evil.com",
        "//evil.com/hack",
        "javascript:alert(1)",
        "/\\evil.com",
    ]

    for target in malicious_targets:
        res = auth_client.post(
            "/login",
            json={
                "username": "redirect_admin",
                "password": "Password123!",
                "next": target
            },
            headers={"Accept": "application/json"}
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["redirect"] == "/dashboard", f"Open redirect permitted for {target}"


def test_18_cache_control_headers_prevent_caching(auth_app, auth_client):
    """Verify Cache-Control headers are added to prevent browser back-button caching."""
    create_test_admin(auth_app, username="cache_admin", password="Password123!")
    auth_client.post("/login", json={"username": "cache_admin", "password": "Password123!"})

    res = auth_client.get("/dashboard")
    assert res.status_code == 200
    assert "no-store" in res.headers.get("Cache-Control", "")
    assert "no-cache" in res.headers.get("Cache-Control", "")


def test_19_no_default_administrator_created_automatically(auth_app):
    """Verify fresh database does not contain pre-seeded or hardcoded administrators."""
    conn = get_db_connection(auth_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) as count FROM admin_users;")
    count = cursor.fetchone()["count"]
    conn.close()
    assert count == 0


def test_20_cli_create_admin_command(auth_app):
    """Verify 'flask create-admin' creates a valid administrator record via interactive prompt."""
    runner = auth_app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "cli_admin"],
        input="SecureCliPass123!\nSecureCliPass123!\n"
    )
    assert result.exit_code == 0
    assert "created successfully" in result.output

    conn = get_db_connection(auth_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT username, password_hash, active FROM admin_users WHERE username = ?;", ("cli_admin",))
    user = cursor.fetchone()
    conn.close()

    assert user is not None
    assert user["username"] == "cli_admin"
    assert verify_password(user["password_hash"], "SecureCliPass123!") is True


# ==============================================================================
# 4. TARGETED SECURITY HARDENING TESTS (SEC-01 TO SEC-05)
# ==============================================================================

def test_21_spoofed_x_forwarded_for_header_is_ignored(auth_app):
    """SEC-01: Verify client IP extraction uses remote_addr and ignores spoofed X-Forwarded-For."""
    with auth_app.test_request_context(
        "/login",
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
        headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
    ):
        ident = get_client_identifier()
        assert ident == "192.168.1.50"


def test_22_same_remote_addr_shares_rate_limit_identity(auth_app, auth_client):
    """SEC-01: Verify multiple failed attempts with spoofed X-Forwarded-For headers cannot evade rate limits."""
    create_test_admin(auth_app, username="spoof_admin", password="Password123!")

    # Issue 5 failed attempts with different spoofed X-Forwarded-For headers from the same client IP
    for i in range(5):
        res = auth_client.post(
            "/login",
            json={"username": "spoof_admin", "password": "WrongPassword!"},
            headers={
                "Accept": "application/json",
                "X-Forwarded-For": f"10.0.0.{i + 1}"
            }
        )
        assert res.status_code == 401

    # 6th attempt with yet another spoofed IP must be blocked by rate limiting (HTTP 429)
    blocked_res = auth_client.post(
        "/login",
        json={"username": "spoof_admin", "password": "WrongPassword!"},
        headers={
            "Accept": "application/json",
            "X-Forwarded-For": "10.0.0.99"
        }
    )
    assert blocked_res.status_code == 429
    data = blocked_res.get_json()
    assert "Too many failed login attempts" in data["message"]


def test_23_production_config_loads_with_valid_secret(monkeypatch):
    """SEC-02: Verify ProductionConfig loads securely when a valid environment SECRET_KEY exists."""
    monkeypatch.setenv("SECRET_KEY", "cryptographically-strong-production-key-999!")
    monkeypatch.setenv("FLASK_ENV", "production")

    config = get_config("production")
    assert config.DEBUG is False
    assert config.TESTING is False
    assert config.SESSION_COOKIE_SECURE is True
    assert config.SESSION_COOKIE_HTTPONLY is True
    assert config.SESSION_COOKIE_SAMESITE == "Lax"
    assert config.SECRET_KEY == "cryptographically-strong-production-key-999!"


def test_24_production_startup_fails_when_secret_key_missing(monkeypatch):
    """SEC-02: Verify production startup raises RuntimeError when SECRET_KEY is missing."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")

    with pytest.raises(RuntimeError) as exc_info:
        create_app("production")
    assert "SECRET_KEY environment variable is missing" in str(exc_info.value)


def test_25_production_startup_fails_when_secret_key_empty(monkeypatch):
    """SEC-02: Verify production startup raises RuntimeError when SECRET_KEY is empty or whitespace."""
    monkeypatch.setenv("SECRET_KEY", "   ")
    with pytest.raises(RuntimeError) as exc_info:
        create_app("production")
    assert "SECRET_KEY environment variable is missing or empty" in str(exc_info.value)


def test_26_production_never_uses_dev_fallback_secret(monkeypatch):
    """SEC-02: Verify production rejects the development fallback SECRET_KEY string."""
    monkeypatch.setenv("SECRET_KEY", "dev-secret-key-smart-attendance-2026")
    with pytest.raises(RuntimeError) as exc_info:
        create_app("production")
    assert "development fallback SECRET_KEY is strictly forbidden" in str(exc_info.value)


def test_27_get_logout_returns_http_405_method_not_allowed(auth_client):
    """SEC-03: Verify GET /logout is disallowed and returns HTTP 405 Method Not Allowed."""
    res = auth_client.get("/logout")
    assert res.status_code == 405
    data = res.get_json()
    assert data["error"] == "Method Not Allowed"


def test_28_post_logout_without_csrf_returns_http_403_when_csrf_enabled(auth_app, auth_client):
    """SEC-03: Verify POST /logout without CSRF token returns HTTP 403 Forbidden when CSRF enabled."""
    auth_app.config["CSRF_ENABLED"] = True
    auth_app.config["WTF_CSRF_ENABLED"] = True

    create_test_admin(auth_app, username="csrf_logout_user", password="Password123!")
    auth_client.get("/login")
    with auth_client.session_transaction() as sess:
        token = sess.get("csrf_token")

    # Log in with valid CSRF
    auth_client.post(
        "/login",
        json={"username": "csrf_logout_user", "password": "Password123!"},
        headers={"Accept": "application/json", "X-CSRF-Token": token}
    )

    # Attempt POST /logout with missing CSRF token
    res = auth_client.post("/logout", headers={"Accept": "application/json"})
    assert res.status_code == 403
    data = res.get_json()
    assert "CSRF" in data["message"]


def test_29_post_logout_with_valid_csrf_succeeds_and_clears_session(auth_app, auth_client):
    """SEC-03: Verify POST /logout with a valid CSRF token clears the session and redirects to /login."""
    auth_app.config["CSRF_ENABLED"] = True
    auth_app.config["WTF_CSRF_ENABLED"] = True

    create_test_admin(auth_app, username="valid_logout_user", password="Password123!")
    auth_client.get("/login")
    with auth_client.session_transaction() as sess:
        token = sess.get("csrf_token")

    # Log in
    auth_client.post(
        "/login",
        json={"username": "valid_logout_user", "password": "Password123!"},
        headers={"Accept": "application/json", "X-CSRF-Token": token}
    )
    with auth_client.session_transaction() as sess:
        assert sess.get("admin_username") == "valid_logout_user"
        post_login_token = sess.get("csrf_token")

    # Log out with valid token
    res = auth_client.post(
        "/logout",
        headers={"X-CSRF-Token": post_login_token},
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]

    with auth_client.session_transaction() as sess:
        assert sess.get("admin_id") is None
        assert sess.get("admin_username") is None


def test_30_frontend_base_template_contains_post_logout_form(auth_app, auth_client):
    """SEC-03: Verify base.html renders an HTML POST form for logout with a hidden CSRF token."""
    create_test_admin(auth_app, username="template_user", password="Password123!")
    auth_client.post("/login", json={"username": "template_user", "password": "Password123!"})

    res = auth_client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert 'id="topbar-logout-form"' in html
    assert 'method="POST"' in html or 'method="post"' in html
    assert 'action="/logout"' in html
    assert 'name="csrf_token"' in html
    assert 'id="btn-topbar-logout"' in html


def test_31_cli_create_admin_prompts_for_password_interactively(auth_app):
    """SEC-04: Verify 'flask create-admin' prompts for password and confirmation interactively."""
    runner = auth_app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "interact_admin"],
        input="ValidPass123!\nValidPass123!\n"
    )
    assert result.exit_code == 0
    assert "created successfully" in result.output
    assert "Password:" in result.output
    assert "Repeat for confirmation:" in result.output


def test_32_cli_create_admin_rejects_password_argument(auth_app):
    """SEC-04: Verify 'flask create-admin --password ...' is rejected to prevent credential exposure."""
    runner = auth_app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "bad_arg_admin", "--password", "Secret123!"]
    )
    assert result.exit_code != 0
    assert "No such option: --password" in result.output or "Error" in result.output


def test_33_cli_create_admin_mismatching_confirmation_rejected(auth_app):
    """SEC-04: Verify mismatching password confirmation aborts administrator creation."""
    runner = auth_app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "mismatch_admin"],
        input="SecretPass123!\nMismatchPass123!\n"
    )
    assert result.exit_code != 0
    assert "Error" in result.output or "do not match" in result.output.lower() or "they didn't match" in result.output.lower()

    conn = get_db_connection(auth_app.config["DATABASE_PATH"])
    cur = conn.cursor()
    cur.execute("SELECT id FROM admin_users WHERE username = ?;", ("mismatch_admin",))
    assert cur.fetchone() is None
    conn.close()


def test_34_rate_limiter_thread_safety_under_concurrent_attempts():
    """SEC-05: Verify rate-limiter dictionary operations are thread-safe under concurrent access."""
    clear_all_rate_limits()
    target_ip = "192.168.10.99"
    errors = []

    def worker(worker_id):
        try:
            for _ in range(10):
                record_failed_login(target_ip)
                check_login_rate_limit(target_ip)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread errors encountered: {errors}"
    allowed, msg = check_login_rate_limit(target_ip)
    assert allowed is False
    assert "Too many failed login attempts" in msg
    clear_all_rate_limits()


def test_35_rate_limiter_expired_attempts_are_cleaned_up():
    """SEC-05: Verify attempts older than RATE_LIMIT_WINDOW_SECONDS are cleanly pruned."""
    clear_all_rate_limits()
    test_ip = "192.168.20.50"

    # Record 5 attempts that occurred in the past (expired)
    old_time = time.time() - (RATE_LIMIT_WINDOW_SECONDS + 10)
    for _ in range(5):
        record_failed_login(test_ip)
    
    # Overwrite timestamps with expired times
    with _failed_attempts_lock():
        _failed_attempts[test_ip] = [old_time] * 5

    # Check rate limit - should prune old attempts and allow login
    allowed, msg = check_login_rate_limit(test_ip)
    assert allowed is True
    assert msg == ""
    clear_all_rate_limits()


def _failed_attempts_lock():
    """Helper context manager to access thread lock in tests."""
    from app.auth import _rate_limit_lock
    return _rate_limit_lock

