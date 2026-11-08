"""
Database connection and initialization module for SQLite.
Manages connections, enables foreign keys, sets row factories, and provisions schema.
Supports standalone usage and Flask application context lifecycle (g.db).
"""

import sqlite3
from pathlib import Path
from typing import Optional, Union
from app.config import get_config
from app.models import (
    CREATE_TABLES_SQL,
    CREATE_INDEXES_SQL,
    CREATE_SHEETS_SYNC_QUEUE_SQL,
    CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL,
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
        # Execute table creation scripts (using executescript for multiple statements)
        cursor.executescript(CREATE_TABLES_SQL)
        # Execute index creation scripts
        cursor.executescript(CREATE_INDEXES_SQL)
        conn.commit()
        # Apply forward schema migrations
        migrate_db(conn)
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
