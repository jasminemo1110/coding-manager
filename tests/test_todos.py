"""待办历史回收站：完成的待办第二天归档、当天仍显示、还原、迁移回填。"""

from datetime import date, timedelta

import app


def iso(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def add_project(test_db, name):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, stage) VALUES (?, 'in_progress')", (name,)
        )
        return cur.lastrowid


def add_todo(test_db, text, project_id=None, done=0, done_at=None, important=0):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO project_todos (project_id, text, done, done_at, important) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, text, done, done_at, important),
        )
        return cur.lastrowid


def todo_row(test_db, tid):
    with test_db.cursor() as cur:
        cur.execute("SELECT done, done_at FROM project_todos WHERE id = ?", (tid,))
        return dict(cur.fetchone())


# ---------- 归档判定：完成于更早某天才归档 ----------

def test_panel_todos_excludes_yesterday_keeps_today(test_db):
    add_todo(test_db, "昨天做完的", done=1, done_at=iso(1))   # 已归档
    add_todo(test_db, "今天做完的", done=1, done_at=iso(0))   # 当天仍显示
    add_todo(test_db, "还没做的", done=0)
    texts = [t["text"] for t in app.panel_todos()]
    assert "今天做完的" in texts
    assert "还没做的" in texts
    assert "昨天做完的" not in texts


def test_toggle_sets_and_clears_done_at(test_db):
    tid = add_todo(test_db, "x")
    client = app.app.test_client()

    assert client.post(f"/todo/{tid}/toggle").get_json()["done"] == 1
    row = todo_row(test_db, tid)
    assert row["done"] == 1 and row["done_at"] == iso(0)

    assert client.post(f"/todo/{tid}/toggle").get_json()["done"] == 0
    row = todo_row(test_db, tid)
    assert row["done"] == 0 and row["done_at"] is None


# ---------- 历史页 ----------

def test_history_shows_archived_grouped(test_db):
    pid = add_project(test_db, "示例项目")
    add_todo(test_db, "项目归档项", project_id=pid, done=1, done_at=iso(2))
    add_todo(test_db, "全局归档项", done=1, done_at=iso(1))
    add_todo(test_db, "今天刚做完", done=1, done_at=iso(0))  # 不该出现
    add_todo(test_db, "还没做完", done=0)                    # 不该出现

    html = app.app.test_client().get("/todos/history").get_data(as_text=True)
    assert "项目归档项" in html
    assert "全局归档项" in html
    assert "示例项目" in html          # 分组标题
    assert "今天刚做完" not in html
    assert "还没做完" not in html


def test_restore_unarchives(test_db):
    tid = add_todo(test_db, "还原我", done=1, done_at=iso(3))
    app.app.test_client().post(f"/todo/{tid}/restore")
    row = todo_row(test_db, tid)
    assert row["done"] == 0 and row["done_at"] is None


# ---------- 迁移：存量已完成待办立即归档 ----------

def test_migration_backfills_done_at(test_db):
    """老库 project_todos 没有 done_at 列时，init_db 补列并把已完成项回填到创建日期。"""
    with test_db.cursor() as cur:
        cur.execute("DROP TABLE project_todos")
        cur.execute(
            "CREATE TABLE project_todos ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, text TEXT NOT NULL, "
            "done INTEGER NOT NULL DEFAULT 0, important INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))"
        )
        cur.execute(
            "INSERT INTO project_todos (text, done, created_at) "
            "VALUES ('老的已完成', 1, '2020-01-01 12:00:00')"
        )
        cur.execute(
            "INSERT INTO project_todos (text, done, created_at) "
            "VALUES ('老的未完成', 0, '2020-01-01 12:00:00')"
        )

    test_db.init_db()

    with test_db.cursor() as cur:
        cur.execute("SELECT text, done_at FROM project_todos")
        rows = {r["text"]: r["done_at"] for r in cur.fetchall()}
    assert rows["老的已完成"] == "2020-01-01"   # 早于今天 => 立即归档
    assert rows["老的未完成"] is None
