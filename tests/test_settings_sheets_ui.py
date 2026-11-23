"""
Tests for Google Sheets Synchronization Monitoring UI in Settings Page.
Verifies frontend markup, CSS integration, API status telemetry endpoint,
and strict database isolation.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import pytest

from app import create_app
from app.auth import hash_password
from app.database import init_db, get_db_connection

PROTECTED_ORIGINAL_DB = Path("database/attendance.db")
EXPECTED_ORIGINAL_HASH = "0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9"


@pytest.fixture
def isolated_app(tmp_path):
    """
    Creates an isolated test application with its own database and configuration.
    Guarantees that tests never touch the production or manual test databases.
    """
    test_db = tmp_path / "test_settings_sheets.db"
    test_backups = tmp_path / "backups"
    test_backups.mkdir(parents=True, exist_ok=True)

    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db
    app.config["BACKUP_DIR"] = test_backups
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["GOOGLE_SHEETS_SYNC_ENABLED"] = True

    init_db(test_db)

    # Seed an admin user for authentication tests
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    now_utc = datetime.now(timezone.utc).isoformat()
    pwd_hash = hash_password("TestAdminSecret123!")
    cursor.execute(
        """
        INSERT INTO admin_users (username, password_hash, role, active, created_at)
        VALUES ('admin_sheets_tester', ?, 'admin', 1, ?);
        """,
        (pwd_hash, now_utc)
    )
    conn.commit()
    conn.close()

    yield app


@pytest.fixture
def isolated_client(isolated_app):
    """Test client for isolated application."""
    return isolated_app.test_client()


def authenticate_client(client, username="admin_sheets_tester", password="TestAdminSecret123!"):
    """Logs the client in via POST /login."""
    res = client.post(
        "/login",
        json={"username": username, "password": password},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    return res


# ==============================================================================
# UI MARKUP TESTS: SETTINGS PAGE
# ==============================================================================

def test_settings_page_unauthenticated_redirects_to_login(isolated_client):
    """Unauthenticated access to /settings redirects to login page."""
    res = isolated_client.get("/settings")
    assert res.status_code in (302, 303)
    assert "/login" in res.headers.get("Location", "")


def test_settings_page_renders_sheets_sync_section(isolated_app, isolated_client):
    """Authenticated admin sees the Google Sheets Synchronization card and all elements."""
    authenticate_client(isolated_client)

    res = isolated_client.get("/settings")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Section Card and Header
    assert 'id="card-sheets-sync"' in html
    assert "Google Sheets Synchronization" in html
    assert 'id="btn-refresh-sheets-sync"' in html
    assert 'id="sheets-refresh-icon"' in html
    assert 'id="sheets-refresh-text"' in html

    # Non-disruptive Error Banner
    assert 'id="sheets-sync-error-banner"' in html

    # Configuration Status Rows
    assert 'id="sheets-config-sync-status"' in html
    assert 'id="sheets-config-creds-status"' in html
    assert 'id="sheets-config-sheet-status"' in html
    assert 'id="sheets-last-sync-time"' in html

    # 6 Outbox Queue Metric Cards
    assert 'id="card-metric-pending"' in html
    assert 'id="sheets-metric-pending"' in html
    assert 'id="card-metric-synced"' in html
    assert 'id="sheets-metric-synced"' in html
    assert 'id="card-metric-inflight"' in html
    assert 'id="sheets-metric-inflight"' in html
    assert 'id="card-metric-deadletter"' in html
    assert 'id="sheets-metric-deadletter"' in html
    assert 'id="card-metric-failed"' in html
    assert 'id="sheets-metric-failed"' in html
    assert 'id="card-metric-total"' in html
    assert 'id="sheets-metric-total"' in html

    # Static CSS and JS assets are loaded
    assert "settings.css" in html
    assert "settings.js" in html


# ==============================================================================
# API ENDPOINT TESTS: GET /api/admin/sheets-sync/status
# ==============================================================================

def test_sheets_sync_status_unauthenticated_returns_401(isolated_client):
    """GET /api/admin/sheets-sync/status returns 401 Unauthorized for unauthenticated requests."""
    res = isolated_client.get("/api/admin/sheets-sync/status")
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False
    assert "Unauthorized" in data.get("error", "") or "Administrator authentication" in data.get("message", "")


def test_sheets_sync_status_authenticated_returns_valid_payload(isolated_app, isolated_client):
    """Authenticated admin receives valid telemetry payload."""
    authenticate_client(isolated_client)

    res = isolated_client.get("/api/admin/sheets-sync/status")
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    assert "sync_enabled" in data
    assert "credentials_configured" in data
    assert "spreadsheet_id_configured" in data
    assert "queue_stats" in data
    assert "last_sync_timestamp" in data

    queue_stats = data["queue_stats"]
    assert "pending" in queue_stats
    assert "synced" in queue_stats
    assert "in_progress" in queue_stats
    assert "dead_letter" in queue_stats
    assert "failed" in queue_stats
    assert "total" in queue_stats


def test_sheets_sync_status_reflects_disabled_sync(isolated_app, isolated_client):
    """Reflects GOOGLE_SHEETS_SYNC_ENABLED=False configuration correctly."""
    isolated_app.config["GOOGLE_SHEETS_SYNC_ENABLED"] = False
    authenticate_client(isolated_client)

    res = isolated_client.get("/api/admin/sheets-sync/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["sync_enabled"] is False


def test_sheets_sync_status_reflects_queue_telemetry(isolated_app, isolated_client):
    """Reflects actual queue counts and last sync timestamp from sheets_sync_queue table."""
    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    # Insert dummy students and session for foreign key constraints
    cursor.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES 
            ('TST-01', 'Test Student 1', '11:22:33:44', 1, '2026-11-22T10:00:00Z'),
            ('TST-02', 'Test Student 2', '11:22:33:55', 1, '2026-11-22T10:00:00Z'),
            ('TST-03', 'Test Student 3', '11:22:33:66', 1, '2026-11-22T10:00:00Z');
        """
    )

    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Test Course', '2026-11-22', '10:00', '11:00', 0, '2026-11-22T10:00:00Z');
        """
    )
    session_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (1, ?, '2026-11-22T10:05:00Z', 'Present', 'rfid');
        """,
        (session_id,)
    )
    att1 = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (2, ?, '2026-11-22T10:10:00Z', 'Present', 'rfid');
        """,
        (session_id,)
    )
    att2 = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (3, ?, '2026-11-22T10:15:00Z', 'Present', 'rfid');
        """,
        (session_id,)
    )
    att3 = cursor.lastrowid

    # Queue records: 1 synced, 1 pending, 1 dead_letter
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (attendance_id, sync_status, created_at, updated_at, synced_at)
        VALUES (?, 'synced', '2026-11-22T10:05:00Z', '2026-11-22T10:06:00Z', '2026-11-22T10:06:00Z');
        """,
        (att1,)
    )
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (attendance_id, sync_status, created_at, updated_at)
        VALUES (?, 'pending', '2026-11-22T10:10:00Z', '2026-11-22T10:10:00Z');
        """,
        (att2,)
    )
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (attendance_id, sync_status, created_at, updated_at, last_error)
        VALUES (?, 'dead_letter', '2026-11-22T10:15:00Z', '2026-11-22T10:15:00Z', 'Test dead letter');
        """,
        (att3,)
    )

    conn.commit()
    conn.close()

    authenticate_client(isolated_client)
    res = isolated_client.get("/api/admin/sheets-sync/status")
    assert res.status_code == 200
    data = res.get_json()

    stats = data["queue_stats"]
    assert stats["synced"] == 1
    assert stats["pending"] == 1
    assert stats["dead_letter"] == 1
    assert stats["total"] == 3
    assert data["last_sync_timestamp"] == "2026-11-22T10:06:00Z"


# ==============================================================================
# DATABASE INTEGRITY TEST
# ==============================================================================

def test_original_database_remains_untouched():
    """Confirms the protected production database was never accessed or modified."""
    if PROTECTED_ORIGINAL_DB.exists():
        current_hash = hashlib.sha256(PROTECTED_ORIGINAL_DB.read_bytes()).hexdigest().upper()
        assert current_hash == EXPECTED_ORIGINAL_HASH, (
            f"Original database hash mismatch! Expected {EXPECTED_ORIGINAL_HASH}, got {current_hash}"
        )
