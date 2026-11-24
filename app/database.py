"""
Database connection and initialization module for SQLite.
Manages connections, enables foreign keys, sets row factories, and provisions schema.
Supports standalone usage and Flask application context lifecycle (g.db).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
from app.config import get_config
from app.models import (
    CREATE_TABLES_SQL,
    CREATE_INDEXES_SQL,
    CREATE_SHEETS_SYNC_QUEUE_SQL,
    CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL,
    CREATE_SHEETS_SYNC_META_SQL,
)


def get_db_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    Creates and returns a connection to the SQLite database.
    
    1. Connects to the database file (resolves path if not provided).
    2. Enables SQLite foreign key enforcement (PRAGMA foreign_keys = ON).
    3. Sets row_factory to sqlite3.Row so rows can be accessed like dictionaries.
    
    :param db_path: Optional path to the database file. If None, uses configured path.
    :return: sqlite3.Connection object
    """
    if db_path is None:
        db_path = get_config().DATABASE_PATH
    
    path_obj = Path(db_path)
    # Ensure database parent directory exists
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        conn = sqlite3.connect(str(path_obj))
        # Crucial for SQLite: Foreign key enforcement is OFF by default and must be enabled per connection!
        conn.execute("PRAGMA foreign_keys = ON;")
        # Allow accessing columns by name (e.g., row['full_name'])
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to connect to SQLite database at {path_obj}: {e}") from e


def close_db_connection(conn: Optional[sqlite3.Connection]) -> None:
    """
    Safely closes a database connection if it is open.
    """
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def get_db(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    Returns a database connection for the current Flask request context (stored in g.db).
    If called outside of a Flask application context, returns a direct database connection.
    """
    try:
        from flask import g, current_app, has_app_context
        if has_app_context():
            if "db" not in g:
                target_path = db_path or current_app.config.get("DATABASE_PATH")
                g.db = get_db_connection(target_path)
            return g.db
    except ImportError:
        pass
    
    return get_db_connection(db_path)


def close_db(e=None) -> None:
    """
    Closes the request-level database connection during Flask context teardown.
    """
    try:
        from flask import g
        db = g.pop("db", None)
        if db is not None:
            close_db_connection(db)
    except (ImportError, RuntimeError):
        pass


def backfill_historical_rows(conn: sqlite3.Connection) -> dict:
    """
    Safely backfills historical spreadsheet row numbers for verified baseline synthetic records:
    - Attendance ID 1 (MANUAL-001) -> Row 3
    - Attendance ID 2 (MANUAL-002) -> Row 4
    - sheets_sync_meta.next_sheet_row -> at least 5

    Safety Rules:
    1. Verify target attendance ID exists and matches expected roll number.
    2. Detect pre-existing conflicting row assignments.
    3. Detect duplicate assignment attempts.
    4. Do not overwrite a conflicting non-NULL assignment.
    5. Preserves existing attendance rows and queue statuses.
    """
    cursor = conn.cursor()
    verified_mappings = [
        (1, "MANUAL-001", 3),
        (2, "MANUAL-002", 4),
    ]

    results = {"backfilled": [], "already_assigned": [], "skipped": [], "conflicts": []}
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for att_id, expected_roll, target_row in verified_mappings:
        # 1. Verify attendance record exists and roll number matches
        cursor.execute(
            """
            SELECT a.id, s.roll_number
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            WHERE a.id = ?;
            """,
            (att_id,)
        )
        att_row = cursor.fetchone()
        if not att_row:
            results["skipped"].append((att_id, "attendance_id_not_found"))
            continue

        roll_num = att_row["roll_number"] if hasattr(att_row, "keys") else att_row[1]
        if roll_num != expected_roll:
            results["skipped"].append((att_id, f"roll_number_mismatch_{roll_num}_vs_{expected_roll}"))
            continue

        # 2. Check sheets_sync_queue for this attendance_id
        cursor.execute(
            "SELECT id, sheet_row_number FROM sheets_sync_queue WHERE attendance_id = ?;",
            (att_id,)
        )
        q_row = cursor.fetchone()
        if not q_row:
            results["skipped"].append((att_id, "queue_record_not_found"))
            continue

        current_row = q_row["sheet_row_number"] if hasattr(q_row, "keys") else q_row[1]
        q_id = q_row["id"] if hasattr(q_row, "keys") else q_row[0]

        if current_row == target_row:
            results["already_assigned"].append((att_id, target_row))
            continue
        elif current_row is not None and current_row != target_row:
            conflict_msg = f"Queue ID {q_id} (attendance_id {att_id}) already assigned row {current_row}, conflicting with target {target_row}"
            results["conflicts"].append((att_id, conflict_msg))
            raise RuntimeError(f"Historical backfill conflict: {conflict_msg}")

        # 3. Check if target_row is already assigned to a DIFFERENT queue record
        cursor.execute(
            "SELECT id, attendance_id FROM sheets_sync_queue WHERE sheet_row_number = ?;",
            (target_row,)
        )
        owner_row = cursor.fetchone()
        if owner_row:
            other_att = owner_row["attendance_id"] if hasattr(owner_row, "keys") else owner_row[1]
            conflict_msg = f"Target row {target_row} already claimed by attendance_id {other_att} (expected {att_id})"
            results["conflicts"].append((att_id, conflict_msg))
            raise RuntimeError(f"Historical backfill conflict: {conflict_msg}")

        # 4. Safe update
        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sheet_row_number = ?, updated_at = ?
            WHERE attendance_id = ? AND sheet_row_number IS NULL;
            """,
            (target_row, now_utc, att_id)
        )
        results["backfilled"].append((att_id, target_row))

    # 5. Ensure sheets_sync_meta next_sheet_row is at least 5
    cursor.execute("SELECT next_sheet_row FROM sheets_sync_meta WHERE id = 1;")
    meta_row = cursor.fetchone()
    if meta_row:
        curr_next = meta_row["next_sheet_row"] if hasattr(meta_row, "keys") else meta_row[0]
        if curr_next < 5:
            cursor.execute(
                "UPDATE sheets_sync_meta SET next_sheet_row = 5, updated_at = ? WHERE id = 1;",
                (now_utc,)
            )
    else:
        cursor.execute(
            """
            INSERT OR IGNORE INTO sheets_sync_meta (id, next_sheet_row, created_at, updated_at)
            VALUES (1, 5, ?, ?);
            """,
            (now_utc, now_utc)
        )

    conn.commit()
    return results


