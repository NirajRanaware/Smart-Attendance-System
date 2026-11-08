"""
Automated Unit and Integration Tests for Google Sheets Local Outbox Service (Phase 3 Milestone 2).
Covers all 20 mandatory scenarios using isolated temporary SQLite databases.
Guarantees the actual development database is NEVER modified.
"""

import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from app import create_app
from app.auth import hash_password
from app.backup import clear_all_test_data, get_database_stats
from app.database import init_db, get_db_connection, close_db_connection, migrate_db
from app.models import CREATE_TABLES_SQL, CREATE_INDEXES_SQL
from app.services.sheets_outbox import (
    create_queue_record,
    get_queue_stats,
    get_pending_records,
    claim_records,
    recover_expired_leases,
    mark_as_synced,
    calculate_retry_delay,
    schedule_retry,
    mark_as_failed,
    move_to_dead_letter,
)


@pytest.fixture
def outbox_app(tmp_path):
    """Creates an isolated test application with its own database."""
    test_db = tmp_path / "test_outbox_app.db"
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
def outbox_client(outbox_app):
    """Provides authenticated Flask test client."""
    client = outbox_app.test_client()
    return client


def seed_test_student_and_session(app):
    """Helper to seed 1 student and 1 active session."""
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    now_utc = "2026-11-07T10:00:00Z"

    # Seed Admin
    cursor.execute(
        """
        INSERT INTO admin_users (username, password_hash, role, active, created_at)
        VALUES ('admin_outbox', ?, 'admin', 1, ?);
        """,
        (hash_password("Pass12345!"), now_utc)
    )

    # Seed Student
    cursor.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('CS-101', 'Alice Johnson', 'AA11BB22', 1, ?);
        """,
        (now_utc,)
    )

    # Seed Active Session
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Distributed Systems', '2026-11-07', '10:00', '11:00', 1, ?);
        """,
        (now_utc,)
    )

    conn.commit()
    conn.close()


def authenticate_client(client, username="admin_outbox", password="Pass12345!"):
    """Helper to log in the test client."""
    res = client.post(
        "/login",
        json={"username": username, "password": password},
        headers={"Accept": "application/json"}
    )
    assert res.status_code == 200


# ==============================================================================
# 20 REQUIRED TEST SCENARIOS
# ==============================================================================

def test_1_queue_table_migration_on_new_database(tmp_path):
    """Scenario 1: Queue table and indexes are created on a fresh database initialization."""
    db_path = tmp_path / "fresh.db"
    init_db(db_path)

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_queue';")
    assert cursor.fetchone() is not None, "sheets_sync_queue table must exist on fresh database."

    # Verify column structure
    cursor.execute("PRAGMA table_info(sheets_sync_queue);")
    columns = {row["name"] for row in cursor.fetchall()}
    expected_cols = {
        "id", "attendance_id", "sync_status", "retry_count",
        "next_retry_at", "lease_expires_at", "last_error",
        "created_at", "updated_at", "synced_at"
    }
    assert expected_cols.issubset(columns)

    # Verify indexes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sheets_sync_queue';")
    indexes = {row["name"] for row in cursor.fetchall()}
    assert "idx_sync_queue_status_retry" in indexes
    assert "idx_sync_queue_attendance_id" in indexes
    assert "idx_sync_queue_lease" in indexes
    conn.close()


