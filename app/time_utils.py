"""
Time and Timezone Utilities for Smart Attendance Management System.
Configured for Asia/Kolkata (UTC+05:30) timezone.
Provides controllable time mocking for deterministic automated testing.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    KOLKATA_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

# Internal mock/frozen time storage for deterministic testing
_frozen_time: Optional[datetime] = None


def set_frozen_time(dt: Optional[datetime]) -> None:
    """
    Sets a simulated current time for deterministic testing.
    If naive datetime is provided, it is assumed to be in Asia/Kolkata timezone.
    
    :param dt: Datetime instance, or None to clear frozen time.
    """
    global _frozen_time
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=KOLKATA_TZ)
    _frozen_time = dt


def clear_frozen_time() -> None:
    """
    Clears any frozen time, returning to live wall-clock system time.
    """
    global _frozen_time
    _frozen_time = None


def get_now_kolkata() -> datetime:
    """
    Returns current datetime in Asia/Kolkata timezone.
    If frozen time is configured, returns frozen time in Asia/Kolkata.
    """
    if _frozen_time is not None:
        return _frozen_time.astimezone(KOLKATA_TZ)
    return datetime.now(KOLKATA_TZ)


def parse_session_end_datetime(session_date: str, end_time: Optional[str]) -> Optional[datetime]:
    """
    Parses a session date (YYYY-MM-DD) and end time (HH:MM) into a timezone-aware
    Asia/Kolkata datetime.
    
    :param session_date: Date string formatted as 'YYYY-MM-DD'.
    :param end_time: Time string formatted as 'HH:MM'.
    :return: Timezone-aware datetime in Asia/Kolkata or None if end_time is not provided or invalid.
    """
    if not session_date or not end_time or not isinstance(end_time, str):
        return None
    
    date_str = session_date.strip()
    time_str = end_time.strip()
    
    if not date_str or not time_str:
        return None
        
    try:
        combined_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return combined_naive.replace(tzinfo=KOLKATA_TZ)
    except ValueError:
        return None


def is_session_expired(session_date: str, end_time: Optional[str]) -> bool:
    """
    Evaluates whether a session has reached or passed its end time.
    Historical sessions without an end_time (None/empty) are not expired by this rule.
    
    :param session_date: Date string formatted as 'YYYY-MM-DD'.
    :param end_time: Optional time string formatted as 'HH:MM'.
    :return: True if session end time is reached or passed, False otherwise.
    """
    expiry_dt = parse_session_end_datetime(session_date, end_time)
    if expiry_dt is None:
        return False
    
    now = get_now_kolkata()
    return now >= expiry_dt
