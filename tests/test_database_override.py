"""
Automated regression tests for the development database path override (DATABASE_PATH).
Verifies:
1. No override preserves default development database path.
2. Relative override resolves against project root (BASE_DIR).
3. Absolute override resolves correctly.
4. Empty override preserves default path.
5. Whitespace-only override preserves default path.
6. Development application uses the configured override with full auth/CSRF enabled.
7. TestingConfig remains strictly unchanged and unaffected by DATABASE_PATH.
8. Database connection mechanism automatically creates non-existent parent directories.
9. Invalid directory paths fail clearly with ValueError (never silently fall back).
10. ProductionConfig remains strictly pinned to default attendance.db.
"""

from pathlib import Path
import pytest

from app.config import (
    BASE_DIR,
    DATABASE_DIR,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    get_config,
    resolve_development_database_path,
)
from app import create_app
from app.database import get_db_connection, close_db_connection


# ==============================================================================
# 1. PATH RESOLUTION UNIT TESTS
# ==============================================================================

def test_1_no_override_preserves_default(monkeypatch):
    """TEST 1: When DATABASE_PATH is not set in environment, preserve existing default."""
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    resolved = resolve_development_database_path()
    assert resolved == DATABASE_DIR / "attendance.db"
    assert str(resolved).endswith("attendance.db")
    assert resolved.is_absolute()


def test_2_relative_override_resolves_against_base_dir(monkeypatch):
    """TEST 2: Relative override path resolves relative to BASE_DIR, not CWD."""
    relative_path_str = "database/manual_test/attendance_manual_test.db"
    monkeypatch.setenv("DATABASE_PATH", relative_path_str)

    resolved = resolve_development_database_path()
    expected = (BASE_DIR / relative_path_str).resolve()

    assert resolved == expected
    assert resolved.is_absolute()
    assert "manual_test" in str(resolved)
    assert str(resolved).endswith("attendance_manual_test.db")


def test_3_absolute_override_resolves_correctly(monkeypatch, tmp_path):
    """TEST 3: Absolute override path resolves correctly to the specified absolute path."""
    abs_path = (tmp_path / "isolated_external" / "test.db").resolve()
    monkeypatch.setenv("DATABASE_PATH", str(abs_path))

    resolved = resolve_development_database_path()
    assert resolved == abs_path
    assert resolved.is_absolute()


def test_4_empty_override_preserves_default(monkeypatch):
    """TEST 4: Empty string in DATABASE_PATH preserves the default development database."""
    monkeypatch.setenv("DATABASE_PATH", "")
    resolved = resolve_development_database_path()
    assert resolved == DATABASE_DIR / "attendance.db"


def test_5_whitespace_only_override_preserves_default(monkeypatch):
    """TEST 5: Whitespace-only string in DATABASE_PATH preserves the default development database."""
    monkeypatch.setenv("DATABASE_PATH", "   \t  \n  ")
    resolved = resolve_development_database_path()
    assert resolved == DATABASE_DIR / "attendance.db"


def test_6_directory_path_raises_value_error(monkeypatch):
    """TEST 6: Override path pointing to a directory fails clearly with ValueError."""
    # Trailing slash
    monkeypatch.setenv("DATABASE_PATH", "database/manual_test/")
    with pytest.raises(ValueError) as exc_info:
        resolve_development_database_path()
    assert "directory" in str(exc_info.value).lower()

    # Existing directory without trailing slash
    monkeypatch.setenv("DATABASE_PATH", "database")
    with pytest.raises(ValueError) as exc_info:
        resolve_development_database_path()
    assert "directory" in str(exc_info.value).lower()


# ==============================================================================
# 2. FLASK APPLICATION INTEGRATION TESTS
# ==============================================================================

def test_7_development_application_uses_configured_override(monkeypatch, tmp_path):
    """
    TEST 7: Development application factory applies the DATABASE_PATH override
    while strictly keeping development security invariants (LOGIN_DISABLED=False, CSRF_ENABLED=True).
    """
    override_db = tmp_path / "dev_override_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(override_db))

    app = create_app("development")
    try:
        assert app.config["DATABASE_PATH"] == override_db.resolve()
        assert app.config["DEBUG"] is True
        assert app.config["LOGIN_DISABLED"] is False
        assert app.config["CSRF_ENABLED"] is True
        assert app.config["WTF_CSRF_ENABLED"] is True
        # Verify database schema was initialized at the override path
        assert override_db.exists()
        assert override_db.stat().st_size > 0
    finally:
        # Clean teardown
        pass


def test_8_testing_config_remains_strictly_unchanged(monkeypatch, tmp_path):
    """
    TEST 8: TestingConfig must NOT be affected by DATABASE_PATH environment override.
    It must continue using its dedicated test database and existing test invariants.
    """
    override_db = tmp_path / "should_not_affect_testing.db"
    monkeypatch.setenv("DATABASE_PATH", str(override_db))

    test_cfg = get_config("testing")
    assert test_cfg.DATABASE_PATH == DATABASE_DIR / "test_attendance.db"
    assert test_cfg.LOGIN_DISABLED is True
    assert test_cfg.CSRF_ENABLED is False
    assert test_cfg.WTF_CSRF_ENABLED is False
    assert test_cfg.TESTING is True

    # Also test via create_app("testing")
    app = create_app("testing")
    assert app.config["DATABASE_PATH"] == DATABASE_DIR / "test_attendance.db"
    assert app.config["LOGIN_DISABLED"] is True


def test_9_production_config_not_redirected(monkeypatch, tmp_path):
    """
    TEST 9: ProductionConfig must NOT be redirected by DATABASE_PATH environment override.
    """
    override_db = tmp_path / "should_not_affect_prod.db"
    monkeypatch.setenv("DATABASE_PATH", str(override_db))
    monkeypatch.setenv("SECRET_KEY", "secure-production-random-token-xyz-1234567890")

    prod_cfg = get_config("production")
    assert prod_cfg.DATABASE_PATH == DATABASE_DIR / "attendance.db"


def test_10_parent_directory_creation_on_connection(tmp_path):
    """
    TEST 10: get_db_connection automatically creates non-existent parent directories
    when opening an isolated database.
    """
    deep_path = tmp_path / "sub1" / "sub2" / "test_auto_dir.db"
    assert not deep_path.parent.exists()

    conn = get_db_connection(deep_path)
    try:
        assert deep_path.parent.exists()
        assert deep_path.parent.is_dir()
    finally:
        close_db_connection(conn)
