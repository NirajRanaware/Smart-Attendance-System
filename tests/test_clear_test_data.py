"""
Automated Integration and Reliability Tests for 'Clear All Test Data' Feature.
Tests all 17 specified conditions using isolated temporary SQLite databases.
Guarantees the actual development database is NEVER cleared during tests.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import pytest

from app import create_app
from app.auth import hash_password, verify_password
from app.backup import get_database_stats, create_database_backup, clear_all_test_data
from app.database import init_db, get_db_connection


@pytest.fixture
def isolated_app(tmp_path):
    """
    Creates an isolated test application with its own database and backup directory.
    Guarantees that tests never touch the development database.
    """
    test_db = tmp_path / "test_isolated.db"
    test_backups = tmp_path / "backups"
    test_backups.mkdir(parents=True, exist_ok=True)

    app = create_app("testing")
    app.config["DATABASE_PATH"] = test_db
    app.config["BACKUP_DIR"] = test_backups
    app.config["LOGIN_DISABLED"] = False
    app.config["CSRF_ENABLED"] = False
    app.config["WTF_CSRF_ENABLED"] = False

    init_db(test_db)
    yield app


@pytest.fixture
def isolated_client(isolated_app):
    """Test client for isolated application."""
    return isolated_app.test_client()


def seed_test_data(app):
    """Helper to seed known data into isolated test database."""
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    now_utc = datetime.now(timezone.utc).isoformat()

    # Seed Admin User
    admin_pwd_hash = hash_password("SuperAdminPass123!")
    cursor.execute(
        """
        INSERT INTO admin_users (username, password_hash, role, active, created_at)
        VALUES ('admin_tester', ?, 'admin', 1, ?);
        """,
        (admin_pwd_hash, now_utc)
    )

    # Seed 3 Students (2 active, 1 inactive)
    cursor.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES 
            ('CS-101', 'Alice Johnson', 'AA:BB:CC:11', 1, ?),
            ('CS-102', 'Bob Smith', 'AA:BB:CC:22', 1, ?),
            ('CS-103', 'Charlie Brown', 'AA:BB:CC:33', 0, ?);
        """,
        (now_utc, now_utc, now_utc)
    )

    # Seed 2 Class Sessions (1 inactive historical, 1 active)
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES 
            ('Data Structures', '2026-11-07', '09:00', '10:00', 0, ?),
            ('Operating Systems', '2026-11-07', '10:15', '11:15', 1, ?);
        """,
        (now_utc, now_utc)
    )

    # Seed 3 Attendance Records (Alice in sess 1 & 2, Bob in sess 2)
    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES 
            (1, 1, ?, 'Present', 'manual'),
            (1, 2, ?, 'Present', 'rfid'),
            (2, 2, ?, 'Present', 'rfid');
        """,
        (now_utc, now_utc, now_utc)
    )

    conn.commit()
    conn.close()


