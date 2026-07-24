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
    paused INTEGER NOT NULL DEFAULT 0,
    excluded_from_scan INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    repo_snapshot TEXT,
    repo_snapshot_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS project_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    important INTEGER NOT NULL DEFAULT 0,
    done_at TEXT,
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
    checklist_reminder_ignored INTEGER NOT NULL DEFAULT 0,
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
    starred INTEGER NOT NULL DEFAULT 0,
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
    os.path.expanduser("~/code"),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 后台同步线程 / launchd CLI 会和 web 请求并发读写同一个库：
    # WAL 让读写互不阻塞，busy_timeout 避免偶发 "database is locked"
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
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
        if "repo_snapshot" not in cols:
            cur.execute("ALTER TABLE projects ADD COLUMN repo_snapshot TEXT")
            cur.execute("ALTER TABLE projects ADD COLUMN repo_snapshot_at TEXT")
        if "tracks_deployment" not in cols:
            cur.execute(
                "ALTER TABLE projects ADD COLUMN tracks_deployment INTEGER NOT NULL DEFAULT 0"
            )
            # backfill: a project with an online URL or marked online clearly needs deploy tracking
            cur.execute(
                "UPDATE projects SET tracks_deployment = 1 "
                "WHERE (online_url IS NOT NULL AND online_url != '') OR online_status = 1"
            )
        if "paused" not in cols:
            # 「暂停中」是叠加在阶段上的状态，不是第六个阶段——项目停在哪一步得保留下来
            cur.execute("ALTER TABLE projects ADD COLUMN paused INTEGER NOT NULL DEFAULT 0")
            # 短暂存在过的 stage='paused'（当时按阶段做的）：转成标记，阶段回落到「推进中」
            cur.execute(
                "UPDATE projects SET paused = 1, stage = 'in_progress' WHERE stage = 'paused'"
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
        if "checklist_reminder_ignored" not in dl_cols:
            # 首页清单提醒的「忽略」标记，只静音首页那一条，不动项目页清单本身
            cur.execute(
                "ALTER TABLE daily_logs ADD COLUMN checklist_reminder_ignored INTEGER NOT NULL DEFAULT 0"
            )
        # media_items: starred
        cur.execute("PRAGMA table_info(media_items)")
        mi_cols = {row["name"] for row in cur.fetchall()}
        if "starred" not in mi_cols:
            cur.execute(
                "ALTER TABLE media_items ADD COLUMN starred INTEGER NOT NULL DEFAULT 0"
            )
        # reference_items: starred（加星置顶）
        cur.execute("PRAGMA table_info(reference_items)")
        ri_cols = {row["name"] for row in cur.fetchall()}
        if "starred" not in ri_cols:
            cur.execute(
                "ALTER TABLE reference_items ADD COLUMN starred INTEGER NOT NULL DEFAULT 0"
            )
        if "publish_date" not in mi_cols:
            cur.execute(
                "ALTER TABLE media_items ADD COLUMN publish_date TEXT"
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
        # project_todos: done_at（完成日期）—— 支撑「完成的待办第二天归档进历史回收站」
        cur.execute("PRAGMA table_info(project_todos)")
        pt_cols = {row["name"] for row in cur.fetchall()}
        if "done_at" not in pt_cols:
            cur.execute("ALTER TABLE project_todos ADD COLUMN done_at TEXT")
            # 存量已完成的待办直接归档：done_at 回填为其创建日期（早于今天 => 立即进回收站）
            cur.execute(
                "UPDATE project_todos SET done_at = date(created_at) "
                "WHERE done = 1 AND done_at IS NULL"
            )
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


BACKUP_ICLOUD_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/coding-dashboard-backups"
)
BACKUP_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def resolve_backup_dir():
    """决定备份落到哪个目录（跨平台）：

    1. 设置里显式填了 backup_dir → 用它（可指向任意云盘/外置盘，任意平台通用）
    2. 否则 macOS 且 iCloud Drive 可用 → iCloud（异地，防盘挂/机器丢）
    3. 否则 → 工程目录 backups/（gitignore）

    这样 Mac 用户开箱即用 iCloud，非 Mac 用户默认落本地、也能自己指云盘。
    """
    configured = (get_setting("backup_dir") or "").strip()
    if configured:
        return os.path.expanduser(configured)
    if os.path.isdir(os.path.dirname(BACKUP_ICLOUD_DIR)):  # iCloud 根存在 = macOS 开了 iCloud
        return BACKUP_ICLOUD_DIR
    return BACKUP_LOCAL_DIR


def backup_db(keep=30):
    """备份 data.db。手动笔记/待办/摘要都只存在这一个文件里，不可再生。

    目录由 resolve_backup_dir() 决定（设置优先 → iCloud → 本地）。
    每天一份、同日覆盖，保留最近 keep 份。用 sqlite3 的 backup API
    而非复制文件，保证 WAL 模式下快照一致。失败返回 None，不影响主流程。
    """
    if not os.path.exists(DB_PATH):
        return None
    target_dir = resolve_backup_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(
            target_dir, f"data-{datetime.now().strftime('%Y%m%d')}.db"
        )
        src = sqlite3.connect(DB_PATH)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        old_backups = sorted(
            f
            for f in os.listdir(target_dir)
            if f.startswith("data-") and f.endswith(".db")
        )
        for old in old_backups[:-keep]:
            os.remove(os.path.join(target_dir, old))
        return target
    except Exception:
        return None


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
