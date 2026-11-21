"""
Automated Security and Integration Tests for ESP32 Hardware Device Authentication.
Verifies that:
1. DEVICE_API_KEYS configuration parsing strictly rejects malformed, empty, duplicate, or invalid keys.
2. Only successfully authenticated device requests to POST /api/scans bypass CSRF.
3. Incomplete, invalid, or unknown device credentials result in HTTP 401 and NEVER fall back to an admin session.
4. Device authentication is strictly restricted to POST /api/scans and rejected on other endpoints/methods.
5. Dashboard sessions continue to enforce admin authentication and CSRF token protection.
6. All tests operate with LOGIN_DISABLED=False and CSRF_ENABLED=True to verify production-grade security.
"""

import json
import pytest
from app import create_app
from app.config import validate_device_api_keys, load_device_api_keys
from app.database import init_db, get_db_connection, close_db_connection


# Test constants
TEST_DEVICE_ID = "ESP32_ATTENDANCE_01"
TEST_API_KEY = "test-secret-key-esp32-valid-token-32b"
SECONDARY_DEVICE_ID = "ESP32_ATTENDANCE_02"
SECONDARY_API_KEY = "another-valid-secret-key-token-32b"


@pytest.fixture
def auth_secure_app(tmp_path, monkeypatch):
    """
    Creates an application configured with:
    - LOGIN_DISABLED = False (authentication strictly enforced)
    - CSRF_ENABLED = True (CSRF strictly enforced)
    - Isolated temporary SQLite database
    - Configured DEVICE_API_KEYS
    """
    test_db_path = tmp_path / "test_secure_device_auth.db"

    # Set environment variable for DEVICE_API_KEYS
    keys_json = json.dumps({
        TEST_DEVICE_ID: TEST_API_KEY,
        SECONDARY_DEVICE_ID: SECONDARY_API_KEY,
    })
    monkeypatch.setenv("DEVICE_API_KEYS", keys_json)

    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db_path
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["DEVICE_API_KEYS"] = {
        TEST_DEVICE_ID: TEST_API_KEY,
        SECONDARY_DEVICE_ID: SECONDARY_API_KEY,
    }

    init_db(test_db_path)

    # Seed an admin user for dashboard testing
    with app.app_context():
        conn = get_db_connection(test_db_path)
        cur = conn.cursor()
        from app.auth import hash_password
        cur.execute(
            """
            INSERT INTO admin_users (username, password_hash, role, created_at)
            VALUES (?, ?, 'admin', '2026-11-21T00:00:00Z');
            """,
            ("admin_user", hash_password("AdminSecurePassword123!"))
        )
        # Seed student
        cur.execute(
            """
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('101', 'Rahul Patil', 'A1B2C3D4', 1, '2026-11-21T00:00:00Z');
            """
        )
        # Seed active lecture session
        cur.execute(
            """
            INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
            VALUES ('Engineering Mathematics', '2026-11-07', '10:00', '12:00', 1, '2026-11-21T00:00:00Z');
            """
        )
        conn.commit()
        close_db_connection(conn)

    yield app


@pytest.fixture
def auth_client(auth_secure_app):
    """Provides test client for the secure app."""
    return auth_secure_app.test_client()


# ==============================================================================
# 1. CONFIGURATION VALIDATION TESTS
# ==============================================================================

def test_1_config_validation_rejects_malformed_json(monkeypatch):
    """Verify DEVICE_API_KEYS fails closed when JSON is malformed."""
    monkeypatch.setenv("DEVICE_API_KEYS", "{invalid_json: 123")
    with pytest.raises(ValueError, match="Malformed JSON in DEVICE_API_KEYS"):
        load_device_api_keys()


def test_2_config_validation_rejects_duplicate_device_ids(monkeypatch):
    """Verify duplicate device IDs in JSON trigger an immediate ValueError."""
    dup_json = '{"ESP32_01": "valid-secret-key-for-esp32-device-01-token-1", "ESP32_01": "valid-secret-key-for-esp32-device-01-token-2"}'
    monkeypatch.setenv("DEVICE_API_KEYS", dup_json)
    with pytest.raises(ValueError, match="Duplicate device_id 'ESP32_01' detected"):
        load_device_api_keys()