def test_2_migration_on_existing_database(tmp_path):
    """Scenario 2: Forward migration adds sheets_sync_queue to pre-existing database without destroying data."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    # Create legacy schema without sheets_sync_queue
    conn.executescript("""
        CREATE TABLE students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_number TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            card_uid TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE class_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            scanned_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Present',
            source TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE,
            UNIQUE(student_id, session_id)
        );
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('LEG-1', 'Legacy Student', 'LEGACY_UID', 1, '2026-11-01T00:00:00Z');
    """)
    conn.commit()

    # Apply migration
    migrate_db(conn)

    # Verify legacy student preserved
    cursor = conn.cursor()
    cursor.execute("SELECT roll_number FROM students;")
    assert cursor.fetchone()[0] == "LEG-1"

    # Verify sheets_sync_queue was created
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_queue';")
    assert cursor.fetchone() is not None
    conn.close()


def test_3_repeated_migration_without_damage(tmp_path):
    """Scenario 3: Repeated migration calls are strictly idempotent and cause zero errors or data alteration."""
    db_path = tmp_path / "idempotent.db"
    init_db(db_path)

    conn = get_db_connection(db_path)
    # Run migrate_db multiple consecutive times
    for _ in range(5):
        migrate_db(conn)

    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check;")
    assert cursor.fetchone()[0] == "ok"
    conn.close()


def test_4_existing_attendance_records_remain_intact(tmp_path):
    """Scenario 4: Existing attendance records prior to migration are untouched."""
    db_path = tmp_path / "existing_attendance.db"
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("INSERT INTO students (roll_number, full_name, card_uid, active, created_at) VALUES ('S1', 'Name', 'UID1', 1, 'now');")
    s_id = cursor.lastrowid
    cursor.execute("INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at) VALUES ('C1', '2026-11-07', '10:00', '11:00', 1, 'now');")
    c_id = cursor.lastrowid
    cursor.execute("INSERT INTO attendance (student_id, session_id, scanned_at, status, source) VALUES (?, ?, '2026-11-07T10:15:00Z', 'Present', 'rfid');", (s_id, c_id))
    att_id = cursor.lastrowid
    conn.commit()

    # Re-run migration
    migrate_db(conn)

    # Verify attendance record exists unchanged
    cursor.execute("SELECT id, status, scanned_at FROM attendance WHERE id = ?;", (att_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["status"] == "Present"
    conn.close()


def test_5_new_attendance_creates_exactly_one_queue_record(outbox_app, outbox_client):
    """Scenario 5: Creating attendance via POST /api/scans creates exactly one matching queue record."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)

    # Perform card scan
    res = outbox_client.post(
        "/api/scans",
        json={"card_uid": "AA11BB22", "source": "rfid"}
    )
    assert res.status_code == 201
    attendance_id = res.get_json()["attendance"]["id"]

    # Verify queue record
    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sheets_sync_queue WHERE attendance_id = ?;", (attendance_id,))
    rows = cursor.fetchall()
    assert len(rows) == 1
    queue_row = rows[0]
    assert queue_row["attendance_id"] == attendance_id
    assert queue_row["sync_status"] == "pending"
    assert queue_row["retry_count"] == 0
    conn.close()


def test_6_attendance_rollback_also_rolls_back_queue_insertion(outbox_app):
    """Scenario 6: If the attendance transaction rolls back, no queue record remains."""
    seed_test_student_and_session(outbox_app)
    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    try:
        conn.execute("BEGIN IMMEDIATE;")
        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (1, 1, '2026-11-07T10:15:00Z', 'Present', 'manual');
            """
        )
        att_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at)
            VALUES (?, 'pending', 0, '2026-11-07T10:15:00Z', '2026-11-07T10:15:00Z');
            """,
            (att_id,)
        )
        # Simulate a transaction failure/abort before commit
        conn.rollback()
    finally:
        pass

    # Verify neither attendance nor queue record exists
    cursor.execute("SELECT COUNT(*) FROM attendance;")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM sheets_sync_queue;")
    assert cursor.fetchone()[0] == 0
    conn.close()


def test_7_duplicate_attendance_does_not_create_duplicate_queue_records(outbox_app, outbox_client):
    """Scenario 7: Duplicate scan attempts return HTTP 409 and create no additional queue records."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)

    # First scan -> 201
    res1 = outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})
    assert res1.status_code == 201

    # Second scan (same student & session) -> 409
    res2 = outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})
    assert res2.status_code == 409

    # Verify only 1 queue record exists
    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["total"] == 1
    assert stats["pending"] == 1


def test_8_queue_status_defaults_to_pending(outbox_app):
    """Scenario 8: Default sync_status is 'pending' and retry_count is 0."""
    seed_test_student_and_session(outbox_app)
    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (1, 1, '2026-11-07T10:15:00Z', 'Present', 'manual');
        """
    )
    att_id = cursor.lastrowid
    q_id = create_queue_record(conn, att_id)
    conn.commit()

    cursor.execute("SELECT sync_status, retry_count, synced_at, last_error FROM sheets_sync_queue WHERE id = ?;", (q_id,))
    row = cursor.fetchone()
    assert row["sync_status"] == "pending"
    assert row["retry_count"] == 0
    assert row["synced_at"] is None
    assert row["last_error"] is None
    conn.close()


