"""
Global Pytest Configuration and Test Fixtures.
Provides deterministic time mocking for tests to prevent wall-clock date drift.
"""

from datetime import datetime
import pytest
from app.time_utils import set_frozen_time, clear_frozen_time, KOLKATA_TZ


@pytest.fixture(autouse=True)
def mock_system_time():
    """
    Freezes test time to 2026-11-07 10:15:00 Asia/Kolkata by default.
    Ensures tests using 2026-11-07 dates remain deterministic regardless of wall-clock time.
    Individual tests can override this by calling set_frozen_time() directly.
    """
    set_frozen_time(datetime(2026, 11, 7, 10, 15, tzinfo=KOLKATA_TZ))
    yield
    clear_frozen_time()