def test_3_config_validation_rejects_empty_or_short_keys():
    """Verify empty or short API keys (< 32 characters) are strictly rejected."""
    # Empty key
    with pytest.raises(ValueError, match="cannot be empty or whitespace-only"):
        validate_device_api_keys({"DEVICE_01": "   "})

    # Short key (< 32 chars): short word
    with pytest.raises(ValueError, match="too short"):
        validate_device_api_keys({"DEVICE_01": "short"})

    # Short key (< 32 chars): boundary check (31 chars)
    with pytest.raises(ValueError, match="too short"):
        validate_device_api_keys({"DEVICE_01": "a" * 31})

    # Exactly 32 chars should succeed
    valid_32 = validate_device_api_keys({"DEVICE_01": "a" * 32})
    assert valid_32["DEVICE_01"] == "a" * 32

    # Empty device_id
    with pytest.raises(ValueError, match="empty or whitespace-only device_id"):
        validate_device_api_keys({"": "valid-secret-key-token-32b-length!!"})

    # Non-dict
    with pytest.raises(ValueError, match="must be a dictionary"):
        validate_device_api_keys(["not", "a", "dict"])


def test_4_config_validation_accepts_valid_mapping(monkeypatch):
    """Verify valid JSON configuration loads successfully."""
    valid_json = '{"ESP32_01": "valid-secret-key-for-esp32-device-01-token", "ESP32_02": "valid-secret-key-for-esp32-device-02-token"}'
    monkeypatch.setenv("DEVICE_API_KEYS", valid_json)
    keys = load_device_api_keys()
    assert keys["ESP32_01"] == "valid-secret-key-for-esp32-device-01-token"
    assert keys["ESP32_02"] == "valid-secret-key-for-esp32-device-02-token"


# ==============================================================================
# 2. HARDWARE DEVICE AUTHENTICATION & CSRF BYPASS TESTS
# ==============================================================================

def test_5_valid_device_credentials_succeed_with_csrf_enabled(auth_client):
    """
    Verify that with CSRF enabled and login required, a request to POST /api/scans
    with valid X-Device-Id and X-API-Key successfully records attendance (HTTP 201)
    and does not require a browser CSRF token.
    """
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": TEST_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "card_uid": "A1B2C3D4",
        "source": TEST_DEVICE_ID,
    }

    response = auth_client.post("/api/scans", json=payload, headers=headers)
    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["scan_result"] == "SUCCESS"
    assert data["student"]["roll_number"] == "101"
    assert data["student"]["student_name"] == "Rahul Patil"


def test_6_unauthenticated_request_rejected_with_401(auth_client):
    """
    Verify that an unauthenticated client sending POST /api/scans without
    device headers or admin login is rejected with HTTP 401 Unauthorized.
    """
    response = auth_client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "UNKNOWN"})
    # CSRF or Auth blocks: unauthenticated without CSRF
    assert response.status_code in (401, 403)
    data = response.get_json()
    assert data["success"] is False


def test_7_incomplete_device_headers_rejected_with_401(auth_client):
    """
    Verify that supplying one device header without the other returns HTTP 401.
    """
    payload = {"card_uid": "A1B2C3D4", "source": TEST_DEVICE_ID}

    # Only X-Device-Id
    res1 = auth_client.post(
        "/api/scans",
        json=payload,
        headers={"X-Device-Id": TEST_DEVICE_ID, "Content-Type": "application/json"}
    )
    assert res1.status_code == 401
    assert "Incomplete device credentials" in res1.get_json()["message"]

    # Only X-API-Key
    res2 = auth_client.post(
        "/api/scans",
        json=payload,
        headers={"X-API-Key": TEST_API_KEY, "Content-Type": "application/json"}
    )
    assert res2.status_code == 401
    assert "Incomplete device credentials" in res2.get_json()["message"]


def test_8_unknown_device_id_rejected_with_401(auth_client):
    """Verify that an unregistered device ID is rejected with HTTP 401."""
    headers = {
        "X-Device-Id": "ESP32_ROGUE_DEVICE",
        "X-API-Key": TEST_API_KEY,
        "Content-Type": "application/json",
    }
    response = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": "ESP32_ROGUE_DEVICE"},
        headers=headers
    )
    assert response.status_code == 401
    assert "Unknown device identifier" in response.get_json()["message"]


def test_9_invalid_device_api_key_rejected_with_401(auth_client):
    """Verify that a valid device ID with an invalid API key is rejected with HTTP 401."""
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": "completely-wrong-api-key-here",
        "Content-Type": "application/json",
    }
    response = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": TEST_DEVICE_ID},
        headers=headers
    )
    assert response.status_code == 401
    assert "Invalid device API key" in response.get_json()["message"]


