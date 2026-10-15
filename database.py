import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime
from werkzeug.security import generate_password_hash

from config import DB_PATH, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, REGISTRATION_FEE


@contextmanager
def get_db():
    """Yield a connection that is committed, rolled back, and closed.

    sqlite3's own `with conn:` commits but leaves the connection open, so every
    request slowly added handles. sqlite3.Row is an independent copy of the row,
    so data fetched here is still readable after the close.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Passenger keeps several worker processes alive against this one file. WAL
    # lets readers work while a writer is committing instead of failing outright.
    conn.execute("PRAGMA journal_mode = WAL")
    # Off by default in SQLite; without this the ON DELETE CASCADE clauses in
    # the schema do nothing and child rows outlive the records they belong to.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id                 TEXT PRIMARY KEY,
            full_name          TEXT NOT NULL,
            email              TEXT NOT NULL,
            phone              TEXT NOT NULL,
            course             TEXT NOT NULL,
            study_mode         TEXT NOT NULL,
            previous_education TEXT,
            status             TEXT NOT NULL DEFAULT 'pending',
            notes              TEXT,
            created_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            category       TEXT NOT NULL,
            original_name  TEXT NOT NULL,
            stored_name    TEXT NOT NULL,
            mime_type      TEXT,
            size_bytes     INTEGER,
            uploaded_at    TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS departments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS courses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            name          TEXT NOT NULL,
            code          TEXT,
            qualification TEXT,
            duration      TEXT,
            exam_body     TEXT,
            created_at    TEXT NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS fee_structures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name   TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            term          TEXT NOT NULL,
            tuition_fee   REAL NOT NULL DEFAULT 0,
            exam_fee      REAL NOT NULL DEFAULT 0,
            other_fee     REAL NOT NULL DEFAULT 0,
            other_items   TEXT,
            created_at    TEXT NOT NULL,
            UNIQUE (course_name, academic_year, term)
        );

        CREATE TABLE IF NOT EXISTS fee_payments (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            amount         REAL NOT NULL,
            category       TEXT NOT NULL,
            term           TEXT,
            academic_year  TEXT,
            method         TEXT,
            reference      TEXT,
            status         TEXT NOT NULL DEFAULT 'confirmed',
            recorded_by    TEXT,
            paid_at        TEXT NOT NULL,
            created_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS teachers (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name            TEXT NOT NULL,
            email                TEXT,
            phone                TEXT,
            national_id          TEXT,
            qualification        TEXT,
            username             TEXT UNIQUE NOT NULL,
            password_hash        TEXT NOT NULL,
            initial_password     TEXT,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            created_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS teacher_documents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id    INTEGER NOT NULL,
            category      TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_name   TEXT NOT NULL,
            mime_type     TEXT,
            size_bytes    INTEGER,
            uploaded_at   TEXT NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS classes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            course        TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            study_mode    TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            UNIQUE (course, academic_year, study_mode)
        );

        CREATE TABLE IF NOT EXISTS teacher_classes (
            teacher_id INTEGER NOT NULL,
            class_id   INTEGER NOT NULL,
            PRIMARY KEY (teacher_id, class_id),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS class_subjects (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id   INTEGER NOT NULL,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id    INTEGER NOT NULL,
            student_id  TEXT NOT NULL,
            attend_date TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'present',
            method      TEXT,
            marked_by   INTEGER,
            created_at  TEXT NOT NULL,
            UNIQUE (class_id, student_id, attend_date),
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exams (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id      INTEGER NOT NULL,
            subject_id    INTEGER,
            title         TEXT NOT NULL,
            term          TEXT,
            academic_year TEXT,
            max_score     REAL NOT NULL DEFAULT 100,
            created_by    INTEGER,
            created_at    TEXT NOT NULL,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exam_results (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id    INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            score      REAL,
            grade      TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (exam_id, student_id),
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS site_content (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT UNIQUE NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            meta        TEXT,
            updated_at  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS progression_requests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            kind           TEXT NOT NULL DEFAULT 'advance',
            from_period    TEXT NOT NULL,
            to_period      TEXT,
            academic_year  TEXT,
            reason         TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            decision       TEXT,
            decision_notes TEXT,
            new_course     TEXT,
            new_study_mode TEXT,
            return_period  TEXT,
            requested_by   TEXT NOT NULL DEFAULT 'student',
            requested_at   TEXT NOT NULL,
            decided_at     TEXT,
            decided_by     TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
        );
        """)

        _ensure_columns(db, "applications", {
            "academic_year":            "TEXT",
            "admission_number":         "TEXT",
            "admitted_at":              "TEXT",
            "registration_paid":        "INTEGER NOT NULL DEFAULT 0",
            "registration_paid_at":     "TEXT",
            "student_password_hash":    "TEXT",
            "student_initial_password": "TEXT",
            "must_change_password":     "INTEGER NOT NULL DEFAULT 0",
            "surname":                  "TEXT",
            "middle_name":              "TEXT",
            "first_name":               "TEXT",
            "nationality":              "TEXT",
            "date_of_birth":            "TEXT",
            "medical_condition":        "TEXT",
            "start_date":               "TEXT",
            "end_date":                 "TEXT",
            "next_of_kin_name":         "TEXT",
            "next_of_kin_relationship": "TEXT",
            "sponsor_name":             "TEXT",
            "sponsor_organisation":     "TEXT",
            "sponsor_contact_person":   "TEXT",
            "student_status":           "TEXT NOT NULL DEFAULT 'active'",
            "current_period":           "TEXT",
        })
        _ensure_columns(db, "courses", {
            "code":             "TEXT",
            "progression_mode": "TEXT NOT NULL DEFAULT 'term'",
            "total_periods":    "INTEGER NOT NULL DEFAULT 3",
            # Registration fee used to be one global number; courses bill 1000,
            # 2000 or 3000 depending on the programme.
            "registration_fee": f"REAL NOT NULL DEFAULT {REGISTRATION_FEE:g}",
        })
        _ensure_columns(db, "fee_structures", {"other_items": "TEXT"})

        row = db.execute("SELECT COUNT(*) AS c FROM admins").fetchone()
        if row["c"] == 0:
            db.execute(
                "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
                (
                    DEFAULT_ADMIN_USERNAME,
                    generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                    datetime.utcnow().isoformat(),
                ),
            )

        _seed_departments_and_courses(db)
        _backfill_course_codes(db)
        _backfill_academic_year(db)
        _ensure_indexes(db)


