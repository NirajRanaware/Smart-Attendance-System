"""
Attendance Scanning and Retrieval API Routes.
Provides endpoints for:
1. Receiving RFID scan requests (POST /api/scans) with atomic transaction safety.
2. Retrieving daily attendance records (GET /api/attendance?date=YYYY-MM-DD).
"""

import re
import sqlite3
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from app.database import get_db
from app.auth import login_required
from app.time_utils import is_session_expired

attendance_bp = Blueprint("attendance", __name__)


def validate_date_string(date_str: str) -> tuple[bool, str]:
    """
    Validates a date string:
    - Must match YYYY-MM-DD format
    - Must be a real calendar date
    
    :return: (is_valid: bool, error_message: str)
    """
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return False, "Query parameter 'date' must be in YYYY-MM-DD format (e.g. 2026-11-07)."
    
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, f"'{date_str}' is not a valid calendar date."


def validate_scan_payload(data: dict) -> tuple[bool, str]:
    """
    Validates the RFID scan payload:
    - Checks required keys: card_uid, source
    - Validates data types (strings) and non-empty values
    - Validates card_uid format (alphanumeric/hex)
    - Validates source length
    
    :return: (is_valid: bool, error_message: str)
    """
    required_fields = ["card_uid", "source"]
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: '{field}'"
        
        val = data[field]
        if not isinstance(val, str):
            return False, f"Field '{field}' must be a string."
        
        trimmed = val.strip()
        if not trimmed:
            return False, f"Field '{field}' cannot be empty or whitespace only."
    
    card_uid = data["card_uid"].strip().upper()
    if len(card_uid) < 4 or len(card_uid) > 50:
        return False, "Field 'card_uid' must be between 4 and 50 characters."
    
    if not re.match(r"^[A-Z0-9_-]+$", card_uid):
        return False, "Field 'card_uid' must contain only alphanumeric characters, dashes, or underscores."
    
    source = data["source"].strip()
    if len(source) > 50:
        return False, "Field 'source' cannot exceed 50 characters."
    
    return True, ""