def test_9_pending_records_can_be_retrieved(outbox_app, outbox_client):
    """Scenario 9: get_pending_records returns all pending items with student/session joins."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    records = get_pending_records(limit=10, db_path=outbox_app.config["DATABASE_PATH"])
    assert len(records) == 1
    rec = records[0]
    assert rec["roll_number"] == "CS-101"
    assert rec["student_name"] == "Alice Johnson"
    assert rec["course_name"] == "Distributed Systems"
    assert rec["sync_status"] == "pending"


def test_10_records_can_be_claimed_safely(outbox_app, outbox_client):
    """Scenario 10: claim_records sets status to 'in_progress' and sets lease_expires_at."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    base_time = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
    claimed = claim_records(
        worker_id="worker-test",
        limit=5,
        lease_duration_seconds=120,
        now_dt=base_time,
        db_path=outbox_app.config["DATABASE_PATH"]
    )
    assert len(claimed) == 1
    assert claimed[0]["sync_status"] == "in_progress"
    assert claimed[0]["lease_expires_at"] == "2026-11-08T10:02:00Z"

    # Verify in DB
    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["in_progress"] == 1
    assert stats["pending"] == 0


def test_11_concurrent_workers_cannot_claim_same_record(outbox_app, outbox_client):
    """Scenario 11: When two workers attempt to claim records, each record is claimed by exactly one worker."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    base_time = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
    worker1_results = []
    worker2_results = []

    def run_worker(target_list, worker_id):
        res = claim_records(
            worker_id=worker_id,
            limit=10,
            now_dt=base_time,
            db_path=outbox_app.config["DATABASE_PATH"]
        )
        target_list.extend(res)

    t1 = threading.Thread(target=run_worker, args=(worker1_results, "worker-1"))
    t2 = threading.Thread(target=run_worker, args=(worker2_results, "worker-2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Total claimed across both workers must be exactly 1
    total_claimed = len(worker1_results) + len(worker2_results)
    assert total_claimed == 1


def test_12_expired_leases_can_be_recovered(outbox_app, outbox_client):
    """Scenario 12: Expired leases in 'in_progress' status are recovered back to 'pending'."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    t0 = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
    # Claim for 60 seconds (expires at 10:01:00Z)
    claim_records(limit=1, lease_duration_seconds=60, now_dt=t0, db_path=outbox_app.config["DATABASE_PATH"])

    # At 10:00:30 (before expiry), recovery should recover 0 records
    t_before = datetime(2026, 11, 8, 10, 0, 30, tzinfo=timezone.utc)
    rec_before = recover_expired_leases(now_dt=t_before, db_path=outbox_app.config["DATABASE_PATH"])
    assert rec_before == 0

    # At 10:01:05 (after expiry), recovery should recover 1 record
    t_after = datetime(2026, 11, 8, 10, 1, 5, tzinfo=timezone.utc)
    rec_after = recover_expired_leases(now_dt=t_after, db_path=outbox_app.config["DATABASE_PATH"])
    assert rec_after == 1

    # Verify status is back to 'pending'
    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["pending"] == 1
    assert stats["in_progress"] == 0


def test_13_successful_records_can_be_marked_synced(outbox_app, outbox_client):
    """Scenario 13: mark_as_synced transitions state to 'synced' and sets synced_at timestamp."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    claimed = claim_records(limit=1, db_path=outbox_app.config["DATABASE_PATH"])
    queue_id = claimed[0]["queue_id"]

    success = mark_as_synced(
        queue_id=queue_id,
        synced_at="2026-11-08T10:05:00Z",
        db_path=outbox_app.config["DATABASE_PATH"]
    )
    assert success is True

    # Verify in DB
    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["synced"] == 1
    assert stats["in_progress"] == 0


def test_14_retry_scheduling_works_correctly(outbox_app, outbox_client):
    """Scenario 14: schedule_retry increments retry_count and sets next_retry_at with backoff."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    claimed = claim_records(limit=1, db_path=outbox_app.config["DATABASE_PATH"])
    queue_id = claimed[0]["queue_id"]

    t_now = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
    # Schedule retry with 0 jitter (delay should be 15s)
    result = schedule_retry(
        queue_id=queue_id,
        error_message="Socket timeout connecting to Sheets API",
        base_delay=15,
        jitter=False,
        now_dt=t_now,
        db_path=outbox_app.config["DATABASE_PATH"]
    )
    assert result["sync_status"] == "failed"
    assert result["retry_count"] == 1
    assert result["delay_seconds"] == 15.0
    assert result["next_retry_at"] == "2026-11-08T10:00:15Z"


