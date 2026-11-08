"""
Class Session Management API Routes.
Provides endpoints to create, retrieve, activate, and deactivate class lecture sessions.
Guarantees at application and database levels that only one session can be active at a time.
"""

import re
import sqlite3
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from app.database import get_db
from app.auth import login_required
from app.time_utils import is_session_expired
from app.scheduler import check_and_deactivate_expired_sessions

sessions_bp = Blueprint("sessions", __name__)


def validate_session_payload(data: dict) -> tuple[bool, str]:
    """
    Validates input for creating a new class session:
    - Required fields: course_name, session_date, start_time, end_time
    - Non-empty strings
    - Valid calendar date (YYYY-MM-DD)
    - Valid 24-hour time (HH:MM)
    - End time must be later than start time
    
    :return: (is_valid: bool, error_message: str)
    """
    required_fields = ["course_name", "session_date", "start_time", "end_time"]
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: '{field}'"
        
        val = data[field]
        if not isinstance(val, str):
            return False, f"Field '{field}' must be a string."
        
        if not val.strip():
            return False, f"Field '{field}' cannot be empty or whitespace only."
    
    course_name = data["course_name"].strip()
    if len(course_name) < 2 or len(course_name) > 100:
        return False, "Field 'course_name' must be between 2 and 100 characters."
    
    session_date = data["session_date"].strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", session_date):
        return False, "Field 'session_date' must be in YYYY-MM-DD format (e.g. 2026-11-07)."
    
    try:
        # Validates real calendar date (e.g. rejects 2026-02-30)
        datetime.strptime(session_date, "%Y-%m-%d")
    except ValueError:
        return False, f"'{session_date}' is not a valid calendar date."
    
    start_time = data["start_time"].strip()
    if not re.match(r"^\d{2}:\d{2}$", start_time):
        return False, "Field 'start_time' must be in 24-hour HH:MM format (e.g. 10:00)."
    
    try:
        # Validates hour (00-23) and minute (00-59)
        start_t = datetime.strptime(start_time, "%H:%M").time()
    except ValueError:
        return False, f"'{start_time}' is not a valid 24-hour time (00:00 to 23:59)."
    
    end_time = data["end_time"].strip()
    if not re.match(r"^\d{2}:\d{2}$", end_time):
        return False, "Field 'end_time' must be in 24-hour HH:MM format (e.g. 11:00)."
    
    try:
        end_t = datetime.strptime(end_time, "%H:%M").time()
    except ValueError:
        return False, f"'{end_time}' is not a valid 24-hour time (00:00 to 23:59)."
    
    if end_t <= start_t:
        return False, "End time must be later than start time."
    
    return True, ""


@sessions_bp.route("/sessions", methods=["POST"])
@login_required
def create_session():
    """
    POST /api/sessions
    Creates a new class session (inactive by default).
    """
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
    
    is_valid, error_msg = validate_session_payload(payload)
    if not is_valid:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": error_msg
        }), 400
    
    course_name = payload["course_name"].strip()
    session_date = payload["session_date"].strip()
    start_time = payload["start_time"].strip()
    end_time = payload["end_time"].strip()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        
        cursor.execute(
            """
            INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
            VALUES (?, ?, ?, ?, 0, ?);
            """,
            (course_name, session_date, start_time, end_time, created_at)
        )
        session_id = cursor.lastrowid
        db.commit()
        
        return jsonify({
            "success": True,
            "message": "Class session created successfully",
            "session": {
                "id": session_id,
                "course_name": course_name,
                "session_date": session_date,
                "start_time": start_time,
                "end_time": end_time,
                "active": False
            }
        }), 201

    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        current_app.logger.error(f"Database error creating class session: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to create class session due to a database error."
        }), 500


@sessions_bp.route("/sessions", methods=["GET"])
@login_required
def get_sessions():
    """
    GET /api/sessions
    Retrieves all class sessions sorted by newest session_date and start_time first.
    Performs JIT session expiry check before returning results.
    """
    try:
        check_and_deactivate_expired_sessions()
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, course_name, session_date, start_time, end_time, active, created_at 
            FROM class_sessions 
            ORDER BY session_date DESC, start_time DESC, id DESC;
            """
        )
        rows = cursor.fetchall()
        
        sessions_list = [
            {
                "id": row["id"],
                "course_name": row["course_name"],
                "session_date": row["session_date"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "active": bool(row["active"]),
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        
        return jsonify({
            "success": True,
            "count": len(sessions_list),
            "sessions": sessions_list
        }), 200

    except Exception as e:
        current_app.logger.error(f"Database error retrieving sessions: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to retrieve class sessions from the database."
        }), 500


@sessions_bp.route("/sessions/active", methods=["GET"])
@login_required
def get_active_session():
    """
    GET /api/sessions/active
    Retrieves the currently active class session.
    Performs JIT session expiry check before returning results.
    If no session is active, returns HTTP 200 with session: null.
    """
    try:
        check_and_deactivate_expired_sessions()
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, course_name, session_date, start_time, end_time, active, created_at 
            FROM class_sessions 
            WHERE active = 1 
            LIMIT 1;
            """
        )
        row = cursor.fetchone()
        
        if row is None:
            return jsonify({
                "success": True,
                "message": "No active class session",
                "session": None
            }), 200
        
        # JIT check: if active session has reached end time, deactivate immediately
        if is_session_expired(row["session_date"], row["end_time"]):
            cursor.execute("UPDATE class_sessions SET active = 0 WHERE id = ?;", (row["id"],))
            db.commit()
            return jsonify({
                "success": True,
                "message": "No active class session",
                "session": None
            }), 200

        return jsonify({
            "success": True,
            "session": {
                "id": row["id"],
                "course_name": row["course_name"],
                "session_date": row["session_date"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "active": True
            }
        }), 200

    except Exception as e:
        current_app.logger.error(f"Database error fetching active session: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to retrieve the active class session."
        }), 500