@attendance_bp.route("/scans", methods=["POST"])
@login_required
def process_scan():
    """
    POST /api/scans
    Receives an RFID scan, verifies the student and active session,
    prevents duplicate attendance, and records the attendance in SQLite.
    Employs BEGIN IMMEDIATE transaction safety to serialize session checks and inserts.
    """
    # 1. Validate request format
    if not request.is_json:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Request body must be a valid JSON object with Content-Type: application/json."
        }), 400
    
    payload = request.get_json(silent=True)
    if payload is None or not isinstance(payload, dict):
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Malformed or invalid JSON payload."
        }), 400
    
    # 2. Validate payload fields
    is_valid, error_msg = validate_scan_payload(payload)
    if not is_valid:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": error_msg
        }), 400
    
    card_uid = payload["card_uid"].strip().upper()
    source = payload["source"].strip()
    
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        
        # 3. Identify the Student
        cursor.execute(
            """
            SELECT id, roll_number, full_name, active 
            FROM students 
            WHERE card_uid = ?;
            """,
            (card_uid,)
        )
        student = cursor.fetchone()
        
        if student is None:
            return jsonify({
                "success": False,
                "error": "Student Not Found",
                "message": "This RFID card is not registered."
            }), 404
        
        # 4. Check whether student account is active
        if student["active"] != 1:
            return jsonify({
                "success": False,
                "error": "Inactive Student",
                "message": "This student account is inactive."
            }), 403
        
        # 5. Transaction Safety: Use BEGIN IMMEDIATE to serialize active session verification and insert
        db.execute("BEGIN IMMEDIATE;")
        
        # Check currently active class session inside the transaction lock
        cursor.execute(
            """
            SELECT id, course_name, session_date, start_time, end_time 
            FROM class_sessions 
            WHERE active = 1 
            LIMIT 1;
            """
        )
        active_session = cursor.fetchone()
        
        if active_session is None:
            db.rollback()
            return jsonify({
                "success": False,
                "error": "No Active Session",
                "message": "No class session is currently active. Attendance cannot be recorded."
            }), 409
        
        # Check if the active session has ended (reached or passed end_time)
        if is_session_expired(active_session["session_date"], active_session["end_time"]):
            cursor.execute("UPDATE class_sessions SET active = 0 WHERE id = ?;", (active_session["id"],))
            db.commit()
            return jsonify({
                "success": False,
                "error": "Conflict",
                "message": "The active class session has ended. Attendance cannot be recorded."
            }), 409
        
        student_id = student["id"]
        session_id = active_session["id"]
        
        # 6. Check for duplicate attendance
        cursor.execute(
            """
            SELECT id, scanned_at 
            FROM attendance 
            WHERE student_id = ? AND session_id = ?;
            """,
            (student_id, session_id)
        )
        existing_attendance = cursor.fetchone()
        
        if existing_attendance is not None:
            db.rollback()
            return jsonify({
                "success": False,
                "error": "Duplicate Attendance",
                "message": "Attendance has already been recorded for this student in this session."
            }), 409
        
        # 7. Generate server UTC timestamp (ISO 8601 ending in Z)
        scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # 8. Insert attendance record
        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (?, ?, ?, 'Present', ?);
            """,
            (student_id, session_id, scanned_at, source)
        )
        attendance_id = cursor.lastrowid

        # 9. Atomic Outbox Queue Insertion (Phase 3 Milestone 2)
        # Guarantees that every committed attendance record has exactly one sync queue record in the same transaction
        cursor.execute(
            """
            INSERT INTO sheets_sync_queue (
                attendance_id, sync_status, retry_count, created_at, updated_at
            ) VALUES (?, 'pending', 0, ?, ?);
            """,
            (attendance_id, scanned_at, scanned_at)
        )

        db.commit()
        
        return jsonify({
            "success": True,
            "message": "Attendance recorded successfully",
            "attendance": {
                "id": attendance_id,
                "roll_number": student["roll_number"],
                "student_name": student["full_name"],
                "course_name": active_session["course_name"],
                "session_id": session_id,
                "scanned_at": scanned_at,
                "status": "Present",
                "source": source
            }
        }), 201

    except sqlite3.IntegrityError as e:
        if db is not None:
            db.rollback()
        current_app.logger.warning(f"Integrity error recording attendance: {e}")
        return jsonify({
            "success": False,
            "error": "Duplicate Attendance",
            "message": "Attendance has already been recorded for this student in this session."
        }), 409

    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        current_app.logger.error(f"Database error recording attendance: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to record attendance due to a database error."
        }), 500


@attendance_bp.route("/attendance", methods=["GET"])
@login_required
def get_daily_attendance():
    """
    GET /api/attendance?date=YYYY-MM-DD
    Retrieves all attendance records for class sessions scheduled on the given date.
    """
    date_str = request.args.get("date")
    if date_str is None:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Missing required query parameter: 'date'."
        }), 400
    
    date_str = date_str.strip()
    if not date_str:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Query parameter 'date' cannot be empty."
        }), 400
        
    is_valid, error_msg = validate_date_string(date_str)
    if not is_valid:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": error_msg
        }), 400
    
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT 
                a.id,
                s.roll_number,
                s.full_name AS student_name,
                cs.course_name,
                a.session_id,
                cs.session_date,
                cs.start_time,
                cs.end_time,
                a.scanned_at,
                a.status,
                a.source
            FROM attendance a
            INNER JOIN students s ON a.student_id = s.id
            INNER JOIN class_sessions cs ON a.session_id = cs.id
            WHERE cs.session_date = ?
            ORDER BY cs.start_time ASC, a.scanned_at ASC, a.id ASC;
            """,
            (date_str,)
        )
        rows = cursor.fetchall()
        
        attendance_list = [
            {
                "id": row["id"],
                "roll_number": row["roll_number"],
                "student_name": row["student_name"],
                "course_name": row["course_name"],
                "session_id": row["session_id"],
                "session_date": row["session_date"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "scanned_at": row["scanned_at"],
                "status": row["status"],
                "source": row["source"]
            }
            for row in rows
        ]
        
        return jsonify({
            "success": True,
            "date": date_str,
            "count": len(attendance_list),
            "attendance": attendance_list
        }), 200

    except Exception as e:
        current_app.logger.error(f"Database error fetching daily attendance: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to retrieve attendance records from the database."
        }), 500
