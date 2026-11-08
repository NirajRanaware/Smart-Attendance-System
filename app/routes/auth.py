"""
Authentication Blueprint for Smart Attendance Management System.
Handles administrator login, logout, session lifecycle, and authentication state checks.
"""

from datetime import datetime, timezone
from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from app.auth import (
    check_login_rate_limit,
    get_client_identifier,
    get_csrf_token,
    get_current_admin,
    is_authenticated,
    is_safe_url,
    record_failed_login,
    reset_failed_logins,
    verify_password,
)
from app.database import get_db

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET"])
def login_view():
    """
    GET /login
    Renders the administrator login interface.
    If already authenticated, redirects to the dashboard.
    """
    if is_authenticated():
        return redirect(url_for("views.dashboard_view"))
    
    # Pre-generate CSRF token in session for the login form
    get_csrf_token()
    
    next_url = request.args.get("next", "")
    return render_template("login.html", next=next_url, active_page="login")


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /login
    Authenticates administrator credentials (supports both form submission and JSON API).
    Enforces rate-limiting, secure password hash comparison, generic failure messages,
    session rotation, and safe internal redirects.
    """
    client_id = get_client_identifier()
    
    # 1. Rate-limiting check
    allowed, rate_msg = check_login_rate_limit(client_id)
    if not allowed:
        if request.is_json:
            return jsonify({
                "success": False,
                "error": "Too Many Requests",
                "message": rate_msg
            }), 429
        return render_template("login.html", error=rate_msg, active_page="login"), 429

    # 2. Extract credentials
    username = ""
    password = ""
    next_url = ""

    if request.is_json:
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        next_url = str(data.get("next", "")).strip()
    else:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_url = request.form.get("next", "").strip()

    # 3. Empty input validation
    if not username or not password:
        err = "Username and password are required."
        if request.is_json:
            return jsonify({
                "success": False,
                "error": "Bad Request",
                "message": err
            }), 400
        return render_template("login.html", error=err, active_page="login"), 400

    # 4. Query administrator record from SQLite
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, username, password_hash, role, active 
            FROM admin_users 
            WHERE username = ?;
            """,
            (username,)
        )
        user = cursor.fetchone()

        # Generic authentication failure message to prevent username enumeration
        auth_failed_msg = "Invalid username or password."

        # Verify existence, active status, and password hash
        if user is None or not verify_password(user["password_hash"], password):
            record_failed_login(client_id)
            if request.is_json:
                return jsonify({
                    "success": False,
                    "error": "Unauthorized",
                    "message": auth_failed_msg
                }), 401
            return render_template("login.html", error=auth_failed_msg, active_page="login"), 401

        # Check if the administrator account is deactivated
        if not bool(user["active"]):
            record_failed_login(client_id)
            if request.is_json:
                return jsonify({
                    "success": False,
                    "error": "Unauthorized",
                    "message": auth_failed_msg
                }), 401
            return render_template("login.html", error=auth_failed_msg, active_page="login"), 401

        # 5. Successful authentication
        reset_failed_logins(client_id)

        # Clear session to prevent session fixation attacks
        session.clear()
        session["admin_id"] = user["id"]
        session["admin_username"] = user["username"]
        session["admin_role"] = user["role"]
        session.permanent = True

        # Generate a fresh CSRF token for the new session
        get_csrf_token()

        # Determine safe redirect target
        target = url_for("views.dashboard_view")
        if next_url and is_safe_url(next_url):
            target = next_url

        if request.is_json:
            return jsonify({
                "success": True,
                "message": "Login successful.",
                "redirect": target,
                "user": {
                    "username": user["username"],
                    "role": user["role"]
                }
            }), 200

        return redirect(target)

    except Exception as e:
        current_app.logger.error(f"Database error during administrator login: {e}")
        err_msg = "An error occurred while verifying credentials. Please try again."
        if request.is_json:
            return jsonify({
                "success": False,
                "error": "Internal Server Error",
                "message": err_msg
            }), 500
        return render_template("login.html", error=err_msg, active_page="login"), 500


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    POST /logout
    Terminates administrator session and redirects to the login screen.
    Protected by CSRF verification (GET method is strictly disallowed).
    """
    session.clear()
    
    if request.is_json:
        return jsonify({
            "success": True,
            "message": "Logged out successfully.",
            "redirect": url_for("auth.login_view")
        }), 200

    return redirect(url_for("auth.login_view"))


@auth_bp.route("/api/auth/status", methods=["GET"])
def auth_status():
    """
    GET /api/auth/status
    Returns current administrator authentication state.
    """
    return jsonify({
        "success": True,
        "authenticated": is_authenticated(),
        "user": get_current_admin()
    }), 200
