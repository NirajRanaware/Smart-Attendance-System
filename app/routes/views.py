"""
Web Frontend Route Blueprint.
Serves HTML dashboard templates and static UI views for the Smart Attendance Management System.
"""

from flask import Blueprint, render_template
from app.auth import login_required

views_bp = Blueprint("views", __name__)


@views_bp.route("/", methods=["GET"])
@views_bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard_view():
    """
    GET / and GET /dashboard
    Renders the primary web dashboard overview.
    """
    return render_template("dashboard.html", active_page="dashboard")


@views_bp.route("/students", methods=["GET"])
@login_required
def students_view():
    """
    GET /students
    Renders the Student Management interface.
    """
    return render_template("students.html", active_page="students")


@views_bp.route("/sessions", methods=["GET"])
@login_required
def sessions_view():
    """
    GET /sessions
    Renders the Class Sessions management interface.
    """
    return render_template("sessions.html", active_page="sessions")


@views_bp.route("/attendance", methods=["GET"])
@login_required
def attendance_view():
    """
    GET /attendance
    Renders the Live Attendance Monitoring & Simulator interface.
    """
    return render_template("attendance.html", active_page="attendance")


@views_bp.route("/reports", methods=["GET"])
@login_required
def reports_view():
    """
    GET /reports
    Renders the Attendance Reports & CSV Export interface.
    """
    return render_template("reports.html", active_page="reports")


@views_bp.route("/settings", methods=["GET"])
@login_required
def settings_view():
    """
    GET /settings
    Renders the System Settings & Administration interface with Database Management.
    """
    return render_template(
        "settings.html",
        title="Settings & Admin",
        section_name="System Configuration & Administration",
        milestone="Milestone 6",
        active_page="settings",
    )