def test_10_invalid_device_headers_never_fall_back_to_admin_session(auth_client):
    """
    CRITICAL SECURITY TEST:
    Even if an administrator is logged in via cookie session, if a request presents
    invalid device headers, it MUST be rejected with HTTP 401 and NEVER fall back
    to the administrator session.
    """
    # 1. First get login form to extract CSRF token
    login_get = auth_client.get("/login")
    assert login_get.status_code == 200

    # 2. Log in as administrator
    login_res = auth_client.post(
        "/login",
        json={"username": "admin_user", "password": "AdminSecurePassword123!"},
        headers={"X-CSRF-Token": auth_client.session_transaction().__enter__()["csrf_token"]}
    )
    assert login_res.status_code == 200

    # 3. Send scan request with active admin session BUT invalid device key
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": "wrong-key-while-admin-is-logged-in",
        "Content-Type": "application/json",
    }
    response = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": TEST_DEVICE_ID},
        headers=headers
    )

    # Must be 401 Unauthorized, NOT 201 Created!
    assert response.status_code == 401
    assert response.get_json()["success"] is False
    assert "Invalid device API key" in response.get_json()["message"]


# ==============================================================================
# 3. ENDPOINT AND METHOD BOUNDARY TESTS
# ==============================================================================

def test_11_device_authentication_rejected_on_non_scan_endpoints(auth_client):
    """
    Verify that device credentials cannot be used to access other administrative endpoints
    (e.g. POST /api/students, POST /api/sessions).
    Must return HTTP 403 Forbidden.
    """
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": TEST_API_KEY,
        "Content-Type": "application/json",
    }

    # Attempt to register student using device headers
    res_student = auth_client.post(
        "/api/students",
        json={"roll_number": "999", "full_name": "Hacker", "card_uid": "HACK99"},
        headers=headers
    )
    assert res_student.status_code == 403
    assert "restricted to POST /api/scans" in res_student.get_json()["message"]

    # Attempt to create session using device headers
    res_session = auth_client.post(
        "/api/sessions",
        json={"course_name": "Rogue Course", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "11:00"},
        headers=headers
    )
    assert res_session.status_code == 403
    assert "restricted to POST /api/scans" in res_session.get_json()["message"]


# ==============================================================================
# 4. DASHBOARD SESSION & SIMULATOR INTEGRATION TESTS
# ==============================================================================

def test_12_dashboard_session_requires_valid_csrf_token(auth_client):
    """
    Verify that dashboard session requests without device headers still strictly
    enforce CSRF token protection.
    """
    # 1. Log in as administrator
    auth_client.get("/login")
    csrf_token = auth_client.session_transaction().__enter__()["csrf_token"]
    auth_client.post(
        "/login",
        json={"username": "admin_user", "password": "AdminSecurePassword123!"},
        headers={"X-CSRF-Token": csrf_token}
    )

    # 2. Call POST /api/scans without CSRF token -> Rejected with HTTP 403
    res_no_csrf = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": "DASHBOARD_SIMULATOR"},
        headers={"Content-Type": "application/json"}
    )
    assert res_no_csrf.status_code == 403
    assert "CSRF verification failed" in res_no_csrf.get_json()["message"]

    # 3. Call POST /api/scans WITH valid CSRF token -> Succeeds with HTTP 201
    csrf_token_active = auth_client.session_transaction().__enter__()["csrf_token"]
    res_with_csrf = auth_client.post(
        "/api/scans",
        json={"card_uid": "A1B2C3D4", "source": "DASHBOARD_SIMULATOR"},
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token_active}
    )
    assert res_with_csrf.status_code == 201
    assert res_with_csrf.get_json()["success"] is True


def test_13_duplicate_scan_with_device_auth_returns_409(auth_client):
    """Verify that scanning twice with valid device auth detects duplicate attendance."""
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": TEST_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"card_uid": "A1B2C3D4", "source": TEST_DEVICE_ID}

    # First scan -> 201
    res1 = auth_client.post("/api/scans", json=payload, headers=headers)
    assert res1.status_code == 201

    # Second scan -> 409 DUPLICATE
    res2 = auth_client.post("/api/scans", json=payload, headers=headers)
    assert res2.status_code == 409
    data = res2.get_json()
    assert data["scan_result"] == "DUPLICATE"
    assert data["student"]["roll_number"] == "101"


def test_14_unknown_uid_with_device_auth_returns_404(auth_client):
    """Verify that scanning an unregistered card with valid device auth returns HTTP 404."""
    headers = {
        "X-Device-Id": TEST_DEVICE_ID,
        "X-API-Key": TEST_API_KEY,
        "Content-Type": "application/json",
    }
    response = auth_client.post(
        "/api/scans",
        json={"card_uid": "UNKNOWN_CARD_99", "source": TEST_DEVICE_ID},
        headers=headers
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data["scan_result"] == "UNKNOWN_CARD"
    assert "student" not in data or data.get("student") is None