def test_15_exponential_backoff_respects_maximum_delay():
    """Scenario 15: calculate_retry_delay implements exponential scaling capped at max_delay with ±10% jitter and absolute 900s cap."""
    # Attempt 0: 15 * 2^0 = 15s
    d0 = calculate_retry_delay(retry_count=0, base_delay=15, max_delay=900, jitter=False)
    assert d0 == 15.0

    # Attempt 1: 15 * 2^1 = 30s
    d1 = calculate_retry_delay(retry_count=1, base_delay=15, max_delay=900, jitter=False)
    assert d1 == 30.0

    # Attempt 2: 15 * 2^2 = 60s
    d2 = calculate_retry_delay(retry_count=2, base_delay=15, max_delay=900, jitter=False)
    assert d2 == 60.0

    # Attempt 3: 15 * 2^3 = 120s
    d3 = calculate_retry_delay(retry_count=3, base_delay=15, max_delay=900, jitter=False)
    assert d3 == 120.0

    # Attempt 6: 15 * 2^6 = 960s -> capped at 900s (15 minutes)
    d6 = calculate_retry_delay(retry_count=6, base_delay=15, max_delay=900, jitter=False)
    assert d6 == 900.0

    # Injected deterministic positive and negative jitter for sub-cap attempts
    d_pos_jitter = calculate_retry_delay(retry_count=1, base_delay=15, max_delay=900, jitter=True, jitter_seconds=3.5)
    assert d_pos_jitter == 33.5

    d_neg_jitter = calculate_retry_delay(retry_count=1, base_delay=15, max_delay=900, jitter=True, jitter_seconds=-2.0)
    assert d_neg_jitter == 28.0

    # Verify ±10% random jitter distribution bounds
    for _ in range(50):
        # For attempt 0 (15s): 15 ± 10% = [13.5s, 16.5s]
        d_rand_0 = calculate_retry_delay(retry_count=0, base_delay=15, max_delay=900, jitter=True)
        assert 13.5 <= d_rand_0 <= 16.5

        # For attempt 1 (30s): 30 ± 10% = [27.0s, 33.0s]
        d_rand_1 = calculate_retry_delay(retry_count=1, base_delay=15, max_delay=900, jitter=True)
        assert 27.0 <= d_rand_1 <= 33.0

        # For attempt 6 (capped 900s): with absolute 900s ceiling, range is strictly [810.0s, 900.0s]
        d_rand_6 = calculate_retry_delay(retry_count=6, base_delay=15, max_delay=900, jitter=True)
        assert 810.0 <= d_rand_6 <= 900.0
        assert d_rand_6 <= 900.0


def test_21_retry_delay_absolute_cap_never_exceeds_900_seconds():
    """Scenario 21: Verify that positive jitter and high retry counts never exceed absolute 900-second maximum."""
    # 1. Deterministic positive jitter at maximum ceiling cannot exceed 900 seconds
    d_pos_cap = calculate_retry_delay(retry_count=6, base_delay=15, max_delay=900, jitter=True, jitter_seconds=50.0)
    assert d_pos_cap == 900.0

    d_pos_cap_huge = calculate_retry_delay(retry_count=10, base_delay=15, max_delay=900, jitter=True, jitter_seconds=500.0)
    assert d_pos_cap_huge == 900.0

    # 2. Deterministic negative jitter at ceiling remains within [810.0s, 900.0s]
    d_neg_cap = calculate_retry_delay(retry_count=6, base_delay=15, max_delay=900, jitter=True, jitter_seconds=-50.0)
    assert d_neg_cap == 850.0

    d_neg_cap_min = calculate_retry_delay(retry_count=6, base_delay=15, max_delay=900, jitter=True, jitter_seconds=-90.0)
    assert d_neg_cap_min == 810.0

    # 3. Repeated randomized calculations (100 iterations) never exceed 900 seconds and stay >= 810.0 seconds
    for _ in range(100):
        for retry in (6, 7, 8, 12, 20):
            d_rand = calculate_retry_delay(retry_count=retry, base_delay=15, max_delay=900, jitter=True)
            assert 810.0 <= d_rand <= 900.0
            assert d_rand <= 900.0