@sessions_bp.route("/sessions/<int:session_id>/activate", methods=["PATCH"])
@login_required
def activate_session(session_id: int):
    """
    PATCH /api/sessions/<session_id>/activate
    Activates the specified session while automatically deactivating any previously active session.
    Prevents activating expired sessions. Executed inside an immediate atomic transaction.
    """
    if session_id <= 0:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Session ID must be a positive integer."
        }), 400
    
    db = None
    try:
        check_and_deactivate_expired_sessions()
        db = get_db()
        cursor = db.cursor()
        
        # Check if the requested session exists
        cursor.execute(
            """
            SELECT id, course_name, session_date, start_time, end_time, active 
            FROM class_sessions 
            WHERE id = ?;
            """,
            (session_id,)
        )
        target_session = cursor.fetchone()
        if target_session is None:
            return jsonify({
                "success": False,
                "error": "Not Found",
                "message": f"Class session with ID {session_id} does not exist."
            }), 404
        
        # Reject activating expired session
        if is_session_expired(target_session["session_date"], target_session["end_time"]):
            if target_session["active"] == 1:
                cursor.execute("UPDATE class_sessions SET active = 0 WHERE id = ?;", (session_id,))
                db.commit()
            return jsonify({
                "success": False,
                "error": "Bad Request",
                "message": "Cannot activate an expired class session."
            }), 400

        # If already active, return success immediately
        if target_session["active"] == 1:
            return jsonify({
                "success": True,
                "message": "Class session is already active",
                "session": {
                    "id": target_session["id"],
                    "course_name": target_session["course_name"],
                    "session_date": target_session["session_date"],
                    "start_time": target_session["start_time"],
                    "end_time": target_session["end_time"],
                    "active": True
                }
            }), 200
        
        # Begin safe atomic transaction
        db.execute("BEGIN IMMEDIATE;")
        
        # 1. Deactivate previously active session(s)
        cursor.execute("UPDATE class_sessions SET active = 0 WHERE active = 1;")
        
        # 2. Activate requested session
        cursor.execute("UPDATE class_sessions SET active = 1 WHERE id = ?;", (session_id,))
        
        db.commit()
        
        return jsonify({
            "success": True,
            "message": "Class session activated successfully",
            "session": {
                "id": target_session["id"],
                "course_name": target_session["course_name"],
                "session_date": target_session["session_date"],
                "start_time": target_session["start_time"],
                "end_time": target_session["end_time"],
                "active": True
            }
        }), 200

    except sqlite3.IntegrityError as e:
        if db is not None:
            db.rollback()
        current_app.logger.error(f"Constraint error activating session: {e}")
        return jsonify({
            "success": False,
            "error": "Conflict",
            "message": "Could not activate session due to an active session constraint conflict."
        }), 409

    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        current_app.logger.error(f"Error activating session: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to activate class session due to a database error."
        }), 500


@sessions_bp.route("/sessions/<int:session_id>/deactivate", methods=["PATCH"])
@login_required
def deactivate_session(session_id: int):
    """
    PATCH /api/sessions/<session_id>/deactivate
    Deactivates the specified class session.
    """
    if session_id <= 0:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": "Session ID must be a positive integer."
        }), 400
    
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Check if the requested session exists
        cursor.execute("SELECT id, course_name, session_date, start_time, end_time, active FROM class_sessions WHERE id = ?;", (session_id,))
        target_session = cursor.fetchone()
        if target_session is None:
            return jsonify({
                "success": False,
                "error": "Not Found",
                "message": f"Class session with ID {session_id} does not exist."
            }), 404
        
        # If already inactive, return success gracefully
        if target_session["active"] == 0:
            return jsonify({
                "success": True,
                "message": "Class session is already inactive",
                "session": {
                    "id": target_session["id"],
                    "course_name": target_session["course_name"],
                    "session_date": target_session["session_date"],
                    "start_time": target_session["start_time"],
                    "end_time": target_session["end_time"],
                    "active": False
                }
            }), 200
        
        # Deactivate session
        cursor.execute("UPDATE class_sessions SET active = 0 WHERE id = ?;", (session_id,))
        db.commit()
        
        return jsonify({
            "success": True,
            "message": "Class session deactivated successfully",
            "session": {
                "id": target_session["id"],
                "course_name": target_session["course_name"],
                "session_date": target_session["session_date"],
                "start_time": target_session["start_time"],
                "end_time": target_session["end_time"],
                "active": False
            }
        }), 200

    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        current_app.logger.error(f"Error deactivating session: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to deactivate class session due to a database error."
        }), 500