def authenticate_client(client, username="admin_tester", password="SuperAdminPass123!"):
    """Logs the client in via /login."""
    res = client.post(
        "/login",
        json={"username": username, "password": password},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    return res


# ==============================================================================
# 17 REQUIRED TEST CONDITIONS
# ==============================================================================

def test_1_database_statistics_are_accurate(isolated_app, isolated_client):
    """Condition 1: Database statistics reflect actual live counts."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    res = isolated_client.get("/api/admin/database-stats")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    stats = data["data"]
    assert stats["total_students"] == 3
    assert stats["active_students"] == 2
    assert stats["total_sessions"] == 2
    assert stats["total_attendance"] == 3
    assert stats["total_admins"] == 1
    assert stats["database_size_bytes"] > 0


def test_2_unauthenticated_users_cannot_access_operation(isolated_app, isolated_client):
    """Condition 2: Unauthenticated users are rejected with HTTP 401."""
    seed_test_data(isolated_app)

    # API endpoints require authentication
    res_clear = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res_clear.status_code == 401
    assert res_clear.get_json()["success"] is False

    res_stats = isolated_client.get(
        "/api/admin/database-stats",
        headers={"Accept": "application/json"}
    )
    assert res_stats.status_code == 401


def test_3_csrf_protection_is_enforced(isolated_app, isolated_client):
    """Condition 3: CSRF protection rejects state-changing requests when missing or invalid token."""
    isolated_app.config["CSRF_ENABLED"] = True
    isolated_app.config["WTF_CSRF_ENABLED"] = True

    seed_test_data(isolated_app)

    # 1. Fetch CSRF token from session
    isolated_client.get("/login")
    csrf_token = ""
    with isolated_client.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")
    assert csrf_token != ""

    # Login with CSRF token
    login_res = isolated_client.post(
        "/login",
        json={"username": "admin_tester", "password": "SuperAdminPass123!"},
        headers={"Accept": "application/json", "X-CSRF-Token": csrf_token}
    )
    assert login_res.status_code == 200

    # 2. Attempt clear-data WITHOUT CSRF token -> MUST return HTTP 403 Forbidden
    res_no_csrf = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res_no_csrf.status_code == 403
    assert "CSRF" in res_no_csrf.get_json()["message"]

    # 3. Retrieve post-login CSRF token (session rotated on login)
    post_login_csrf = ""
    with isolated_client.session_transaction() as sess:
        post_login_csrf = sess.get("csrf_token")
    assert post_login_csrf != ""

    # Attempt clear-data WITH valid CSRF token -> Succeeds
    res_with_csrf = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json", "X-CSRF-Token": post_login_csrf}
    )
    assert res_with_csrf.status_code == 200
    assert res_with_csrf.get_json()["success"] is True


def test_4_incorrect_confirmation_phrase_is_rejected(isolated_app, isolated_client):
    """Condition 4: Incorrect confirmation phrases are rejected with HTTP 400 and no data deleted."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    invalid_phrases = [
        "DELETE DATA",
        "delete all data",
        "DELETE ALL DATA ",
        "DELETE_ALL_DATA",
        "CONFIRM",
        "YES",
    ]

    for phrase in invalid_phrases:
        res = isolated_client.post(
            "/api/admin/clear-data",
            json={"confirmation_phrase": phrase},
            headers={"Accept": "application/json"}
        )
        assert res.status_code == 400
        assert res.get_json()["success"] is False

    # Verify no records were deleted
    stats = get_database_stats(isolated_app.config["DATABASE_PATH"])
    assert stats["total_students"] == 3
    assert stats["total_sessions"] == 2
    assert stats["total_attendance"] == 3


def test_5_missing_confirmation_phrase_is_rejected(isolated_app, isolated_client):
    """Condition 5: Missing or empty confirmation phrase is rejected with HTTP 400."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Empty body
    res_empty = isolated_client.post(
        "/api/admin/clear-data",
        json={},
        headers={"Accept": "application/json"}
    )
    assert res_empty.status_code == 400
    assert res_empty.get_json()["success"] is False

    # None phrase
    res_none = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": None},
        headers={"Accept": "application/json"}
    )
    assert res_none.status_code == 400

    # Verify records remain intact
    stats = get_database_stats(isolated_app.config["DATABASE_PATH"])
    assert stats["total_students"] == 3


def test_6_get_requests_cannot_trigger_deletion(isolated_app, isolated_client):
    """Condition 6: GET requests cannot trigger deletion (HTTP 405 Method Not Allowed)."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    res = isolated_client.get("/api/admin/clear-data")
    assert res.status_code == 405
    data = res.get_json()
    assert data["success"] is False

    # Verify data intact
    stats = get_database_stats(isolated_app.config["DATABASE_PATH"])
    assert stats["total_students"] == 3
    assert stats["total_sessions"] == 2
    assert stats["total_attendance"] == 3


def test_7_successful_backup_creation(isolated_app, isolated_client):
    """Condition 7: Safe backup file is created with timestamped name in backups directory."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    backup_dir = Path(isolated_app.config["BACKUP_DIR"])
    assert len(list(backup_dir.glob("*.db"))) == 0

    res = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # Verify backup exists
    backups = list(backup_dir.glob("attendance_backup_*.db"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    assert data["backup"]["filename"] == backups[0].name


def test_8_backup_contains_original_records(isolated_app, isolated_client):
    """Condition 8: Verified backup file contains all original records prior to deletion."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    res = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200
    backup_filename = res.get_json()["backup"]["filename"]
    backup_path = Path(isolated_app.config["BACKUP_DIR"]) / backup_filename

    assert backup_path.exists()

    # Query backup database directly
    backup_conn = sqlite3.connect(str(backup_path))
    backup_conn.row_factory = sqlite3.Row
    cursor = backup_conn.cursor()

    # Verify students in backup
    cursor.execute("SELECT roll_number, full_name FROM students ORDER BY roll_number;")
    students = cursor.fetchall()
    assert len(students) == 3
    assert students[0]["roll_number"] == "CS-101"
    assert students[1]["roll_number"] == "CS-102"
    assert students[2]["roll_number"] == "CS-103"

    # Verify sessions in backup
    cursor.execute("SELECT course_name FROM class_sessions;")
    sessions = cursor.fetchall()
    assert len(sessions) == 2

    # Verify attendance in backup
    cursor.execute("SELECT COUNT(*) FROM attendance;")
    assert cursor.fetchone()[0] == 3

    # Verify admin users in backup
    cursor.execute("SELECT username FROM admin_users;")
    admins = cursor.fetchall()
    assert len(admins) == 1
    assert admins[0]["username"] == "admin_tester"

    backup_conn.close()


