"""
Flask Application Factory for Smart Attendance Management System.
Configures settings, database lifecycle, blueprints, and global JSON error handlers.
"""

from typing import Optional
from flask import Flask, jsonify
from app.config import get_config
from app.database import init_db, close_db
from app.routes.health import health_bp
from app.routes.students import students_bp
from app.routes.sessions import sessions_bp
from app.routes.attendance import attendance_bp
from app.routes.reports import reports_bp
from app.routes.admin import admin_bp
from app.routes.views import views_bp
from app.routes.auth import auth_bp
from app.auth import init_security_extensions
from app.cli import init_cli
from app.scheduler import init_scheduler


def register_error_handlers(app: Flask) -> None:
    """
    Registers custom JSON error handlers so API consumers receive clean JSON
    instead of default HTML error pages.
    """
    @app.errorhandler(401)
    def unauthorized_error(error):
        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "message": "Administrator authentication is required."
        }), 401

    @app.errorhandler(403)
    def forbidden_error(error):
        return jsonify({
            "success": False,
            "error": "Forbidden",
            "message": "Access forbidden: security check failed."
        }), 403

    @app.errorhandler(404)
    def not_found_error(error):
        return jsonify({
            "success": False,
            "error": "Not Found",
            "message": "The requested API endpoint was not found on this server."
        }), 404

    @app.errorhandler(405)
    def method_not_allowed_error(error):
        return jsonify({
            "success": False,
            "error": "Method Not Allowed",
            "message": "The HTTP method used is not allowed for this endpoint."
        }), 405

    @app.errorhandler(500)
    def internal_server_error(error):
        app.logger.error(f"Internal Server Error: {error}")
        return jsonify({
            "success": False,
            "error": "Internal Server Error",
            "message": "An internal server error occurred. Please contact the administrator."
        }), 500


def create_app(config_name: Optional[str] = None) -> Flask:
    """
    Application Factory Pattern:
    Constructs and configures a Flask application instance.
    
    :param config_name: 'development', 'testing', etc. (Defaults to 'development')
    :return: Configured Flask application instance
    """
    app = Flask(__name__)
    
    # 1. Load application configuration
    config_class = get_config(config_name)
    app.config.from_object(config_class)
    
    # 2. Ensure database schema is initialized safely (idempotent, does not overwrite existing data)
    db_path = app.config.get("DATABASE_PATH")
    init_db(db_path)
    
    # 3. Register database connection cleanup on request teardown
    app.teardown_appcontext(close_db)
    
    # 4. Register API & Web Blueprints
    app.register_blueprint(views_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(students_bp, url_prefix="/api")
    app.register_blueprint(sessions_bp, url_prefix="/api")
    app.register_blueprint(attendance_bp, url_prefix="/api")
    app.register_blueprint(reports_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")
    
    # 5. Initialize Security Extensions (CSRF, Cache-Control, Template Context)
    init_security_extensions(app)

    # 6. Initialize Custom CLI Commands ('flask create-admin')
    init_cli(app)

    # 7. Register global error handlers (401, 403, 404, 405, 500)
    register_error_handlers(app)
    
    # 8. Initialize Background Scheduler for Session Auto-Expiration
    init_scheduler(app)
    
    return app
