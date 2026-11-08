"""
Database schema definitions and DDL scripts for Smart Attendance System.
Defines SQL statements for tables, constraints, foreign keys, and indexes.
"""

# SQL statements to create all 3 core tables
CREATE_TABLES_SQL = """
-- Table 1: Students
-- Stores registered students and their unique RFID card identifiers.
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_number TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    card_uid TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- Table 2: Class Sessions
-- Stores classroom/lecture sessions for courses. Only one session should be active at a time.
CREATE TABLE IF NOT EXISTS class_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    session_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Table 3: Attendance Records
-- Stores attendance records linked to a specific student and a specific class session.
-- Constraint UNIQUE(student_id, session_id) guarantees no duplicate attendance in the same session.
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    scanned_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Present',
    source TEXT NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE,
    UNIQUE(student_id, session_id)
);

-- Table 4: Administrator Users
-- Stores authenticated administrator accounts with secure hashed passwords.
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- Table 5: Google Sheets Synchronization Outbox Queue
-- Stores local outbox records for asynchronous synchronization to Google Sheets.
CREATE TABLE IF NOT EXISTS sheets_sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL UNIQUE,
    sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending', 'in_progress', 'synced', 'failed', 'dead_letter')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    synced_at TEXT,
    FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE
);
"""

# SQL statements to create indexes for optimized queries
CREATE_INDEXES_SQL = """
-- Optimize administrator lookup by username during login
CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(username);

-- Optimize student lookup by RFID card UID (used on every card scan)
CREATE INDEX IF NOT EXISTS idx_students_card_uid ON students(card_uid);

-- Optimize student lookup by roll number
CREATE INDEX IF NOT EXISTS idx_students_roll_number ON students(roll_number);

-- Optimize finding active class session (WHERE active = 1)
CREATE INDEX IF NOT EXISTS idx_sessions_active ON class_sessions(active);

-- Database-level constraint: Ensure only ONE session can be active (active = 1) at any time.
-- Inactive sessions (active = 0) are excluded from this unique index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_single_active_session ON class_sessions(active) WHERE active = 1;

-- Optimize querying sessions by date
CREATE INDEX IF NOT EXISTS idx_sessions_date ON class_sessions(session_date);

-- Optimize duplicate attendance validation (WHERE student_id = ? AND session_id = ?)
CREATE INDEX IF NOT EXISTS idx_attendance_student_session ON attendance(student_id, session_id);

-- Optimize daily attendance queries and date-based reports
CREATE INDEX IF NOT EXISTS idx_attendance_scanned_at ON attendance(scanned_at);

-- Optimize polling for pending and retry-eligible synchronization records
CREATE INDEX IF NOT EXISTS idx_sync_queue_status_retry ON sheets_sync_queue(sync_status, next_retry_at);

-- Optimize lookup of sync status by attendance_id
CREATE INDEX IF NOT EXISTS idx_sync_queue_attendance_id ON sheets_sync_queue(attendance_id);

-- Optimize recovering expired worker processing leases
CREATE INDEX IF NOT EXISTS idx_sync_queue_lease ON sheets_sync_queue(sync_status, lease_expires_at);
"""

# Standalone DDL for sheets_sync_queue migration
CREATE_SHEETS_SYNC_QUEUE_SQL = """
CREATE TABLE IF NOT EXISTS sheets_sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL UNIQUE,
    sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending', 'in_progress', 'synced', 'failed', 'dead_letter')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    synced_at TEXT,
    FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE
);
"""

CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_sync_queue_status_retry ON sheets_sync_queue(sync_status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_sync_queue_attendance_id ON sheets_sync_queue(attendance_id);
CREATE INDEX IF NOT EXISTS idx_sync_queue_lease ON sheets_sync_queue(sync_status, lease_expires_at);
"""
