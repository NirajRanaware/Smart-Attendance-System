"""
Authentication and Authorization Module for Smart Attendance Management System.
Provides login/logout helpers, secure password hashing, session management,
login rate limiting, CSRF verification, and route protection decorators.
"""

import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit
from flask import (
    Flask,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from app.database import get_db

# Thread-safe in-memory tracking of failed login attempts: {key: [timestamp, ...]}
_rate_limit_lock = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes
MAX_FAILED_ATTEMPTS = 5


def hash_password(password: str) -> str:
    """Hashes a password using Werkzeug's secure hashing algorithm."""
    try:
        return generate_password_hash(password, method="scrypt")
    except Exception:
        # Fallback to pbkdf2 if scrypt is unavailable on older environments
        return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    """Verifies a plaintext password against a stored password hash."""
    if not password_hash or not password:
        return False
    return check_password_hash(password_hash, password)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validates that a password satisfies minimum security requirements:
    - At least 8 characters long
    - Contains at least one digit or special symbol
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[0-9!@#$%^&*()_+\-=\[\]{};':\",./<>?]", password):
        return False, "Password must contain at least one digit or special symbol."
    return True, ""


def get_client_identifier() -> str:
    """
    Retrieves an IP-based client identifier for rate-limiting.
    Uses request.remote_addr directly to prevent header spoofing.
    X-Forwarded-For is strictly rejected unless the immediate peer is an explicitly trusted proxy.
    """
    peer_ip = request.remote_addr or "127.0.0.1"
    trusted_proxies = current_app.config.get("TRUSTED_PROXIES", [])
    if trusted_proxies and peer_ip in trusted_proxies:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer_ip


def check_login_rate_limit(identifier: str) -> tuple[bool, str]:
    """
    Checks if the client has exceeded the maximum number of failed login attempts.
    Thread-safe implementation protected by _rate_limit_lock.
    Returns (allowed: bool, message: str).
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        attempts = _failed_attempts.get(identifier, [])
        # Filter attempts within current window
        recent_attempts = [t for t in attempts if t > cutoff]
        if recent_attempts:
            _failed_attempts[identifier] = recent_attempts
        else:
            _failed_attempts.pop(identifier, None)

        if len(recent_attempts) >= MAX_FAILED_ATTEMPTS:
            remaining = int((recent_attempts[0] + RATE_LIMIT_WINDOW_SECONDS) - now)
            wait_min = max(1, (remaining + 59) // 60)
            return False, f"Too many failed login attempts. Please wait {wait_min} minute(s) before trying again."

    return True, ""


def record_failed_login(identifier: str) -> None:
    """Records a failed login attempt timestamp for the client identifier (thread-safe)."""
    now = time.time()
    with _rate_limit_lock:
        if identifier not in _failed_attempts:
            _failed_attempts[identifier] = []
        _failed_attempts[identifier].append(now)


def reset_failed_logins(identifier: str) -> None:
    """Clears failed login attempt records upon successful login (thread-safe)."""
    with _rate_limit_lock:
        _failed_attempts.pop(identifier, None)


def clear_all_rate_limits() -> None:
    """Resets all rate limiting records across all identifiers (thread-safe, used in tests)."""
    with _rate_limit_lock:
        _failed_attempts.clear()


def is_safe_url(target: str) -> bool:
    """
    Ensures a redirect target is an internal relative path on this host to prevent open redirects.
    """
    if not target:
        return False
    # Ensure target starts with / and not // or /\ to avoid open redirect vulnerabilities
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return False
    if "\\" in target:
        return False
    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(target)
    return test_url.scheme in ("",) and test_url.netloc in ("", ref_url.netloc)


def get_csrf_token() -> str:
    """Retrieves or creates a cryptographically secure CSRF token in the session."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf_token(token: str) -> bool:
    """Validates a submitted CSRF token against the session CSRF token."""
    expected = session.get("csrf_token")
    if not expected or not token:
        return False
    return hmac.compare_digest(str(expected), str(token))


def is_authenticated() -> bool:
    """Returns True if an administrator is currently logged in."""
    return bool(session.get("admin_id") and session.get("admin_username"))


def get_current_admin() -> dict | None:
    """Returns dict of current logged-in administrator or None."""
    if not is_authenticated():
        return None
    return {
        "id": session.get("admin_id"),
        "username": session.get("admin_username"),
        "role": session.get("admin_role", "admin"),
    }


def login_required(view_func):
    """
    Decorator to protect administrative views and API endpoints.
    - If LOGIN_DISABLED is True in application config (e.g. testing), bypasses check.
    - If unauthenticated API/JSON request: returns HTTP 401 JSON.
    - If unauthenticated HTML request: redirects to /login with safe 'next' param.
    """
    @wraps(view_func)
    def decorated_view(*args, **kwargs):
        if current_app.config.get("LOGIN_DISABLED", False):
            return view_func(*args, **kwargs)

        if not is_authenticated():
            # Check if this is an API or JSON request
            is_api = (
                request.path.startswith("/api/")
                or request.is_json
                or request.headers.get("Accept") == "application/json"
            )
            if is_api:
                return jsonify({
                    "success": False,
                    "error": "Unauthorized",
                    "message": "Administrator authentication is required to access this endpoint."
                }), 401
            
            # HTML page request - redirect to login
            safe_next = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login_view", next=safe_next))

        return view_func(*args, **kwargs)

    return decorated_view


def init_security_extensions(app: Flask) -> None:
    """
    Initializes security extensions, template globals, CSRF middleware, and cache headers.
    """
    # 1. Template context processor for csrf_token and current_admin
    @app.context_processor
    def inject_security_context():
        return {
            "csrf_token": get_csrf_token,
            "current_admin": get_current_admin(),
            "is_authenticated": is_authenticated(),
        }

    # 2. Before-request CSRF protection middleware
    @app.before_request
    def verify_csrf():
        # Safe HTTP methods do not change state
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return None

        # Check configuration flags
        csrf_enabled = app.config.get("CSRF_ENABLED", True) and app.config.get("WTF_CSRF_ENABLED", True)
        if not csrf_enabled:
            return None

        # Allow testing bypass if configured
        if app.config.get("TESTING") and not app.config.get("CSRF_ENABLED", False):
            return None

        # Extract token from header, form, or JSON body
        token = request.headers.get("X-CSRF-Token")
        if not token and request.form:
            token = request.form.get("csrf_token")
        if not token and request.is_json:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                token = body.get("csrf_token")

        if not token or not validate_csrf_token(token):
            if (
                request.path.startswith("/api/")
                or request.is_json
                or request.headers.get("Accept") == "application/json"
            ):
                return jsonify({
                    "success": False,
                    "error": "Forbidden",
                    "message": "CSRF verification failed: missing or invalid CSRF token."
                }), 403
            
            # For HTML forms, render login with an error message
            from flask import render_template
            return render_template(
                "login.html",
                error="Security verification failed. Please try submitting again.",
                active_page="login"
            ), 403

        return None

    # 3. After-request cache-control headers to prevent post-logout back-button caching
    @app.after_request
    def apply_security_headers(response):
        if is_authenticated() or request.path in ("/login", "/logout") or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
