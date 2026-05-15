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
    category TEXT,
    description TEXT,
    online_url TEXT,
    online_status INTEGER NOT NULL DEFAULT 0,
    tracks_deployment INTEGER NOT NULL DEFAULT 0,
    excluded_from_scan INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS project_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    important INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    auto_summary TEXT,
    manual_notes TEXT,
    raw_commits_json TEXT,
    claudemd_updated INTEGER NOT NULL DEFAULT 0,
    memory_updated INTEGER NOT NULL DEFAULT 0,
    pushed_to_github INTEGER NOT NULL DEFAULT 0,
    deployed INTEGER NOT NULL DEFAULT 0,
    disabled_checks TEXT NOT NULL DEFAULT '',
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
    starred INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reference_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT,
    links TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_categories (
    project_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (project_id, category_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
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
        if "category" not in cols:
            cur.execute("ALTER TABLE projects ADD COLUMN category TEXT")
        if "starred" not in cols:
            cur.execute("ALTER TABLE projects ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        if "description" not in cols:
            cur.execute("ALTER TABLE projects ADD COLUMN description TEXT")
        if "tracks_deployment" not in cols:
            cur.execute(
                "ALTER TABLE projects ADD COLUMN tracks_deployment INTEGER NOT NULL DEFAULT 0"
            )
            # backfill: a project with an online URL or marked online clearly needs deploy tracking
            cur.execute(
                "UPDATE projects SET tracks_deployment = 1 "
                "WHERE (online_url IS NOT NULL AND online_url != '') OR online_status = 1"
            )
        # daily_logs checklist columns
        cur.execute("PRAGMA table_info(daily_logs)")
        dl_cols = {row["name"] for row in cur.fetchall()}
        for col in ("claudemd_updated", "memory_updated", "pushed_to_github", "deployed"):
            if col not in dl_cols:
                cur.execute(
                    f"ALTER TABLE daily_logs ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )
        if "disabled_checks" not in dl_cols:
            cur.execute(
                "ALTER TABLE daily_logs ADD COLUMN disabled_checks TEXT NOT NULL DEFAULT ''"
            )
            # backfill: projects that don't track deployment -> 'deployed' removed from their logs
            cur.execute(
                "UPDATE daily_logs SET disabled_checks = 'deployed' "
                "WHERE project_id IN (SELECT id FROM projects WHERE tracks_deployment = 0)"
            )
        # media_items: starred
        cur.execute("PRAGMA table_info(media_items)")
        mi_cols = {row["name"] for row in cur.fetchall()}
        if "starred" not in mi_cols:
            cur.execute(
                "ALTER TABLE media_items ADD COLUMN starred INTEGER NOT NULL DEFAULT 0"
            )
        # project_todos: make project_id nullable + add `important` (recreate table)
        cur.execute("PRAGMA table_info(project_todos)")
        pt_cols = {row["name"] for row in cur.fetchall()}
        if "important" not in pt_cols:
            cur.execute(
                "CREATE TABLE project_todos_new ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "project_id INTEGER, "
                "text TEXT NOT NULL, "
                "done INTEGER NOT NULL DEFAULT 0, "
                "important INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), "
                "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)"
            )
            cur.execute(
                "INSERT INTO project_todos_new (id, project_id, text, done, created_at) "
                "SELECT id, project_id, text, done, created_at FROM project_todos"
            )
            cur.execute("DROP TABLE project_todos")
            cur.execute("ALTER TABLE project_todos_new RENAME TO project_todos")
        cur.execute("SELECT value FROM settings WHERE key = 'scan_paths'")
        row = cur.fetchone()
        if not row:
            import json
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('scan_paths', ?)",
                (json.dumps(DEFAULT_SCAN_PATHS),),
            )
        # seed categories on first run
        cur.execute("SELECT COUNT(*) AS c FROM categories")
        if cur.fetchone()["c"] == 0:
            for i, name in enumerate(PRESET_CATEGORIES):
                cur.execute(
                    "INSERT INTO categories (name, sort_order) VALUES (?, ?)", (name, i)
                )
        # one-time migration: single category text -> project_categories join table
        cur.execute(
            "SELECT id, category FROM projects WHERE category IS NOT NULL AND category != ''"
        )
        for row in cur.fetchall():
            cur.execute("SELECT id FROM categories WHERE name = ?", (row["category"],))
            crow = cur.fetchone()
            if crow:
                cid = crow["id"]
            else:
                cur.execute(
                    "INSERT INTO categories (name, sort_order) VALUES (?, 99)",
                    (row["category"],),
                )
                cid = cur.lastrowid
            cur.execute(
                "INSERT OR IGNORE INTO project_categories (project_id, category_id) VALUES (?, ?)",
                (row["id"], cid),
            )
            cur.execute("UPDATE projects SET category = NULL WHERE id = ?", (row["id"],))
        # one-time migration: iterations -> per-day checklist on daily_logs
        cur.execute("SELECT value FROM settings WHERE key = 'iterations_migrated'")
        if not cur.fetchone():
            cur.execute("SELECT * FROM iterations")
            for it in cur.fetchall():
                cur.execute(
                    "SELECT id FROM daily_logs WHERE project_id = ? AND date = ?",
                    (it["project_id"], it["date"]),
                )
                row = cur.fetchone()
                if row:
                    log_id = row["id"]
                else:
                    cur.execute(
                        "INSERT INTO daily_logs (project_id, date) VALUES (?, ?)",
                        (it["project_id"], it["date"]),
                    )
                    log_id = cur.lastrowid
                cur.execute(
                    "UPDATE daily_logs SET "
                    "claudemd_updated = max(claudemd_updated, ?), "
                    "memory_updated = max(memory_updated, ?), "
                    "pushed_to_github = max(pushed_to_github, ?), "
                    "deployed = max(deployed, ?) WHERE id = ?",
                    (
                        it["claudemd_updated"],
                        it["memory_updated"],
                        it["pushed_to_github"],
                        it["deployed"],
                        log_id,
                    ),
                )
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('iterations_migrated', '1')"
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

PRESET_CATEGORIES = ["Skill", "公开网站", "工作", "自用", "生活"]
