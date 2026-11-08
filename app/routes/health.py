"""
Health Check API Route.
Verifies server status and database connectivity.
"""

from flask import Blueprint, jsonify, current_app
from app.database import get_db

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health_check():
    """
    GET /api/health
    Checks whether the backend server is running and the database is accessible.
    """
    try:
        db = get_db()
        # Perform a lightweight ping query
        cursor = db.cursor()
        cursor.execute("SELECT 1;")
        cursor.fetchone()
        
        return jsonify({
            "success": True,
            "message": "Smart Attendance API is running",
            "database": "connected"
        }), 200
        
    except Exception as e:
        # Log internal error for developer debugging, return clean JSON to client
        current_app.logger.error(f"Health check database failure: {e}")
        return jsonify({
            "success": False,
            "message": "Smart Attendance API is running",
            "database": "disconnected",
            "error": "Database service unavailable"
        }), 503
