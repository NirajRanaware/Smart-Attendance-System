"""
Database Backup and Maintenance Module for Smart Attendance Management System.
Provides safe, transaction-consistent SQLite online backups, integrity verification,
live database statistics, and safe clearing of operational test data.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
from flask import current_app, has_app_context
from app.config import BASE_DIR, get_config


def get_default_backup_dir() -> Path:
    """
    Returns the designated backup directory.
    Uses app.config['BACKUP_DIR'] if inside Flask application context,
    otherwise defaults to BASE_DIR / 'backups'.
    """
    if has_app_context():
        cfg_backup = current_app.config.get("BACKUP_DIR")
        if cfg_backup:
            return Path(cfg_backup)
    return BASE_DIR / "backups"


def get_database_stats(db_path: Optional[Union[str, Path]] = None) -> dict:
    """
    Retrieves live, non-hardcoded database statistics for registered students,
    active students, class sessions, attendance records, and administrator accounts.
    
    :param db_path: Path to SQLite database. If None, uses configured path.
    :return: Dictionary containing live counts and safe database file location.
    """
    if db_path is None:
        if has_app_context():
            db_path = current_app.config.get("DATABASE_PATH")
        else:
            db_path = get_config().DATABASE_PATH

    path_obj = Path(db_path).resolve()
    if not path_obj.exists():
        return {
            "total_students": 0,
            "active_students": 0,
            "total_sessions": 0,
            "total_attendance": 0,
            "total_admins": 0,
            "database_path": str(path_obj.name),
            "database_size_bytes": 0,
            "exists": False,
        }

    conn = sqlite3.connect(str(path_obj))
    try:
        cursor = conn.cursor()
        
        # Verify tables exist before querying
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}

        total_students = 0
        active_students = 0
        if "students" in tables:
            cursor.execute("SELECT COUNT(*) FROM students;")
            total_students = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM students WHERE active = 1;")
            active_students = cursor.fetchone()[0]

        total_sessions = 0
        if "class_sessions" in tables:
            cursor.execute("SELECT COUNT(*) FROM class_sessions;")
            total_sessions = cursor.fetchone()[0]

        total_attendance = 0
        if "attendance" in tables:
            cursor.execute("SELECT COUNT(*) FROM attendance;")
            total_attendance = cursor.fetchone()[0]

        total_admins = 0
        if "admin_users" in tables:
            cursor.execute("SELECT COUNT(*) FROM admin_users;")
            total_admins = cursor.fetchone()[0]

        # Present a clean, safe relative path if inside BASE_DIR
        try:
            display_path = str(path_obj.relative_to(BASE_DIR))
        except ValueError:
            display_path = path_obj.name

        file_size = path_obj.stat().st_size

        return {
            "total_students": total_students,
            "active_students": active_students,
            "total_sessions": total_sessions,
            "total_attendance": total_attendance,
            "total_admins": total_admins,
            "database_path": display_path,
            "database_size_bytes": file_size,
            "exists": True,
        }
    finally:
        conn.close()


def create_database_backup(
    db_path: Optional[Union[str, Path]] = None,
    backup_dir: Optional[Union[str, Path]] = None,
) -> dict:
    """
    Creates a timestamped, ACID-safe backup of the active SQLite database using
    SQLite's online backup API (source_conn.backup(target_conn)).
    
    Guarantees:
    - Never overwrites an earlier backup (adds counter if collision occurs).
    - Verifies the backup exists and can be opened as a valid SQLite database.
    - Verifies 'PRAGMA integrity_check' passes on the backup file.
    - Verifies table record counts match original database records.
    - If creation or verification fails, aborts, removes invalid backup, and raises RuntimeError.
    
    :param db_path: Path to source SQLite database.
    :param backup_dir: Destination directory for backup files.
    :return: dict with backup metadata (filename, path, size, timestamp, record_counts).
    """
    if db_path is None:
        if has_app_context():
            db_path = current_app.config.get("DATABASE_PATH")
        else:
            db_path = get_config().DATABASE_PATH

    src_path = Path(db_path).resolve()
    if not src_path.exists():
        raise RuntimeError(f"Source database file not found at: {src_path}")

    if backup_dir is None:
        backup_dir = get_default_backup_dir()

    dest_dir = Path(backup_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Generate collision-safe timestamped filename
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    base_filename = f"attendance_backup_{timestamp_str}.db"
    target_path = dest_dir / base_filename

    # Prevent accidental overwrites if multiple backups are run within the same second
    counter = 1
    while target_path.exists():
        target_path = dest_dir / f"attendance_backup_{timestamp_str}_{counter}.db"
        counter += 1

    # Retrieve source row counts for verification
    source_stats = get_database_stats(src_path)

    # Perform safe SQLite online backup
    source_conn = sqlite3.connect(str(src_path))
    target_conn = sqlite3.connect(str(target_path))
    try:
        source_conn.backup(target_conn)
    except Exception as e:
        target_conn.close()
        source_conn.close()
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"SQLite online backup failed during copy: {e}") from e
    finally:
        target_conn.close()
        source_conn.close()

    # Mandatory Post-Backup Verification
    try:
        # 1. Verify file exists and has non-zero size
        if not target_path.exists() or target_path.stat().st_size == 0:
            raise RuntimeError("Backup file does not exist or is empty.")

        # 2. Verify SQLite database integrity
        verify_conn = sqlite3.connect(str(target_path))
        try:
            cursor = verify_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            row = cursor.fetchone()
            if not row or row[0] != "ok":
                raise RuntimeError(f"Backup failed SQLite integrity check: {row}")

            # 3. Verify record counts match the source database
            backup_stats = get_database_stats(target_path)
            for key in ("total_students", "active_students", "total_sessions", "total_attendance", "total_admins"):
                if backup_stats[key] != source_stats[key]:
                    raise RuntimeError(
                        f"Backup verification mismatch for '{key}': "
                        f"source={source_stats[key]}, backup={backup_stats[key]}"
                    )
        finally:
            verify_conn.close()

    except Exception as verify_err:
        # Clean up corrupted or invalid backup file
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"Backup verification failed: {verify_err}") from verify_err

    # Compute display path
    try:
        rel_path = str(target_path.relative_to(BASE_DIR))
    except ValueError:
        rel_path = str(target_path)

    return {
        "success": True,
        "filename": target_path.name,
        "path": rel_path,
        "absolute_path": str(target_path),
        "size_bytes": target_path.stat().st_size,
        "timestamp": now.isoformat(),
        "verified": True,
        "record_counts": {
            "students": source_stats["total_students"],
            "sessions": source_stats["total_sessions"],
            "attendance": source_stats["total_attendance"],
            "admins": source_stats["total_admins"],
        },
    }


def clear_all_test_data(
    db_path: Optional[Union[str, Path]] = None,
    backup_dir: Optional[Union[str, Path]] = None,
) -> dict:
    """
    Safely clears operational test data (attendance, class_sessions, students)
    after first creating and verifying a complete database backup.
    
    Guarantees:
    - Creates and verifies a timestamped SQLite backup first.
    - If backup creation or verification fails, aborts completely without deleting any data.
    - Deletes ONLY: attendance, class_sessions, students.
    - Strictly PRESERVES: admin_users accounts, roles, password hashes.
    - Strictly PRESERVES: database schema, tables, indexes, and single-active-session constraint.
    - Executes in an immediate database transaction (BEGIN IMMEDIATE) with foreign keys enabled.
    
    :param db_path: Path to database file.
    :param backup_dir: Path to directory where backups are stored.
    :return: dict with operation result, backup metadata, and cleared counts.
    """
    if db_path is None:
        if has_app_context():
            db_path = current_app.config.get("DATABASE_PATH")
        else:
            db_path = get_config().DATABASE_PATH

    path_obj = Path(db_path).resolve()
    if not path_obj.exists():
        raise RuntimeError(f"Database file not found at: {path_obj}")

    # Step 1: Pre-deletion stats
    pre_stats = get_database_stats(path_obj)

    # Step 2: MANDATORY safe backup and verification BEFORE deleting anything
    backup_result = create_database_backup(path_obj, backup_dir=backup_dir)
    if not backup_result or not backup_result.get("verified"):
        raise RuntimeError("Automatic pre-deletion database backup could not be verified. Deletion aborted.")

    # Step 3: Execute safe deletion transaction
    conn = sqlite3.connect(str(path_obj))
    try:
        # Enforce foreign key constraints
        conn.execute("PRAGMA foreign_keys = ON;")
        
        # Acquire immediate write lock for atomic deletion
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.cursor()

        # Delete dependent tables in order (or cascading via foreign keys)
        cursor.execute("DELETE FROM attendance;")
        attendance_cleared = cursor.rowcount

        cursor.execute("DELETE FROM class_sessions;")
        sessions_cleared = cursor.rowcount

        cursor.execute("DELETE FROM students;")
        students_cleared = cursor.rowcount

        # Strictly verify admin_users remains intact
        cursor.execute("SELECT COUNT(*) FROM admin_users;")
        post_admin_count = cursor.fetchone()[0]
        if pre_stats["total_admins"] > 0 and post_admin_count == 0:
            conn.rollback()
            raise RuntimeError("CRITICAL ERROR: Administrator accounts were affected! Transaction rolled back.")

        conn.commit()

    except Exception as delete_err:
        conn.rollback()
        raise RuntimeError(f"Database deletion transaction failed: {delete_err}") from delete_err
    finally:
        conn.close()

    # Step 4: Verify post-deletion database integrity
    chk_conn = sqlite3.connect(str(path_obj))
    try:
        chk_cursor = chk_conn.cursor()
        chk_cursor.execute("PRAGMA integrity_check;")
        chk_row = chk_cursor.fetchone()
        if not chk_row or chk_row[0] != "ok":
            raise RuntimeError(f"Database integrity check failed post-clearing: {chk_row}")
    finally:
        chk_conn.close()

    return {
        "success": True,
        "message": "All operational test data cleared successfully. Database backup verified and saved.",
        "backup": {
            "filename": backup_result["filename"],
            "path": backup_result["path"],
            "timestamp": backup_result["timestamp"],
            "size_bytes": backup_result["size_bytes"],
        },
        "cleared_counts": {
            "students": pre_stats["total_students"],
            "sessions": pre_stats["total_sessions"],
            "attendance": pre_stats["total_attendance"],
        },
        "preserved": {
            "admin_accounts": pre_stats["total_admins"],
            "schema_intact": True,
        },
    }