def migrate_db(conn: sqlite3.Connection) -> None:
    """
    Safely applies schema migrations for existing databases.
    Idempotent: verifies schema column existence before altering tables.
    """
    cursor = conn.cursor()
    try:
        # 1. Forward migration: end_time column in class_sessions
        cursor.execute("PRAGMA table_info(class_sessions);")
        columns = [row["name"] if hasattr(row, "keys") else row[1] for row in cursor.fetchall()]
        if columns and "end_time" not in columns:
            cursor.execute("ALTER TABLE class_sessions ADD COLUMN end_time TEXT;")
            conn.commit()

        # 2. Forward migration: sheets_sync_queue table & indexes (Phase 3 Milestone 2)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_queue';")
        if not cursor.fetchone():
            cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_SQL)
            cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL)
            conn.commit()

        # 3. Forward migration: sheets_sync_queue columns (Phase 3 Milestone 5 - Stage 9.7B.13F)
        cursor.execute("PRAGMA table_info(sheets_sync_queue);")
        queue_columns = [row["name"] if hasattr(row, "keys") else row[1] for row in cursor.fetchall()]
        if queue_columns:
            if "sheet_row_number" not in queue_columns:
                cursor.execute("ALTER TABLE sheets_sync_queue ADD COLUMN sheet_row_number INTEGER;")
            if "claim_token" not in queue_columns:
                cursor.execute("ALTER TABLE sheets_sync_queue ADD COLUMN claim_token INTEGER NOT NULL DEFAULT 0;")

            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_sheets_sync_queue_sheet_row_number
                ON sheets_sync_queue(sheet_row_number)
                WHERE sheet_row_number IS NOT NULL;
                """
            )
            conn.commit()

        # 4. Forward migration: sheets_sync_meta table (Phase 3 Milestone 5 - Stage 9.7B.13F)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_meta';")
        if not cursor.fetchone():
            cursor.execute(CREATE_SHEETS_SYNC_META_SQL)
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cursor.execute(
                """
                INSERT OR IGNORE INTO sheets_sync_meta (id, next_sheet_row, created_at, updated_at)
                VALUES (1, 5, ?, ?);
                """,
                (now_utc, now_utc)
            )
            conn.commit()
        else:
            # Table exists; ensure singleton row 1 exists
            cursor.execute("SELECT id FROM sheets_sync_meta WHERE id = 1;")
            if not cursor.fetchone():
                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO sheets_sync_meta (id, next_sheet_row, created_at, updated_at)
                    VALUES (1, 5, ?, ?);
                    """,
                    (now_utc, now_utc)
                )
                conn.commit()

        # 5. Historical row backfill for verified baseline records
        backfill_historical_rows(conn)

    except sqlite3.Error as e:
        conn.rollback()
        raise RuntimeError(f"Database migration failed: {e}") from e


def init_db(db_path: Optional[Union[str, Path]] = None) -> None:
    """
    Initializes the database schema:
    1. Ensures the target database directory exists.
    2. Creates the students, class_sessions, and attendance tables if they do not exist.
    3. Creates all performance indexes.
    4. Applies schema migrations (such as end_time column).
    5. Idempotent: Can be run multiple times safely without altering existing records.
    
    :param db_path: Optional path to the database. If None, uses configured path.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        # 1. Execute table creation scripts (using executescript for multiple statements)
        cursor.executescript(CREATE_TABLES_SQL)
        conn.commit()
        # 2. Apply forward schema migrations (ensuring all new columns and tables exist)
        migrate_db(conn)
        # 3. Execute index creation scripts
        cursor.executescript(CREATE_INDEXES_SQL)
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise RuntimeError(f"Database initialization failed: {e}") from e
    finally:
        close_db_connection(conn)


if __name__ == "__main__":
    # If run directly: initialize the development database
    dev_path = get_config("development").DATABASE_PATH
    print(f"Initializing database at: {dev_path}")
    init_db(dev_path)
    print("Database initialization successful.")