def _ensure_indexes(db):
    """Index the columns the pages filter on.

    SQLite does not index foreign-key columns automatically, so every
    "give me this student's payments / this class's register" was a full
    table scan. CREATE INDEX IF NOT EXISTS makes this safe to re-run.
    """
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_fee_payments_app ON fee_payments(application_id)",
        "CREATE INDEX IF NOT EXISTS idx_fee_payments_status ON fee_payments(status)",
        "CREATE INDEX IF NOT EXISTS idx_documents_app ON documents(application_id)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_class_date ON attendance(class_id, attend_date)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id)",
        "CREATE INDEX IF NOT EXISTS idx_exam_results_student ON exam_results(student_id)",
        "CREATE INDEX IF NOT EXISTS idx_exams_class ON exams(class_id)",
        "CREATE INDEX IF NOT EXISTS idx_class_subjects_class ON class_subjects(class_id)",
        "CREATE INDEX IF NOT EXISTS idx_progression_app ON progression_requests(application_id)",
        "CREATE INDEX IF NOT EXISTS idx_progression_status ON progression_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)",
        "CREATE INDEX IF NOT EXISTS idx_applications_admission ON applications(admission_number)",
        "CREATE INDEX IF NOT EXISTS idx_applications_course_year ON applications(course, academic_year)",
    ):
        db.execute(statement)


def _ensure_columns(db, table, columns):
    """Add columns that are missing from an existing table (simple migration)."""
    existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


DEFAULT_COURSES = [
    ("I.C.T Courses", "ICT"),
    ("Hairdressing & Beauty Therapy", "BEAUTY"),
    ("Fashion & Design", "FASHION"),
    ("Business Courses", "BUSINESS"),
    ("Social Work & Community Development", "SOCIAL"),
    ("Food & Beverage", "FOOD"),
    ("Languages", "LANG"),
    ("Video & Photography", "MEDIA"),
    ("Nursing Aid / Care Giving", "NURSING"),
]


def _seed_departments_and_courses(db):
    """Create a default department and seed the original course list once."""
    if db.execute("SELECT COUNT(*) AS c FROM courses").fetchone()["c"] > 0:
        return

    now = datetime.utcnow().isoformat()
    dept = db.execute(
        "SELECT id FROM departments ORDER BY id LIMIT 1"
    ).fetchone()
    if not dept:
        db.execute(
            "INSERT INTO departments (name, description, created_at) VALUES (?, ?, ?)",
            (
                "General Programmes",
                "Default department. Rename it or add your own.",
                now,
            ),
        )
        dept = db.execute(
            "SELECT id FROM departments ORDER BY id LIMIT 1"
        ).fetchone()

    for name, code in DEFAULT_COURSES:
        db.execute(
            """INSERT INTO courses
               (department_id, name, code, qualification, duration, exam_body, created_at)
               VALUES (?, ?, ?, '', '', '', ?)""",
            (dept["id"], name, code, now),
        )


def _course_code(name):
    """Derive a short uppercase code from a course name."""
    key = (name or "").strip()
    for default_name, default_code in DEFAULT_COURSES:
        if default_name.lower() == key.lower():
            return default_code
    words = [w for w in re.split(r"[^A-Za-z]+", key) if w]
    if not words:
        return "GEN"
    if len(words) == 1:
        return words[0][:5].upper()
    return "".join(w[0] for w in words)[:6].upper()


def _backfill_course_codes(db):
    rows = db.execute(
        "SELECT id, name FROM courses WHERE code IS NULL OR code = ''"
    ).fetchall()
    for row in rows:
        db.execute(
            "UPDATE courses SET code = ? WHERE id = ?",
            (_course_code(row["name"]), row["id"]),
        )


def _backfill_academic_year(db):
    db.execute(
        "UPDATE applications SET academic_year = substr(created_at, 1, 4) "
        "WHERE academic_year IS NULL"
    )
