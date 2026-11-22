"""
Pytest Fixtures and Orchestration for Stage 8.4 End-to-End Simulation.
Runs live Flask server instances on 127.0.0.1 with isolated temporary databases.
"""

from datetime import datetime, timezone
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
import urllib.error
import pytest
from werkzeug.serving import make_server

from app import create_app
from app.database import init_db

# Constants
TEST_DEVICE_ID = "ESP32_ATTENDANCE_01"
TEST_API_KEY = "test-secret-key-esp32-valid-token-32b"
SECONDARY_DEVICE_ID = "ESP32_ATTENDANCE_02"
SECONDARY_API_KEY = "another-valid-secret-key-token-32b"

NATIVE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "native"))
BUILD_E2E_BAT = os.path.join(NATIVE_DIR, "build_e2e.bat")
E2E_RUNNER_EXE = os.path.join(NATIVE_DIR, "firmware_e2e_runner.exe")
PROD_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "database", "attendance.db"))


def find_free_port() -> int:
    """Finds an available TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


@pytest.fixture(scope="session", autouse=True)
def ensure_e2e_runner_built():
    """Ensures the native C++ E2E runner is compiled before tests execute."""
    if not os.path.exists(E2E_RUNNER_EXE):
        res = subprocess.run(["cmd.exe", "/c", BUILD_E2E_BAT], cwd=NATIVE_DIR, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to build firmware_e2e_runner.exe:\n{res.stdout}\n{res.stderr}"
    assert os.path.exists(E2E_RUNNER_EXE), f"Runner not found at {E2E_RUNNER_EXE}"


@pytest.fixture
def temp_db(tmp_path):
    """Creates a temporary, isolated SQLite database initialized with production schema."""
    db_file = tmp_path / "e2e_temp_attendance.db"
    init_db(db_file)
    yield str(db_file)

    # Teardown check: verify temporary db integrity
    if os.path.exists(db_file):
        conn = sqlite3.connect(db_file)
        res = conn.execute("PRAGMA integrity_check;").fetchall()
        conn.close()
        assert res == [("ok",)], f"Temporary DB integrity check failed: {res}"


@pytest.fixture
def live_server(temp_db, monkeypatch):
    """
    Spins up an actual live Flask application instance in a background thread
    bound strictly to 127.0.0.1 on an ephemeral port.
    Enforces real device authentication and real CSRF protection.
    """
    # Record initial state of production DB to ensure zero modifications
    prod_mtime_before = os.path.getmtime(PROD_DB_PATH) if os.path.exists(PROD_DB_PATH) else None

    port = find_free_port()
    keys_json = json.dumps({
        TEST_DEVICE_ID: TEST_API_KEY,
        SECONDARY_DEVICE_ID: SECONDARY_API_KEY
    })
    monkeypatch.setenv("DEVICE_API_KEYS", keys_json)

    app = create_app("testing")
    app.config["DATABASE_PATH"] = temp_db
    app.config["LOGIN_DISABLED"] = False   # Strict auth enabled
    app.config["CSRF_ENABLED"] = True      # Strict CSRF enabled
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["DEVICE_API_KEYS"] = {
        TEST_DEVICE_ID: TEST_API_KEY,
        SECONDARY_DEVICE_ID: SECONDARY_API_KEY
    }

    server = make_server("127.0.0.1", port, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Readiness check: wait up to 5 seconds for /api/health
    health_url = f"http://127.0.0.1:{port}/api/health"
    ready = False
    start_time = time.time()
    while time.time() - start_time < 5.0:
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.05)

    assert ready, f"Live Flask server failed to start on 127.0.0.1:{port} within timeout"

    server_info = {
        "host": "127.0.0.1",
        "port": port,
        "db_path": temp_db,
        "device_id": TEST_DEVICE_ID,
        "api_key": TEST_API_KEY,
        "secondary_device_id": SECONDARY_DEVICE_ID,
        "secondary_api_key": SECONDARY_API_KEY
    }

    yield server_info

    # Graceful shutdown
    server.shutdown()
    server_thread.join(timeout=3.0)

    # Verify production database was never modified
    if prod_mtime_before is not None and os.path.exists(PROD_DB_PATH):
        prod_mtime_after = os.path.getmtime(PROD_DB_PATH)
        assert prod_mtime_before == prod_mtime_after, "FATAL: Production database was modified during E2E test!"


def run_firmware_client(
    port: int,
    card_uid: str = "A1B2C3D4",
    device_id: str = TEST_DEVICE_ID,
    api_key: str = TEST_API_KEY,
    extra_args: list = None,
    timeout: int = 10
) -> dict:
    """
    Executes the actual compiled C++ firmware_e2e_runner.exe against the live server.
    Captures stdout, validates zero crash exit code, parses and returns the structured JSON output.
    """
    cmd = [
        E2E_RUNNER_EXE,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--uid", card_uid,
        "--device", device_id,
        "--key", api_key
    ]
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(cmd, cwd=NATIVE_DIR, capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, f"Firmware runner exited with code {proc.returncode}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    # Extract JSON line
    json_match = re.search(r"E2E_JSON_OUTPUT:(\{.*?\})", proc.stdout)
    assert json_match is not None, f"Could not find E2E_JSON_OUTPUT in runner stdout:\n{proc.stdout}"

    parsed = json.loads(json_match.group(1))
    parsed["raw_stdout"] = proc.stdout
    return parsed


# Database Seed Helpers
def seed_student(db_path: str, roll_number: str, full_name: str, card_uid: str, active: int = 1) -> int:
    """Seeds a student record into the temporary test database."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO students (roll_number, full_name, card_uid, active, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (roll_number, full_name, card_uid.upper(), active, now_utc)
    )
    conn.commit()
    student_id = cur.lastrowid
    conn.close()
    return student_id


def seed_session(
    db_path: str,
    course_name: str = "Computer Networks",
    session_date: str = "2026-11-22",
    start_time: str = "08:00",
    end_time: str = "20:00",
    active: int = 1
) -> int:
    """Seeds a class session into the temporary test database."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (course_name, session_date, start_time, end_time, active, now_utc)
    )
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id


def get_attendance_count(db_path: str, student_id: int = None) -> int:
    """Returns attendance row count in the temporary database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if student_id:
        cur.execute("SELECT COUNT(*) FROM attendance WHERE student_id = ?;", (student_id,))
    else:
        cur.execute("SELECT COUNT(*) FROM attendance;")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_sync_queue_records(db_path: str, attendance_id: int = None) -> list:
    """Returns rows from sheets_sync_queue."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if attendance_id:
        cur.execute("SELECT * FROM sheets_sync_queue WHERE attendance_id = ?;", (attendance_id,))
    else:
        cur.execute("SELECT * FROM sheets_sync_queue;")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
