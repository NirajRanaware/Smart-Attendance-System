"""
Admin Route Blueprint for Smart Attendance Management System.
Provides administrative endpoints for database statistics and safe test data clearing.
Requires administrator authentication and strict confirmation checks.
"""

from pathlib import Path
from flask import Blueprint, jsonify, request, current_app
from app.auth import login_required
from app.backup import get_database_stats, clear_all_test_data
from app.database import get_db_connection, close_db_connection
from app.services.sheets_outbox import (
    get_queue_stats,
    get_dead_letter_records,
    retry_dead_letter_records,
)
from app.services.google_sheets import get_google_config

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/database-stats", methods=["GET"])
@admin_bp.route("/admin/stats", methods=["GET"])
@login_required
def database_stats():
    """
    GET /api/admin/database-stats
    Returns live, non-hardcoded counts of registered students, active students,
    class sessions, attendance records, and administrator accounts.
    Protected: Requires administrator authentication.
    """
    try:
        db_path = current_app.config.get("DATABASE_PATH")
        stats = get_database_stats(db_path)
        return jsonify({
            "success": True,
            "data": stats,
            "message": "Database statistics retrieved successfully."
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Database Stats Error",
            "message": f"Failed to retrieve database statistics: {str(e)}"
        }), 500


@admin_bp.route("/admin/sheets-sync/status", methods=["GET"])
@login_required
def sheets_sync_status():
    """
    GET /api/admin/sheets-sync/status
    Returns status of Google Sheets synchronization, including queue telemetry
    and configuration presence flags.
    Protected: Requires administrator authentication (@login_required).
    Security: Never exposes credentials, secrets, or local file system paths.
    """
    try:
        cfg = get_google_config()
        db_path = current_app.config.get("DATABASE_PATH")

        creds_exist = False
        if cfg["credentials_path"]:
            creds_exist = Path(cfg["credentials_path"]).is_file()

        stats = get_queue_stats(db_path)

        last_sync = None
        conn = None
        try:
            conn = get_db_connection(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(synced_at) AS last_sync FROM sheets_sync_queue WHERE sync_status = 'synced';")
            row = cursor.fetchone()
            if row and row["last_sync"]:
                last_sync = row["last_sync"]
        except Exception:
            pass
        finally:
            if conn:
                close_db_connection(conn)

        return jsonify({
            "success": True,
            "sync_enabled": current_app.config.get("GOOGLE_SHEETS_SYNC_ENABLED", True),
            "credentials_configured": creds_exist,
            "spreadsheet_id_configured": bool(cfg["spreadsheet_id"]),
            "queue_stats": stats,
            "last_sync_timestamp": last_sync
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Sync Status Error",
            "message": f"Failed to retrieve synchronization status: {str(e)}"
        }), 500


@admin_bp.route("/admin/sheets-sync/dead-letter", methods=["GET"])
@login_required
def get_dead_letter():
    """
    GET /api/admin/sheets-sync/dead-letter
    Returns paginated list of dead-letter records with error categories and student metadata.
    Protected: Requires administrator authentication (@login_required).
    Query parameters:
    - limit: int (default 50, max 200)
    - offset: int (default 0)
    """
    try:
        db_path = current_app.config.get("DATABASE_PATH")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        # Enforce reasonable boundaries
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        records = get_dead_letter_records(limit=limit, offset=offset, db_path=db_path)
        stats = get_queue_stats(db_path=db_path)

        return jsonify({
            "success": True,
            "total_dead_letter": stats.get("dead_letter", 0),
            "limit": limit,
            "offset": offset,
            "records": records,
            "message": f"Retrieved {len(records)} dead-letter record(s)."
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching dead letter records: {e}")
        return jsonify({
            "success": False,
            "error": "Dead Letter Fetch Error",
            "message": str(e)
        }), 500


@admin_bp.route("/admin/sheets-sync/dead-letter/retry", methods=["POST"])
@login_required
def retry_dead_letter():
    """
    POST /api/admin/sheets-sync/dead-letter/retry
    Requeues dead-letter synchronization records back to 'pending'.
    Protected: Requires administrator authentication (@login_required) and CSRF protection.
    JSON payload:
    - queue_ids: Optional list of integer queue IDs to retry.
    """
    try:
        db_path = current_app.config.get("DATABASE_PATH")
        queue_ids = None

        if request.is_json:
            data = request.get_json(silent=True) or {}
            if "queue_ids" in data:
                raw_ids = data.get("queue_ids")
                if not isinstance(raw_ids, list):
                    return jsonify({
                        "success": False,
                        "error": "Invalid Payload",
                        "message": "'queue_ids' must be a list of integers."
                    }), 400
                if len(raw_ids) == 0:
                    return jsonify({
                        "success": False,
                        "error": "Invalid Payload",
                        "message": "'queue_ids' must not be empty. Omit 'queue_ids' to requeue all records, or provide specific IDs."
                    }), 400
                try:
                    queue_ids = [int(i) for i in raw_ids]
                except (ValueError, TypeError):
                    return jsonify({
                        "success": False,
                        "error": "Invalid Payload",
                        "message": "All elements in 'queue_ids' must be integers."
                    }), 400

        result = retry_dead_letter_records(queue_ids=queue_ids, reset_retries=True, db_path=db_path)
        return jsonify({
            "success": True,
            "requeued_count": result["requeued_count"],
            "requeued_ids": result["requeued_ids"],
            "message": f"Successfully requeued {result['requeued_count']} dead-letter record(s) for synchronization."
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error retrying dead letter records: {e}")
        return jsonify({
            "success": False,
            "error": "Dead Letter Retry Error",
            "message": str(e)
        }), 500


@admin_bp.route("/admin/clear-data", methods=["POST"])
@admin_bp.route("/admin/clear-test-data", methods=["POST"])
@login_required
def clear_data():
    """
    POST /api/admin/clear-data
    Safely clears operational test data (attendance, class_sessions, students)
    after creating and verifying an ACID-consistent SQLite backup.
    
    Security & Safety Guarantees:
    - Requires administrator authentication (@login_required).
    - Protected by CSRF middleware (X-CSRF-Token).
    - Requires JSON or form field 'confirmation_phrase' equal to 'DELETE ALL DATA'.
    - Performs mandatory SQLite online backup and integrity check before deleting.
    - If backup fails or verification fails, aborts completely (no data deleted).
    - Strictly preserves administrator credentials, roles, and password hashes.
    - Preserves database tables, indexes, constraints, and configuration.
    - Executes in an immediate database transaction (BEGIN IMMEDIATE).
    """
    # 1. Parse request payload
    data = {}
    if request.is_json:
        data = request.get_json(silent=True) or {}
    elif request.form:
        data = request.form

    confirmation_phrase = data.get("confirmation_phrase")
    
    # 2. Strict server-side confirmation phrase validation
    if not confirmation_phrase or not isinstance(confirmation_phrase, str):
        return jsonify({
            "success": False,
            "error": "Missing Confirmation Phrase",
            "message": "Confirmation phrase is required. You must provide 'confirmation_phrase': 'DELETE ALL DATA'."
        }), 400

    if confirmation_phrase != "DELETE ALL DATA":
        return jsonify({
            "success": False,
            "error": "Invalid Confirmation Phrase",
            "message": "Invalid confirmation phrase. You must type exactly 'DELETE ALL DATA' to proceed."
        }), 400

    # 3. Execute safe clearing operation
    try:
        db_path = current_app.config.get("DATABASE_PATH")
        backup_dir = current_app.config.get("BACKUP_DIR")
        
        result = clear_all_test_data(db_path=db_path, backup_dir=backup_dir)
        return jsonify(result), 200

    except Exception as e:
        current_app.logger.error(f"Error executing clear_all_test_data: {e}")
        return jsonify({
            "success": False,
            "error": "Clear Operation Failed",
            "message": str(e)
        }), 500