def test_16_maximum_attempts_lead_to_dead_letter(outbox_app, outbox_client):
    """Scenario 16: When retry count reaches max_retries, record moves to 'dead_letter'."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    claimed = claim_records(limit=1, db_path=outbox_app.config["DATABASE_PATH"])
    queue_id = claimed[0]["queue_id"]

    # Execute retries up to max_retries = 3
    schedule_retry(queue_id=queue_id, error_message="Err 1", max_retries=3, db_path=outbox_app.config["DATABASE_PATH"])
    schedule_retry(queue_id=queue_id, error_message="Err 2", max_retries=3, db_path=outbox_app.config["DATABASE_PATH"])
    res3 = schedule_retry(queue_id=queue_id, error_message="Err 3", max_retries=3, db_path=outbox_app.config["DATABASE_PATH"])

    assert res3["sync_status"] == "dead_letter"
    assert res3["max_retries_exceeded"] is True
    assert res3["next_retry_at"] is None

    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["dead_letter"] == 1
    assert stats["failed"] == 0


def test_17_queue_statistics_are_accurate(outbox_app, outbox_client):
    """Scenario 17: get_queue_stats accurately aggregates counts across all states."""
    seed_test_student_and_session(outbox_app)
    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()

    # Create dummy attendance records to attach queue rows
    for i in range(1, 6):
        cursor.execute(
            f"""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('CS-STAT-{i}', 'Student {i}', 'UID_STAT_{i}', 1, 'now');
            """
        )
        s_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (?, 1, 'now', 'Present', 'test');
            """,
            (s_id,)
        )
        att_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO sheets_sync_queue (attendance_id, sync_status, retry_count, created_at, updated_at)
            VALUES (?, 'pending', 0, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z');
            """,
            (att_id,)
        )
    conn.commit()

    # Transition rows into various states
    cursor.execute("UPDATE sheets_sync_queue SET sync_status = 'synced' WHERE id = 1;")
    cursor.execute("UPDATE sheets_sync_queue SET sync_status = 'in_progress' WHERE id = 2;")
    cursor.execute("UPDATE sheets_sync_queue SET sync_status = 'failed' WHERE id = 3;")
    cursor.execute("UPDATE sheets_sync_queue SET sync_status = 'dead_letter' WHERE id = 4;")
    # id = 5 remains 'pending'
    conn.commit()
    conn.close()

    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["total"] == 5
    assert stats["synced"] == 1
    assert stats["in_progress"] == 1
    assert stats["failed"] == 1
    assert stats["dead_letter"] == 1
    assert stats["pending"] == 1


def test_18_deleting_attendance_cascades_to_queue_record(outbox_app, outbox_client):
    """Scenario 18: Foreign key cascade ensures deleting an attendance record deletes its queue entry."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    res = outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})
    att_id = res.get_json()["attendance"]["id"]

    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sheets_sync_queue WHERE attendance_id = ?;", (att_id,))
    assert cursor.fetchone()[0] == 1

    # Delete attendance record with foreign keys enforced
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("DELETE FROM attendance WHERE id = ?;", (att_id,))
    conn.commit()

    # Queue record must be automatically cascaded
    cursor.execute("SELECT COUNT(*) FROM sheets_sync_queue WHERE attendance_id = ?;", (att_id,))
    assert cursor.fetchone()[0] == 0
    conn.close()


def test_19_database_integrity_remains_valid(outbox_app, outbox_client):
    """Scenario 19: PRAGMA integrity_check returns 'ok' after various outbox lifecycle transitions."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    claimed = claim_records(limit=1, db_path=outbox_app.config["DATABASE_PATH"])
    mark_as_synced(claimed[0]["queue_id"], db_path=outbox_app.config["DATABASE_PATH"])

    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check;")
    assert cursor.fetchone()[0] == "ok"
    cursor.execute("PRAGMA foreign_key_check;")
    assert len(cursor.fetchall()) == 0
    conn.close()


def test_20_existing_clear_all_test_data_functionality_remains_compatible(outbox_app, outbox_client):
    """Scenario 20: Settings 'Clear All Test Data' feature successfully purges queue along with attendance."""
    seed_test_student_and_session(outbox_app)
    authenticate_client(outbox_client)
    outbox_client.post("/api/scans", json={"card_uid": "AA11BB22", "source": "rfid"})

    # Clear test data using existing service
    clear_res = clear_all_test_data(
        db_path=outbox_app.config["DATABASE_PATH"],
        backup_dir=outbox_app.config["BACKUP_DIR"]
    )
    assert clear_res["success"] is True

    # Verify queue is empty
    stats = get_queue_stats(outbox_app.config["DATABASE_PATH"])
    assert stats["total"] == 0

    # Verify admin preserved
    conn = get_db_connection(outbox_app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM admin_users;")
    assert cursor.fetchone()[0] == 1
    cursor.execute("PRAGMA integrity_check;")
    assert cursor.fetchone()[0] == "ok"
    conn.close()
