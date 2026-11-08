"""
Background Scheduler for Automatic Class Session Expiration.
Monitors active sessions and deactivates them when their configured end time is reached.
Provides dual guarantee: background timer-driven check + atomic JIT request checks.
"""

import atexit
import logging
import os
import sqlite3
import threading
from typing import Optional, Dict, Any, Union
from pathlib import Path
from flask import Flask

from app.database import get_db_connection, close_db_connection
from app.time_utils import is_session_expired

logger = logging.getLogger(__name__)


def check_and_deactivate_expired_sessions(db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """
    Checks if the currently active class session has reached or passed its end time.
    If expired, atomically sets active = 0 and commits.
    
    :param db_path: Optional path to SQLite database.
    :return: Dict of deactivated session details, or None if no session was deactivated.
    """
    if db_path is None:
        try:
            from flask import current_app, has_app_context
            if has_app_context():
                db_path = current_app.config.get("DATABASE_PATH")
        except Exception:
            pass

    conn = None
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        
        # 1. Fetch current active session
        cursor.execute(
            """
            SELECT id, course_name, session_date, start_time, end_time 
            FROM class_sessions 
            WHERE active = 1 
            LIMIT 1;
            """
        )
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        # 2. Check if the active session has expired
        session_id = row["id"]
        course_name = row["course_name"]
        session_date = row["session_date"]
        end_time = row["end_time"]
        
        if is_session_expired(session_date, end_time):
            # 3. Atomically deactivate the session
            conn.execute("BEGIN IMMEDIATE;")
            cursor.execute(
                """
                UPDATE class_sessions 
                SET active = 0 
                WHERE id = ? AND active = 1;
                """,
                (session_id,)
            )
            conn.commit()
            logger.info(
                f"Auto-deactivated expired session ID {session_id} "
                f"({course_name}, Date: {session_date}, End Time: {end_time})"
            )
            return {
                "id": session_id,
                "course_name": course_name,
                "session_date": session_date,
                "end_time": end_time
            }
            
        return None

    except sqlite3.Error as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error(f"Error checking/deactivating expired sessions: {e}")
        return None
    finally:
        close_db_connection(conn)


class SessionExpiryScheduler:
    """
    Singleton background scheduler daemon thread.
    Monitors active class sessions periodically and deactivates expired ones.
    """
    _instance: Optional["SessionExpiryScheduler"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SessionExpiryScheduler, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[Union[str, Path]] = None, interval: float = 5.0):
        if getattr(self, "_initialized", False):
            return
        self.db_path = db_path
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._thread_lock = threading.Lock()
        self._initialized = True

    def start(self, db_path: Optional[Union[str, Path]] = None) -> bool:
        """
        Starts the background scheduler thread if not already running.
        
        :param db_path: Optional database path override.
        :return: True if started or already running, False otherwise.
        """
        with self._thread_lock:
            if db_path is not None:
                self.db_path = db_path

            if self._thread is not None and self._thread.is_alive():
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="SessionExpirySchedulerThread",
                daemon=True
            )
            self._thread.start()
            logger.info(f"Session expiry background scheduler started (interval={self.interval}s).")
            return True

    def stop(self, timeout: float = 2.0) -> bool:
        """
        Stops the background scheduler thread gracefully.
        
        :param timeout: Seconds to wait for thread termination.
        :return: True if stopped, False if timeout expired.
        """
        with self._thread_lock:
            if self._thread is None or not self._thread.is_alive():
                return True

            self._stop_event.set()
            self._thread.join(timeout=timeout)
            is_dead = not self._thread.is_alive()
            if is_dead:
                self._thread = None
                logger.info("Session expiry background scheduler stopped.")
            else:
                logger.warning("Session expiry scheduler thread did not terminate within timeout.")
            return is_dead

    def is_running(self) -> bool:
        """
        Returns whether the scheduler thread is actively running.
        """
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        """
        Main scheduler loop. Runs until stop_event is signaled.
        """
        while not self._stop_event.is_set():
            try:
                check_and_deactivate_expired_sessions(self.db_path)
            except Exception as e:
                logger.error(f"Unexpected error in session expiry scheduler loop: {e}")
            
            # Wait for next interval or stop signal
            if self._stop_event.wait(timeout=self.interval):
                break


_scheduler = SessionExpiryScheduler()


def get_scheduler() -> SessionExpiryScheduler:
    """Returns the singleton scheduler instance."""
    return _scheduler


def init_scheduler(app: Flask) -> None:
    """
    Initializes and starts the session expiry scheduler within a Flask application lifecycle.
    Deduplicates against Werkzeug reloader child processes and skips in test mode unless forced.
    """
    # In testing mode, do not auto-start background thread unless explicitly configured
    if app.config.get("TESTING") and not app.config.get("START_SCHEDULER"):
        return

    # In debug mode with Werkzeug reloader, only start inside the main worker process
    if app.debug:
        if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            logger.debug("Skipping scheduler startup in Werkzeug reloader parent process.")
            return

    db_path = app.config.get("DATABASE_PATH")
    scheduler = get_scheduler()
    scheduler.start(db_path=db_path)

    # Register clean shutdown on interpreter exit
    atexit.register(scheduler.stop)
