"""
Daily Attendance Reports and CSV Export API Routes.
Provides endpoints for:
1. Daily attendance statistics summary per session (GET /api/reports/daily?date=YYYY-MM-DD).
2. Downloadable CSV attendance register (GET /api/reports/export.csv?date=YYYY-MM-DD).
"""

import csv
import io
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, Response, current_app
from app.database import get_db
from app.auth import login_required

reports_bp = Blueprint("reports", __name__)


def sanitize_csv_cell(value: object) -> str:
    """
    Sanitizes user-controlled values against CSV Formula Injection (CWE-1236).
    Spreadsheet applications (Excel, Google Sheets, LibreOffice Calc) may interpret
    cells beginning with '=', '+', '-', '@', '\t', '\r' as active executable formulas.
    Prepending a single quote (') neutralizes formula execution while preserving the text.
    
    :param value: Cell value to be written to CSV.
    :return: Safe string representation of the cell value.
    """
    if value is None:
        return ""
    val_str = str(value)
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


def validate_date_string(date_str: str) -> tuple[bool, str]:
    """
    Validates a date query parameter:
    - Must match YYYY-MM-DD format
    - Must represent a real calendar date
    
    :return: (is_valid: bool, error_message: str)
    """
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return False, "Query parameter 'date' must be in YYYY-MM-DD format (e.g. 2026-11-07)."
    
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True, ""
    except ValueError:
        return False, f"'{date_str}' is not a valid calendar date."


@reports_bp.route("/reports/daily", methods=["GET"])
@login_required
def get_daily_report():
    """
    GET /api/reports/daily?date=YYYY-MM-DD
    Generates attendance statistics for every class session scheduled on the selected date.
    Calculates total active students, present, absent, and percentage per session and overall.
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
        
        # 1. Fetch all class sessions scheduled on the selected date
        cursor.execute(
            """
            SELECT id, course_name, start_time, end_time 
            FROM class_sessions 
            WHERE session_date = ? 
            ORDER BY start_time ASC, id ASC;
            """,
            (date_str,)
        )
        sessions = cursor.fetchall()
        
        # 2. Count current active registered students in the system roster
        cursor.execute("SELECT COUNT(*) AS total_active FROM students WHERE active = 1;")
        total_active_students = cursor.fetchone()["total_active"]
        
        # 3. If no sessions exist for that date, return zero totals
        if not sessions:
            return jsonify({
                "success": True,
                "date": date_str,
                "summary": {
                    "total_sessions": 0,
                    "total_enrollments": 0,
                    "total_present": 0,
                    "total_absent": 0,
                    "overall_attendance_percentage": 0.0
                },
                "sessions": []
            }), 200
        
        session_stats = []
        total_enrollments = 0
        total_present = 0
        
        for sess in sessions:
            sess_id = sess["id"]
            # Count distinct active students with an attendance record for this session
            cursor.execute(
                """
                SELECT COUNT(DISTINCT a.student_id) AS present_count
                FROM attendance a
                INNER JOIN students s ON a.student_id = s.id
                WHERE a.session_id = ? AND s.active = 1;
                """,
                (sess_id,)
            )
            present_count = cursor.fetchone()["present_count"]
            
            absent_count = max(0, total_active_students - present_count)
            attendance_pct = (
                round((present_count / total_active_students) * 100, 2)
                if total_active_students > 0
                else 0.0
            )
            
            session_stats.append({
                "session_id": sess_id,
                "course_name": sess["course_name"],
                "start_time": sess["start_time"],
                "end_time": sess["end_time"],
                "total_students": total_active_students,
                "present": present_count,
                "absent": absent_count,
                "attendance_percentage": attendance_pct
            })
            
            total_enrollments += total_active_students
            total_present += present_count
        
        total_absent = total_enrollments - total_present
        overall_pct = (
            round((total_present / total_enrollments) * 100, 2)
            if total_enrollments > 0
            else 0.0
        )
        
        return jsonify({
            "success": True,
            "date": date_str,
            "summary": {
                "total_sessions": len(sessions),
                "total_enrollments": total_enrollments,
                "total_present": total_present,
                "total_absent": total_absent,
                "overall_attendance_percentage": overall_pct
            },
            "sessions": session_stats
        }), 200

    except Exception as e:
        current_app.logger.error(f"Database error generating daily report: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to generate daily attendance report."
        }), 500


@reports_bp.route("/reports/export.csv", methods=["GET"])
@login_required
def export_csv():
    """
    GET /api/reports/export.csv?date=YYYY-MM-DD
    Generates a downloadable CSV register for all scheduled sessions on the given date.
    Includes both Present and Absent active students using a LEFT JOIN.
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
        
        # 1. Fetch all sessions for this date
        cursor.execute(
            """
            SELECT id, course_name, start_time 
            FROM class_sessions 
            WHERE session_date = ? 
            ORDER BY start_time ASC, id ASC;
            """,
            (date_str,)
        )
        sessions = cursor.fetchall()
        
        # In-memory CSV buffer
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        
        # Write CSV header row
        headers = [
            "Date",
            "Session ID",
            "Course Name",
            "Start Time",
            "Roll Number",
            "Student Name",
            "RFID UID",
            "Attendance Status",
            "Scanned At (UTC)",
            "Source"
        ]
        writer.writerow(headers)
        
        # If sessions exist, write each student's attendance row per session
        for sess in sessions:
            sess_id = sess["id"]
            course_name = sess["course_name"]
            start_time = sess["start_time"]
            
            # LEFT JOIN active students with attendance for this specific session
            cursor.execute(
                """
                SELECT 
                    s.roll_number,
                    s.full_name AS student_name,
                    s.card_uid,
                    a.id AS attendance_id,
                    a.scanned_at,
                    a.source
                FROM students s
                LEFT JOIN attendance a ON s.id = a.student_id AND a.session_id = ?
                WHERE s.active = 1
                ORDER BY s.roll_number ASC;
                """,
                (sess_id,)
            )
            rows = cursor.fetchall()
            
            for row in rows:
                if row["attendance_id"] is not None:
                    status = "Present"
                    scanned_at = row["scanned_at"]
                    source = row["source"]
                else:
                    status = "Absent"
                    scanned_at = ""
                    source = ""
                
                writer.writerow([
                    date_str,
                    sess_id,
                    sanitize_csv_cell(course_name),
                    start_time,
                    sanitize_csv_cell(row["roll_number"]),
                    sanitize_csv_cell(row["student_name"]),
                    sanitize_csv_cell(row["card_uid"]),
                    status,
                    scanned_at,
                    sanitize_csv_cell(source)
                ])
        
        csv_data = output.getvalue()
        output.close()
        
        # Return CSV response with attachment header
        response = Response(csv_data, mimetype="text/csv")
        response.headers["Content-Disposition"] = f'attachment; filename="attendance_{date_str}.csv"'
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        return response, 200

    except Exception as e:
        current_app.logger.error(f"Database error exporting attendance CSV: {e}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "Failed to generate attendance CSV export."
        }), 500
