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


# ---------- 首页清单提醒：只报最新那天、悬停看明细、可忽略 ----------

def _proj_with_log(pid=1, name="P", unpushed=0, log=None):
    return {"id": pid, "name": name, "live": {"unpushed_count": unpushed}, "latest_log": log}


def _log(log_id=1, date_="2026-07-19", disabled_checks="", ignored=0, **checks):
    row = {
        "id": log_id, "date": date_, "disabled_checks": disabled_checks,
        "checklist_reminder_ignored": ignored,
        "claudemd_updated": 0, "memory_updated": 0,
        "pushed_to_github": 0, "deployed": 0,
    }
    row.update(checks)
    return row


def test_reminder_hides_specifics_keeps_date_and_tooltip(test_db):
    # deployed 被 disabled；claudemd 已勾；剩 memory、GitHub 未勾
    log = _log(log_id=7, date_="2026-07-19", disabled_checks="deployed", claudemd_updated=1)
    checklist = [t for t in app.build_todos([_proj_with_log(log=log)])
                 if t["kind"] == "checklist_pending"]
    assert len(checklist) == 1
    t = checklist[0]
    assert t["date"] == "2026-07-19"
    assert "memory" not in t["text"] and "CLAUDE" not in t["text"]  # 首页不铺明细
    assert t["log_id"] == 7
    assert set(t["unchecked_labels"]) == {"memory", "GitHub"}       # 明细留给 tooltip


def test_reminder_omitted_when_ignored(test_db):
    log = _log(ignored=1, memory_updated=0)  # 有未勾项但已忽略
    todos = app.build_todos([_proj_with_log(log=log)])
    assert not [t for t in todos if t["kind"] == "checklist_pending"]


def test_reminder_omitted_when_nothing_unchecked(test_db):
    log = _log(disabled_checks="deployed", claudemd_updated=1,
               memory_updated=1, pushed_to_github=1)
    todos = app.build_todos([_proj_with_log(log=log)])
    assert not [t for t in todos if t["kind"] == "checklist_pending"]


def test_ignore_route_sets_flag(test_db):
    pid = add_project(test_db, "P")
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, memory_updated) "
            "VALUES (?, '2026-07-19', 0)",
            (pid,),
        )
        log_id = cur.lastrowid
    assert app.app.test_client().post(f"/log/{log_id}/ignore-checklist").get_json()["ok"]
    with test_db.cursor() as cur:
        cur.execute("SELECT checklist_reminder_ignored FROM daily_logs WHERE id = ?", (log_id,))
        assert cur.fetchone()["checklist_reminder_ignored"] == 1
