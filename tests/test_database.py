"""
Automated unit tests for SQLite database design and constraints.
Runs on an isolated temporary database to prevent altering development data.
"""

import sqlite3
import pytest
from app.database import get_db_connection, init_db, close_db_connection


@pytest.fixture
def test_db(tmp_path):
    """
    Pytest fixture that creates a fresh, isolated SQLite database
    in a temporary directory for each test function.
    Guarantees no interference with development data.
    """
    test_db_path = tmp_path / "test_attendance.db"
    init_db(test_db_path)
    conn = get_db_connection(test_db_path)
    yield conn, test_db_path
    close_db_connection(conn)


def test_1_database_initializes_successfully(test_db):
    """TEST 1: Verify the database initializes successfully without errors."""
    conn, db_path = test_db
    assert db_path.exists(), "Database file should exist after initialization."
    assert db_path.stat().st_size > 0, "Database file should not be empty."


def test_2_all_three_tables_are_created(test_db):
    """TEST 2: Verify all three tables (students, class_sessions, attendance) exist."""
    conn, _ = test_db
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name NOT LIKE 'sqlite_%';
    """)
    tables = {row["name"] for row in cursor.fetchall()}
    assert "students" in tables, "Table 'students' must exist."
    assert "class_sessions" in tables, "Table 'class_sessions' must exist."
    assert "attendance" in tables, "Table 'attendance' must exist."


def test_3_duplicate_roll_number_rejected(test_db):
    """TEST 3: Verify that duplicate student roll numbers are rejected by UNIQUE constraint."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert first student
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'UID_101', 1, '2026-11-07 10:00:00');
    """)
    conn.commit()
    
    # Attempt inserting second student with the same roll number
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('101', 'Another Name', 'UID_999', 1, '2026-11-07 10:01:00');
        """)
        conn.commit()


def test_4_duplicate_rfid_uid_rejected(test_db):
    """TEST 4: Verify that duplicate RFID card UIDs are rejected by UNIQUE constraint."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert first student
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_ABC_123', 1, '2026-11-07 10:00:00');
    """)
    conn.commit()
    
    # Attempt inserting second student with different roll number but same card_uid
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('102', 'Priya Patel', 'CARD_ABC_123', 1, '2026-11-07 10:01:00');
        """)
        conn.commit()


def test_5_attendance_references_valid_student_and_session(test_db):
    """TEST 5: Verify valid attendance insertion referencing student and session."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert student
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_101', 1, '2026-11-07 10:00:00');
    """)
    student_id = cursor.lastrowid
    
    # Insert class session
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Computer Engineering', '2026-11-07', '10:00', 1, '2026-11-07 10:00:00');
    """)
    session_id = cursor.lastrowid
    
    # Insert attendance
    cursor.execute("""
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, '2026-11-07 10:05:00', 'Present', 'simulated_rfid');
    """, (student_id, session_id))
    conn.commit()
    
    cursor.execute("SELECT * FROM attendance WHERE student_id = ? AND session_id = ?;", (student_id, session_id))
    record = cursor.fetchone()
    assert record is not None
    assert record["status"] == "Present"
    assert record["source"] == "simulated_rfid"


def test_6_invalid_foreign_key_references_rejected(test_db):
    """TEST 6: Verify foreign-key enforcement rejects attendance for non-existent students/sessions."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Try inserting attendance for non-existent student (id: 9999) and session (id: 9999)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (9999, 9999, '2026-11-07 10:05:00', 'Present', 'simulated_rfid');
        """)
        conn.commit()


def test_7_duplicate_attendance_rejected(test_db):
    """TEST 7: Verify UNIQUE(student_id, session_id) constraint prevents duplicate scans."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert student and session
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_101', 1, '2026-11-07 10:00:00');
    """)
    student_id = cursor.lastrowid
    
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Computer Engineering', '2026-11-07', '10:00', 1, '2026-11-07 10:00:00');
    """)
    session_id = cursor.lastrowid
    
    # First attendance scan -> succeeds
    cursor.execute("""
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, '2026-11-07 10:05:00', 'Present', 'simulated_rfid');
    """, (student_id, session_id))
    conn.commit()
    
    # Second attendance scan for the exact same session -> must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (?, ?, '2026-11-07 10:06:00', 'Present', 'simulated_rfid');
        """, (student_id, session_id))
        conn.commit()


def test_8_student_can_attend_different_sessions(test_db):
    """TEST 8: Verify a student can attend multiple distinct class sessions."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert student
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_101', 1, '2026-11-07 10:00:00');
    """)
    student_id = cursor.lastrowid
    
    # Insert Session 1 (Morning)
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Computer Networks', '2026-11-07', '09:00', 0, '2026-11-07 09:00:00');
    """)
    session_1 = cursor.lastrowid
    
    # Insert Session 2 (Afternoon)
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Database Management', '2026-11-07', '14:00', 1, '2026-11-07 14:00:00');
    """)
    session_2 = cursor.lastrowid
    
    # Mark attendance for both sessions
    cursor.execute("""
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, '2026-11-07 09:05:00', 'Present', 'simulated_rfid');
    """, (student_id, session_1))
    
    cursor.execute("""
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, '2026-11-07 14:05:00', 'Present', 'simulated_rfid');
    """, (student_id, session_2))
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) AS total FROM attendance WHERE student_id = ?;", (student_id,))
    assert cursor.fetchone()["total"] == 2