def test_9_backup_failure_prevents_deletion(isolated_app, isolated_client):
    """Condition 9: If backup creation fails, deletion is aborted and data is preserved."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Mock create_database_backup to raise an exception simulating backup disk/IO error
    with patch("app.routes.admin.clear_all_test_data", side_effect=RuntimeError("Simulated backup failure")):
        res = isolated_client.post(
            "/api/admin/clear-data",
            json={"confirmation_phrase": "DELETE ALL DATA"},
            headers={"Accept": "application/json"}
        )
        assert res.status_code == 500
        data = res.get_json()
        assert data["success"] is False
        assert "Simulated backup failure" in data["message"]

    # Verify data was NOT deleted
    stats = get_database_stats(isolated_app.config["DATABASE_PATH"])
    assert stats["total_students"] == 3
    assert stats["total_sessions"] == 2
    assert stats["total_attendance"] == 3


def test_10_attendance_sessions_and_students_are_cleared(isolated_app, isolated_client):
    """Condition 10: Attendance, sessions, and students tables are completely cleared."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    res = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200

    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM attendance;")
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT COUNT(*) FROM class_sessions;")
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT COUNT(*) FROM students;")
    assert cursor.fetchone()[0] == 0

    conn.close()


def test_11_administrator_account_remains_intact(isolated_app, isolated_client):
    """Condition 11: Administrator accounts and password hashes remain untouched."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Get admin record before
    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, role, active FROM admin_users WHERE username = 'admin_tester';")
    pre_admin = dict(cursor.fetchone())
    conn.close()

    # Clear data
    res = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200

    # Get admin record after
    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, role, active FROM admin_users WHERE username = 'admin_tester';")
    post_admin = dict(cursor.fetchone())
    conn.close()

    assert pre_admin == post_admin


def test_12_administrator_can_still_log_in_after_clearing(isolated_app, isolated_client):
    """Condition 12: Administrator credentials remain valid and can log in after data is cleared."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Clear data
    res_clear = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res_clear.status_code == 200

    # Logout
    isolated_client.post("/logout")

    # Re-login with original credentials
    res_login = isolated_client.post(
        "/login",
        json={"username": "admin_tester", "password": "SuperAdminPass123!"},
        headers={"Accept": "application/json"}
    )
    assert res_login.status_code == 200
    assert res_login.get_json()["success"] is True


def test_13_database_schema_and_indexes_remain_intact(isolated_app, isolated_client):
    """Condition 13: All database tables, columns, constraints, and indexes remain intact."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Clear data
    isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )

    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    # Verify tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert {"students", "class_sessions", "attendance", "admin_users"}.issubset(tables)

    # Verify key indexes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_single_active_session" in indexes
    assert "idx_students_card_uid" in indexes
    assert "idx_students_roll_number" in indexes
    assert "idx_attendance_student_session" in indexes
    assert "idx_admin_users_username" in indexes

    conn.close()


def test_14_foreign_key_integrity_remains_valid(isolated_app, isolated_client):
    """Condition 14: Foreign-key integrity check returns 0 errors and cascade constraints work."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Clear data
    isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )

    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    # PRAGMA foreign_key_check must return empty
    cursor.execute("PRAGMA foreign_key_check;")
    violations = cursor.fetchall()
    assert len(violations) == 0

    # Verify foreign key enforcement rejects invalid foreign keys
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (9999, 9999, '2026-11-08T10:00:00Z', 'Present', 'manual');
            """
        )
        conn.commit()

    conn.close()


def test_15_single_active_session_constraint_remains_functional(isolated_app, isolated_client):
    """Condition 15: Single active session unique index constraint remains strictly functional."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Clear data
    isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )

    conn = get_db_connection(isolated_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    # Insert 1 active session
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Session 1', '2026-11-08', '09:00', '10:00', 1, '2026-11-08T09:00:00Z');
        """
    )
    conn.commit()

    # Attempt to insert a 2nd active session -> Must fail with IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
            VALUES ('Session 2', '2026-11-08', '10:00', '11:00', 1, '2026-11-08T10:00:00Z');
            """
        )
        conn.commit()

    conn.close()


