"""SQLite layer. Single point of truth for schema and connections."""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT,
    github_repo TEXT,
    github_visibility TEXT,
    github_repo_public TEXT,
    stage TEXT NOT NULL DEFAULT 'sprout',
    online_url TEXT,
    online_status INTEGER NOT NULL DEFAULT 0,
    excluded_from_scan INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    auto_summary TEXT,
    manual_notes TEXT,
    raw_commits_json TEXT,
    UNIQUE(project_id, date),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    claudemd_updated INTEGER NOT NULL DEFAULT 0,
    memory_updated INTEGER NOT NULL DEFAULT 0,
    pushed_to_github INTEGER NOT NULL DEFAULT 0,
    deployed INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT NOT NULL,
    body TEXT,
    tags TEXT,
    linked_daily_log_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (linked_daily_log_id) REFERENCES daily_logs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    type TEXT NOT NULL DEFAULT 'video',
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    link TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SCAN_PATHS = [
    "/Users/lixiaonan/code",
    "/Users/lixiaonan/.claude/skills",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor():
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def init_db():
    with cursor() as cur:
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        # lightweight column migration for existing DBs
        cur.execute("PRAGMA table_info(projects)")
        cols = {row["name"] for row in cur.fetchall()}
        if "github_repo_public" not in cols:
            cur.execute("ALTER TABLE projects ADD COLUMN github_repo_public TEXT")
        cur.execute("SELECT value FROM settings WHERE key = 'scan_paths'")
        row = cur.fetchone()
        if not row:
            import json
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('scan_paths', ?)",
                (json.dumps(DEFAULT_SCAN_PATHS),),
            )


def get_setting(key, default=None):
    with cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_scan_paths():
    import json
    raw = get_setting("scan_paths")
    return json.loads(raw) if raw else DEFAULT_SCAN_PATHS[:]


def set_scan_paths(paths):
    import json
    set_setting("scan_paths", json.dumps(paths))


STAGE_LABELS = {
    "sprout": "萌芽",
    "plan": "Plan",
    "in_progress": "推进中",
    "mvp_done": "初步完成",
    "polishing": "进阶优化中",
}
STAGE_ORDER = ["sprout", "plan", "in_progress", "mvp_done", "polishing"]