def test_9_reinitialization_preserves_existing_data(test_db):
    """TEST 9: Verify re-running init_db does not delete or overwrite existing records."""
    conn, db_path = test_db
    cursor = conn.cursor()
    
    # Insert a record
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_101', 1, '2026-11-07 10:00:00');
    """)
    conn.commit()
    
    # Re-run init_db on the exact same database
    init_db(db_path)
    
    # Check that the student record still exists unharmed
    cursor.execute("SELECT * FROM students WHERE roll_number = '101';")
    row = cursor.fetchone()
    assert row is not None
    assert row["full_name"] == "Rahul Sharma"


def test_10_single_active_session_restriction_at_database_level(test_db):
    """TEST 10: Verify the partial unique index idx_single_active_session prevents two active sessions."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert first active session
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Course A', '2026-11-07', '10:00', 1, '2026-11-07 10:00:00');
    """)
    conn.commit()
    
    # Attempt inserting second active session -> must raise IntegrityError due to partial unique index
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
            VALUES ('Course B', '2026-11-07', '11:00', 1, '2026-11-07 11:00:00');
        """)
        conn.commit()


def test_11_transaction_rollback_after_database_error(test_db):
    """TEST 11: Verify transaction rollback properly discards intermediate uncommitted changes."""
    conn, _ = test_db
    cursor = conn.cursor()
    
    # Insert initial student
    cursor.execute("""
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES ('101', 'Rahul Sharma', 'CARD_101', 1, '2026-11-07 10:00:00');
    """)
    conn.commit()
    
    try:
        cursor.execute("BEGIN IMMEDIATE;")
        # Valid insert inside transaction
        cursor.execute("""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('102', 'Priya Patel', 'CARD_102', 1, '2026-11-07 10:01:00');
        """)
        # Conflicting insert that causes failure
        cursor.execute("""
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES ('101', 'Conflict Student', 'CARD_DIFF', 1, '2026-11-07 10:02:00');
        """)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
    
    # Verify Priya Patel was NOT committed due to rollback
    cursor.execute("SELECT * FROM students WHERE roll_number = '102';")
    assert cursor.fetchone() is None
    
    # Verify original student remains intact
    cursor.execute("SELECT COUNT(*) AS total FROM students;")
    assert cursor.fetchone()["total"] == 1


def test_12_development_and_test_database_separation():
    """TEST 12: Verify development and testing configurations point to different database paths."""
    from app.config import DevelopmentConfig, TestingConfig
    assert DevelopmentConfig.DATABASE_PATH != TestingConfig.DATABASE_PATH
    assert "test" in str(TestingConfig.DATABASE_PATH).lower()
    assert str(DevelopmentConfig.DATABASE_PATH).endswith("attendance.db")


def test_13_connection_closing_behavior(tmp_path):
    """TEST 13: Verify close_db_connection closes connection and subsequent calls raise ProgrammingError."""
    db_path = tmp_path / "test_close.db"
    init_db(db_path)
    conn = get_db_connection(db_path)
    close_db_connection(conn)
    
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1;")


def test_14_database_utilities_and_edge_cases(tmp_path):
    """TEST 14: Verify get_db outside app context, close_db, and close_db_connection(None)."""
    from app.database import get_db, close_db
    
    # 1. close_db_connection(None) should not fail
    close_db_connection(None)
    
    # 2. close_db() outside app context should not fail
    close_db()
    
    # 3. get_db() outside app context returns a standalone connection
    db_path = tmp_path / "standalone.db"
    init_db(db_path)
    conn = get_db(db_path)
    assert conn is not None
    res = conn.execute("SELECT 1;").fetchone()[0]
    assert res == 1
    close_db_connection(conn)


def test_15_init_db_error_handling(tmp_path, monkeypatch):
    """TEST 15: Verify init_db handles sqlite3.Error, rolls back, and raises RuntimeError."""
    db_path = tmp_path / "fail_init.db"
    monkeypatch.setattr("app.database.CREATE_TABLES_SQL", "SYNTAX ERROR INVALID SQL;")
    
    with pytest.raises(RuntimeError) as exc_info:
        init_db(db_path)
    assert "Database initialization failed" in str(exc_info.value)


def test_16_inspect_database_script(tmp_path, monkeypatch):
    """TEST 16: Verify scripts/check_database.py inspect_database passes all schema checks."""
    from scripts.check_database import inspect_database
    test_db = tmp_path / "inspect_test.db"
    init_db(test_db)
    
    class DummyConfig:
        DATABASE_PATH = test_db
        
    monkeypatch.setattr("scripts.check_database.get_config", lambda env: DummyConfig)
    result = inspect_database()
    assert result is True


