"""
Student Registration and Retrieval API Routes.
Provides endpoints to register new students with unique RFID UIDs and list registered students.
"""

import re
import sqlite3
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app
from app.database import get_db
from app.auth import login_required

students_bp = Blueprint("students", __name__)


def validate_student_payload(data: dict) -> tuple[bool, str]:
    """
    Validates the student registration payload:
    - Checks required keys: roll_number, full_name, card_uid
    - Validates data types, lengths, and non-empty strings
    - Normalizes values (trimmed, card_uid uppercased)
    
    :return: (is_valid: bool, error_message: str)
    """
    required_fields = ["roll_number", "full_name", "card_uid"]
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: '{field}'"
        
        val = data[field]
        if not isinstance(val, str):
            return False, f"Field '{field}' must be a string."
        
        trimmed = val.strip()
        if not trimmed:
            return False, f"Field '{field}' cannot be empty or whitespace only."
    
    # Specific format/length checks
    roll_number = data["roll_number"].strip()
    if len(roll_number) > 50:
        return False, "Field 'roll_number' exceeds maximum length of 50 characters."
    
    full_name = data["full_name"].strip()
    if len(full_name) < 2 or len(full_name) > 100:
        return False, "Field 'full_name' must be between 2 and 100 characters."
    
    card_uid = data["card_uid"].strip().upper()
    if len(card_uid) < 4 or len(card_uid) > 50:
        return False, "Field 'card_uid' must be between 4 and 50 characters."
    
    # RFID UIDs are typically alphanumeric / hexadecimal (e.g., A1B2C3D4 or 04A2B3C4)
    if not re.match(r"^[A-Z0-9_-]+$", card_uid):
        return False, "Field 'card_uid' must contain only alphanumeric characters, dashes, or underscores."

    return True, ""


@students_bp.route("/students", methods=["POST"])
@login_required
def register_student():
    """
    POST /api/students
    Registers a new student with roll number, full name, and RFID card UID.
    """
    # 1. Verify incoming request has valid JSON body
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
    
    # 2. Validate input fields
    is_valid, error_msg = validate_student_payload(payload)
    if not is_valid:
        return jsonify({
            "success": False,
            "error": "Bad Request",
            "message": error_msg
        }), 400
    
    # 3. Clean and normalize inputs
    roll_number = payload["roll_number"].strip()
    full_name = payload["full_name"].strip()
    card_uid = payload["card_uid"].strip().upper()
    
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        
        # 4. Check for existing roll number (Pre-insertion validation)
        cursor.execute("SELECT id FROM students WHERE roll_number = ?;", (roll_number,))
        if cursor.fetchone() is not None:
            return jsonify({
                "success": False,
                "error": "Conflict",
                "message": f"A student with roll number '{roll_number}' already exists."
            }), 409
        
        # 5. Check for existing RFID card UID (Pre-insertion validation)
        cursor.execute("SELECT id FROM students WHERE card_uid = ?;", (card_uid,))
        if cursor.fetchone() is not None:
            return jsonify({
                "success": False,
                "error": "Conflict",
                "message": f"A student with RFID card UID '{card_uid}' already exists."
            }), 409
        
        # 6. Server-generated timestamp (ISO 8601 UTC)
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        
        # 7. Insert the new student into SQLite
        cursor.execute(
            """
            INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
            VALUES (?, ?, ?, 1, ?);
            """,
            (roll_number, full_name, card_uid, created_at)
        )
        student_id = cursor.lastrowid
        db.commit()
        
        # 8. Return structured success response
        return jsonify({
            "success": True,
            "message": "Student registered successfully",
            "student": {
                "id": student_id,
                "roll_number": roll_number,
                "full_name": full_name,
                "card_uid": card_uid,
                "active": True
            }
        }), 201

    except sqlite3.IntegrityError as e:
        if db is not None:
            db.rollback()
        current_app.logger.warning(f"Integrity error during student registration: {e}")
        # Database UNIQUE constraint tripped as safety net
        return jsonify({
            "success": False,
            "error": "Conflict",
            "message": "Student registration failed because roll number or card UID already exists."
        }), 409

    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        current_app.logger.error(f"Database error registering student: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Database insertion failed while registering student."
        }), 500


@students_bp.route("/students", methods=["GET"])
@login_required
def get_students():
    """
    GET /api/students
    Retrieves all registered students sorted by roll number.
    """
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, roll_number, full_name, card_uid, active, created_at 
            FROM students 
            ORDER BY roll_number ASC;
            """
        )
        rows = cursor.fetchall()
        
        students_list = [
            {
                "id": row["id"],
                "roll_number": row["roll_number"],
                "full_name": row["full_name"],
                "card_uid": row["card_uid"],
                "active": bool(row["active"]),
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        
        return jsonify({
            "success": True,
            "count": len(students_list),
            "students": students_list
        }), 200

    except Exception as e:
        current_app.logger.error(f"Database error fetching students: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to retrieve student records from the database."
        }), 500
