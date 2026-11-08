"""
Automated tests for Flask application core, health check API, and JSON error handling.
Runs against an isolated temporary test database.
"""

from unittest.mock import patch
import pytest
from app import create_app
from app.database import init_db


@pytest.fixture
def app(tmp_path):
    """
    Creates an instance of the Flask application configured for testing.
    Uses an isolated temporary SQLite database so development data is untouched.
    """
    test_db_path = tmp_path / "test_health_app.db"
    
    # Create the test application
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's built-in HTTP test client."""
    return app.test_client()


def test_1_app_initializes_successfully(app):
    """TEST 1: Verify the Flask application instance initializes properly."""
    assert app is not None
    assert app.config["TESTING"] is True


def test_2_health_endpoint_returns_http_200(client):
    """TEST 2: Verify GET /api/health returns HTTP 200 OK status code."""
    response = client.get("/api/health")
    assert response.status_code == 200


def test_3_health_endpoint_returns_valid_json(client):
    """TEST 3: Verify GET /api/health returns response with application/json content type."""
    response = client.get("/api/health")
    assert response.content_type == "application/json"
    data = response.get_json()
    assert isinstance(data, dict)


def test_4_expected_response_fields_are_present(client):
    """TEST 4: Verify that all expected fields (success, message, database) are in the JSON response."""
    response = client.get("/api/health")
    data = response.get_json()
    assert "success" in data
    assert "message" in data
    assert "database" in data
    assert data["message"] == "Smart Attendance API is running"


def test_5_database_connection_is_verified(client):
    """TEST 5: Verify that the database connectivity check reports 'connected'."""
    response = client.get("/api/health")
    data = response.get_json()
    assert data["success"] is True
    assert data["database"] == "connected"


def test_6_404_requests_return_json(client):
    """TEST 6: Verify non-existent routes return HTTP 404 with JSON error structure."""
    response = client.get("/api/unknown_route_path")
    assert response.status_code == 404
    assert response.content_type == "application/json"
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Not Found"
    assert "message" in data


def test_7_unsupported_http_methods_return_json(client):
    """TEST 7: Verify unsupported HTTP methods (e.g. POST on /api/health) return HTTP 405 with JSON."""
    response = client.post("/api/health", json={"dummy": "data"})
    assert response.status_code == 405
    assert response.content_type == "application/json"
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Method Not Allowed"
    assert "message" in data


def test_8_database_failure_returns_503_json(client):
    """TEST 8: Verify database failure scenario returns HTTP 503 and 'disconnected' status."""
    with patch("app.routes.health.get_db") as mock_get_db:
        mock_get_db.side_effect = RuntimeError("Database connection lost")
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.content_type == "application/json"
        data = response.get_json()
        assert data["success"] is False
        assert data["database"] == "disconnected"
        assert "error" in data