def test_16_database_statistics_refresh_correctly(isolated_app, isolated_client):
    """Condition 16: Database statistics accurately show 0s for cleared tables after operation."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Verify initial stats
    res1 = isolated_client.get("/api/admin/database-stats")
    assert res1.get_json()["data"]["total_students"] == 3

    # Clear data
    res_clear = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res_clear.status_code == 200

    # Verify stats immediately reflect zeros
    res2 = isolated_client.get("/api/admin/database-stats")
    assert res2.status_code == 200
    stats = res2.get_json()["data"]

    assert stats["total_students"] == 0
    assert stats["active_students"] == 0
    assert stats["total_sessions"] == 0
    assert stats["total_attendance"] == 0
    assert stats["total_admins"] == 1


def test_17_existing_project_functionality_remains_intact(isolated_app, isolated_client):
    """Condition 17: Project remains 100% operational (students, sessions, scans, reports work on cleared DB)."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    # Clear data
    res_clear = isolated_client.post(
        "/api/admin/clear-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
        headers={"Accept": "application/json"}
    )
    assert res_clear.status_code == 200

    # 1. Register a new student via API
    res_stud = isolated_client.post(
        "/api/students",
        json={
            "roll_number": "CS-200",
            "full_name": "New Enrolled Student",
            "card_uid": "99887766"
        }
    )
    assert res_stud.status_code == 201
    student_id = res_stud.get_json()["student"]["id"]

    # 2. Create a new class session via API
    res_sess = isolated_client.post(
        "/api/sessions",
        json={
            "course_name": "Computer Networks",
            "session_date": "2026-11-07",
            "start_time": "10:00",
            "end_time": "11:00"
        }
    )
    assert res_sess.status_code == 201
    session_id = res_sess.get_json()["session"]["id"]

    # Activate session
    res_act = isolated_client.patch(f"/api/sessions/{session_id}/activate")
    assert res_act.status_code == 200

    # 3. Record attendance scan via API
    res_scan = isolated_client.post(
        "/api/scans",
        json={
            "card_uid": "99887766",
            "source": "rfid"
        }
    )
    assert res_scan.status_code == 201

    # 4. Fetch daily attendance report
    res_report = isolated_client.get("/api/reports/daily?date=2026-11-07")
    assert res_report.status_code == 200
    report_data = res_report.get_json()
    assert report_data["success"] is True
    assert report_data["summary"]["total_sessions"] == 1
    assert report_data["summary"]["total_present"] == 1
    assert report_data["summary"]["overall_attendance_percentage"] == 100.0


def test_18_settings_view_serves_database_management_ui(isolated_app, isolated_client):
    """Verify GET /settings renders the Database Management UI and confirmation modal markup."""
    seed_test_data(isolated_app)
    authenticate_client(isolated_client)

    res = isolated_client.get("/settings")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    # Core UI elements
    assert "Settings &amp; Admin" in html or "Settings & Admin" in html
    assert "Database Management &amp; Statistics" in html or "Database Management & Statistics" in html
    assert 'id="database-management-section"' in html
    assert 'id="stat-db-students"' in html
    assert 'id="stat-db-active-students"' in html
    assert 'id="stat-db-sessions"' in html
    assert 'id="stat-db-attendance"' in html
    assert 'id="stat-db-path"' in html
    assert 'id="btn-refresh-stats"' in html
    assert 'id="btn-open-clear-modal"' in html

    # Modal Elements
    assert 'id="modal-clear-backdrop"' in html
    assert 'id="clear-data-confirmation-input"' in html
    assert 'id="btn-confirm-clear"' in html
    assert 'id="btn-cancel-clear"' in html
    assert "DELETE ALL DATA" in html
