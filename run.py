"""
Smart Attendance Management System - Application Entry Point.
Starts the local Flask development server.
"""

from app import create_app

# Instantiate application using development configuration
app = create_app("development")

if __name__ == "__main__":
    print("=" * 60)
    print("IoT-Based Smart Attendance Management System - Backend Server")
    print("=" * 60)
    print("Server URL:     http://127.0.0.1:5000")
    print("Health Check:   http://127.0.0.1:5000/api/health")
    print("Environment:    Development (debug=True)")
    print("=" * 60)
    # WARNING: debug=True is strictly for local development.
    # It must be disabled (False) in production to avoid exposing internal debuggers!
    app.run(host="127.0.0.1", port=5000, debug=True)
