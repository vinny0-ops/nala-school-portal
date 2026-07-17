import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'school.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  recovery_code_hash TEXT
);
CREATE TABLE IF NOT EXISTS teachers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  recovery_code_hash TEXT,
  status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS teacher_subjects (
  teacher_id TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  PRIMARY KEY (teacher_id, subject_id)
);
CREATE TABLE IF NOT EXISTS students (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  form TEXT NOT NULL,
  stream TEXT DEFAULT 'A',
  sex TEXT DEFAULT 'M',
  parent_name TEXT DEFAULT '',
  parent_phone TEXT DEFAULT '',
  parent_email TEXT DEFAULT '',
  recovery_code_hash TEXT,
  status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS subjects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  abbr TEXT,
  code TEXT,
  sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS forms (
  name TEXT PRIMARY KEY,
  sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
  student_id TEXT NOT NULL,
  exam_type TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (student_id, exam_type, subject_id)
);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, body TEXT, author TEXT, date TEXT
);
CREATE TABLE IF NOT EXISTS fee_structure (
  form TEXT PRIMARY KEY,
  amount INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fee_custom_due (
  student_id TEXT PRIMARY KEY,
  amount INTEGER
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id TEXT, date TEXT, amount INTEGER, category TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT, actor TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts (
  key TEXT PRIMARY KEY, count INTEGER DEFAULT 0, locked_until TEXT
);
"""

DEFAULT_SUBJECTS = [
    ("Physics", "PHY", "030"), ("Chemistry", "CHEM", "031"), ("Basic Mathematics", "B/MATH", "041"),
    ("Biology", "BIO", "026"), ("Kiswahili", "KISW", "050"), ("Geography", "GEO", "055"),
    ("English Language", "ENGL", "022"), ("History", "HIST", "059"),
    ("Historia ya Tanzania na Maadili", "HIST TZ", "060"), ("Computer Science", "COMP SC", "098"),
    ("Business Studies", "B/STUDIES", "065"),
]
DEFAULT_FORMS = ["I", "II", "III", "IV"]
EXAM_TYPES = ["Test 1", "Test 2", "Midterm", "Terminal/Annual"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


BOOTSTRAP_RECOVERY_CODE = "NALA-RESET-2026"


def _column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def _migrate(conn):
    # Add columns to pre-existing databases that were created before this feature existed.
    if not _column_exists(conn, "admins", "recovery_code_hash"):
        conn.execute("ALTER TABLE admins ADD COLUMN recovery_code_hash TEXT")
    if not _column_exists(conn, "teachers", "recovery_code_hash"):
        conn.execute("ALTER TABLE teachers ADD COLUMN recovery_code_hash TEXT")
    if not _column_exists(conn, "teachers", "status"):
        conn.execute("ALTER TABLE teachers ADD COLUMN status TEXT DEFAULT 'active'")
    if not _column_exists(conn, "students", "recovery_code_hash"):
        conn.execute("ALTER TABLE students ADD COLUMN recovery_code_hash TEXT")
    if not _column_exists(conn, "students", "status"):
        conn.execute("ALTER TABLE students ADD COLUMN status TEXT DEFAULT 'active'")
    conn.commit()

    conn.execute("UPDATE teachers SET status='active' WHERE status IS NULL")
    conn.execute("UPDATE students SET status='active' WHERE status IS NULL")
    conn.commit()

    conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_name','Nala Secondary School')")
    conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_code','S9081')")
    conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_region','Dar es Salaam')")
    conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_district','')")
    conn.commit()

    admins_without_code = conn.execute(
        "SELECT id FROM admins WHERE recovery_code_hash IS NULL").fetchall()
    if admins_without_code:
        code_hash = generate_password_hash(BOOTSTRAP_RECOVERY_CODE)
        for row in admins_without_code:
            conn.execute("UPDATE admins SET recovery_code_hash=? WHERE id=?", (code_hash, row["id"]))
        conn.commit()


def init_db():
    first_run = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)

    if first_run:
        for i, (name, abbr, code) in enumerate(DEFAULT_SUBJECTS):
            conn.execute("INSERT OR IGNORE INTO subjects (name, abbr, code, sort_order) VALUES (?,?,?,?)",
                         (name, abbr, code, i))
        for i, f in enumerate(DEFAULT_FORMS):
            conn.execute("INSERT OR IGNORE INTO forms (name, sort_order) VALUES (?,?)", (f, i))
        for f in DEFAULT_FORMS:
            conn.execute("INSERT OR IGNORE INTO fee_structure (form, amount) VALUES (?,0)", (f,))
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('term_label','Term II')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('academic_year','2026')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_name','Nala Secondary School')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_code','S9081')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_region','Dar es Salaam')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('school_district','')")
        admin_hash = generate_password_hash('admin123')
        recovery_hash = generate_password_hash(BOOTSTRAP_RECOVERY_CODE)
        conn.execute("INSERT OR IGNORE INTO admins (id,name,password_hash,recovery_code_hash) VALUES (?,?,?,?)",
                     ('ADM-001', 'Head Teacher', admin_hash, recovery_hash))
        conn.commit()
        log(conn, 'system', 'init', 'Database created with default admin ADM-001')
    conn.close()


def log(conn, actor, action, detail):
    conn.execute("INSERT INTO activity_log (time, actor, action, detail) VALUES (?,?,?,?)",
                 (datetime.utcnow().isoformat(), actor, action, detail))
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))
    conn.commit()


def get_subjects(conn):
    return conn.execute("SELECT * FROM subjects ORDER BY sort_order, id").fetchall()


def get_forms(conn):
    return conn.execute("SELECT * FROM forms ORDER BY sort_order").fetchall()
