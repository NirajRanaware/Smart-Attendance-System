"""
CLI Commands Module for Smart Attendance Management System.
Provides administrative commands including 'flask create-admin'.
"""

from datetime import datetime, timezone
import click
from flask import Flask
from app.auth import hash_password, validate_password_strength
from app.database import get_db_connection


def init_cli(app: Flask) -> None:
    """Registers custom CLI commands with the Flask application."""

    @app.cli.command("create-admin")
    @click.option(
        "--username",
        prompt="Administrator Username",
        help="Unique username for the administrator account."
    )
    def create_admin_command(username):
        """
        Create a new administrator account with a hashed password in SQLite.
        Prompts securely for password without exposing credentials on CLI.
        """
        # 1. Clean and validate username
        cleaned_username = username.strip()
        if not cleaned_username:
            click.echo("Error: Username cannot be empty.", err=True)
            return
        
        if len(cleaned_username) < 3 or len(cleaned_username) > 50:
            click.echo("Error: Username must be between 3 and 50 characters long.", err=True)
            return

        # 2. Interactive hidden password prompt with mandatory confirmation
        password = click.prompt(
            "Password",
            hide_input=True,
            confirmation_prompt=True
        )

        # 3. Validate password strength
        is_strong, err_msg = validate_password_strength(password)
        if not is_strong:
            click.echo(f"Error: {err_msg}", err=True)
            return

        # 4. Insert into database
        db_path = app.config.get("DATABASE_PATH")
        conn = get_db_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM admin_users WHERE username = ?;",
                (cleaned_username,)
            )
            existing = cursor.fetchone()
            if existing:
                click.echo(f"Error: Administrator with username '{cleaned_username}' already exists.", err=True)
                return

            # Hash password securely
            pwd_hash = hash_password(password)
            now_utc = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT INTO admin_users (username, password_hash, role, active, created_at)
                VALUES (?, ?, 'admin', 1, ?);
                """,
                (cleaned_username, pwd_hash, now_utc)
            )
            conn.commit()
            click.echo(f"Success: Administrator user '{cleaned_username}' created successfully.")
        except Exception as e:
            conn.rollback()
            click.echo(f"Error: Failed to create administrator in database: {e}", err=True)
        finally:
            conn.close()

    @app.cli.command("run-sheets-worker")
    @click.option(
        "--once",
        is_flag=True,
        help="Run a single synchronization batch and exit immediately."
    )
    @click.option(
        "--interval",
        type=int,
        default=None,
        help="Polling interval in seconds (default: from configuration or 10s)."
    )
    @click.option(
        "--batch-size",
        type=int,
        default=None,
        help="Maximum records to process per batch (default: from configuration or 20)."
    )
    def run_sheets_worker_command(once, interval, batch_size):
        """
        Runs the background Google Sheets synchronization worker.
        Polls the SQLite outbox queue, claims records with leased concurrency locks,
        verifies duplicate prevention in Google Sheets, and appends attendance rows.
        """
        from app.services.sheets_worker import SheetsSyncWorker
        click.echo("Initializing Google Sheets Synchronization Worker...")
        worker = SheetsSyncWorker(
            app=app,
            poll_interval=interval,
            batch_size=batch_size
        )
        if once:
            click.echo("Running single batch synchronization...")
            res = worker.run(once=True)
            click.echo(f"Result: {res.get('message', res)}")
        else:
            click.echo("Starting continuous sync worker loop. Press Ctrl+C to terminate.")
            worker.run(once=False)
