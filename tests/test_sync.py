"""同步逻辑——历史上出过回归的重灾区：水位线、清单自动检测、AI 失败重试、快照。"""

import json
import threading
import time
from datetime import date, timedelta

import pytest

import app
from conftest import git, make_commit


def iso(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def add_project(test_db, name, path, tracks_deployment=0):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, stage, tracks_deployment) "
            "VALUES (?, ?, 'in_progress', ?)",
            (name, str(path), tracks_deployment),
        )
        return cur.lastrowid


def get_log(test_db, pid, day):
    with test_db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_logs WHERE project_id=? AND date=?", (pid, day)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def fake_ai(monkeypatch, reply="AI 摘要"):
    """让 get_ai_config 有 key、ai.summarize 返回固定文本，并记录调用。"""
    calls = []

    def summarize(name, commits, diff, cfg=None):
        calls.append(name)
        return reply

    monkeypatch.setattr(app.ai, "summarize", summarize)
    monkeypatch.setattr(
        app, "get_ai_config",
        lambda: {"api_key": "test-key", "base_url": "", "model": ""},
    )
    return calls


# ---------- 同步主流程 ----------

def test_sync_creates_log_with_auto_checks(test_db, repo):
    make_commit(repo, "AGENTS.md", "说明", iso(0))
    pid = add_project(test_db, "proj", repo)
    r = app.sync_one_project(pid)
    assert r["ok"] and r["commits"] == 1 and r["days"] == 1

    log = get_log(test_db, pid, iso(0))
    assert log["claudemd_updated"] == 1        # 今天的 commit 改了 AGENTS.md
    assert log["pushed_to_github"] == 0        # 无 remote → 全部算未推
    assert len(json.loads(log["raw_commits_json"])) == 1
    assert "deployed" in log["disabled_checks"]            # tracks_deployment=0
    assert "claudemd_updated" not in log["disabled_checks"]  # 有 AGENTS.md 文件
    # 水位线推进到今天
    assert test_db.get_setting(f"last_sync_date:{pid}") == iso(0)


def test_sync_falls_back_to_legacy_claude_md(test_db, repo):
    make_commit(repo, "CLAUDE.md", "旧说明", iso(0))
    pid = add_project(test_db, "legacy", repo)
    app.sync_one_project(pid)
    log = get_log(test_db, pid, iso(0))
    assert log["claudemd_updated"] == 1
    assert "claudemd_updated" not in log["disabled_checks"]


def test_sync_backfills_since_watermark(test_db, repo):
    make_commit(repo, "a.txt", "三天前", iso(3))
    make_commit(repo, "b.txt", "昨天", iso(1))
    pid = add_project(test_db, "proj", repo)
    test_db.set_setting(f"last_sync_date:{pid}", iso(4))
    r = app.sync_one_project(pid)
    assert r["commits"] == 2 and r["days"] == 2
    assert get_log(test_db, pid, iso(3))
    assert get_log(test_db, pid, iso(1))


def test_log_start_date_excludes_inherited_history(test_db, repo):
    make_commit(repo, "upstream.txt", "原作者", iso(3))
    make_commit(repo, "mine.txt", "我的修改", iso(1))
    pid = add_project(test_db, "fork", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET log_start_date=? WHERE id=?",
            (iso(1), pid),
        )
    test_db.set_setting(f"last_sync_date:{pid}", iso(4))

    result = app.sync_one_project(pid)

    assert result["commits"] == 1
    assert get_log(test_db, pid, iso(3)) is None
    assert get_log(test_db, pid, iso(1)) is not None


def test_log_start_date_hides_retained_rows_from_stats(test_db, repo):
    pid = add_project(test_db, "fork", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET log_start_date=? WHERE id=?",
            (iso(1), pid),
        )
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, raw_commits_json) VALUES (?, ?, ?)",
            (pid, iso(3), '[{"hash":"old"}]'),
        )
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, raw_commits_json) VALUES (?, ?, ?)",
            (pid, iso(1), '[{"hash":"mine"}]'),
        )

    by_date = app._day_commit_index(iso(4))

    assert iso(3) not in by_date
    assert by_date[iso(1)]["commits"] == 1


def test_sync_empty_repo_skipped(test_db, repo):
    pid = add_project(test_db, "proj", repo)  # 有 path 但没有任何 commit
    r = app.sync_one_project(pid)
    assert r["ok"] and r.get("skipped") is True


def test_pushed_detection_per_day(test_db, repo, tmp_path):
    git(tmp_path, "init", "--bare", "-q", "origin.git")
    make_commit(repo, "a.txt", "v1", iso(2))
    git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    make_commit(repo, "a.txt", "v2", iso(1))
    pid = add_project(test_db, "proj", repo)
    app.sync_one_project(pid)
    assert get_log(test_db, pid, iso(2))["pushed_to_github"] == 1  # 已推的那天
    assert get_log(test_db, pid, iso(1))["pushed_to_github"] == 0  # 未推的那天


def test_repo_snapshot_saved_on_sync(test_db, repo):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    app.sync_one_project(pid)
    with test_db.cursor() as cur:
        cur.execute(
            "SELECT repo_snapshot, repo_snapshot_at FROM projects WHERE id=?", (pid,)
        )
        row = cur.fetchone()
    snap = json.loads(row["repo_snapshot"])
    assert snap["unpushed_count"] == 1
    assert row["repo_snapshot_at"]


def test_add_from_scan_sets_personal_start_and_fork_source(test_db, repo, monkeypatch):
    test_db.set_setting("last_backup_date", iso(0))
    monkeypatch.setattr(
        app.scanner,
        "get_repo_info",
        lambda path: {
            "path": path,
            "name": "fork",
            "github_repo": "me/fork",
            "upstream_repo": "original/project",
        },
    )
    monkeypatch.setattr(
        app.scanner,
        "github_repo_metadata",
        lambda repo_slug, token=None: {
            "visibility": "public",
            "forked_from": "original/project",
        },
    )

    response = app.app.test_client().post(
        "/project/add-from-scan", data={"path": str(repo)}
    )

    assert response.status_code == 302
    with test_db.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE path=?", (str(repo),))
        project = dict(cur.fetchone())
    assert project["log_start_date"] == iso(0)
    assert project["forked_from"] == "original/project"
    assert project["github_visibility"] == "public"


# ---------- AI 摘要的重试语义 ----------

def test_historical_summary_not_regenerated(test_db, repo, monkeypatch):
    make_commit(repo, "a.txt", "x", iso(2))
    pid = add_project(test_db, "proj", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary) VALUES (?,?,?)",
            (pid, iso(2), "已有摘要"),
        )
    calls = fake_ai(monkeypatch, "新摘要")
    app.sync_one_project(pid)
    assert get_log(test_db, pid, iso(2))["auto_summary"] == "已有摘要"
    assert calls == []  # 历史天有摘要就不再花 AI 调用


def test_failed_placeholder_retried(test_db, repo, monkeypatch):
    make_commit(repo, "a.txt", "x", iso(2))
    pid = add_project(test_db, "proj", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary) VALUES (?,?,?)",
            (pid, iso(2), "[AI 摘要失败：Connection timeout]"),
        )
    calls = fake_ai(monkeypatch, "重试成功")
    app.sync_one_project(pid)
    assert get_log(test_db, pid, iso(2))["auto_summary"] == "重试成功"
    assert len(calls) == 1


def test_midnight_late_commit_backfilled(test_db, repo, monkeypatch):
    """零点裂缝：水位线当天在上次同步之后又出的 commit，次日同步要补进那天日志。"""
    yesterday = iso(1)
    make_commit(repo, "a.txt", "傍晚的提交", yesterday)
    pid = add_project(test_db, "proj", repo)
    # 模拟昨天同步过：日志只含当时那 1 条 commit + 摘要，水位线停在昨天
    commits, _ = app.scanner.get_commits_for_day(str(repo), yesterday)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, raw_commits_json) "
            "VALUES (?,?,?,?)",
            (pid, yesterday, "昨晚的摘要", json.dumps(commits)),
        )
    test_db.set_setting(f"last_sync_date:{pid}", yesterday)
    # 23:59 又提交一条（git 日期仍属昨天），过零点才同步
    make_commit(repo, "b.txt", "深夜的提交", yesterday)

    calls = fake_ai(monkeypatch, "补齐后的摘要")
    app.sync_one_project(pid)
    log = get_log(test_db, pid, yesterday)
    assert len(json.loads(log["raw_commits_json"])) == 2   # 深夜那条补进来了
    assert log["auto_summary"] == "补齐后的摘要"             # 摘要按完整两条重写
    assert len(calls) == 1                                  # 只为集合变了的那天调 AI


def test_watermark_day_unchanged_skipped(test_db, repo, monkeypatch):
    """水位线当天重扫但 commit 集合没变：照旧跳过，不重复花 AI。"""
    yesterday = iso(1)
    make_commit(repo, "a.txt", "x", yesterday)
    pid = add_project(test_db, "proj", repo)
    commits, _ = app.scanner.get_commits_for_day(str(repo), yesterday)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, raw_commits_json) "
            "VALUES (?,?,?,?)",
            (pid, yesterday, "已有摘要", json.dumps(commits)),
        )
    test_db.set_setting(f"last_sync_date:{pid}", yesterday)

    calls = fake_ai(monkeypatch, "不该出现")
    app.sync_one_project(pid)
    assert get_log(test_db, pid, yesterday)["auto_summary"] == "已有摘要"
    assert calls == []


def test_today_summary_refreshed_every_sync(test_db, repo, monkeypatch):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary) VALUES (?,?,?)",
            (pid, iso(0), "早上的摘要"),
        )
    fake_ai(monkeypatch, "晚上的摘要")
    app.sync_one_project(pid)
    assert get_log(test_db, pid, iso(0))["auto_summary"] == "晚上的摘要"


# ---------- 清单：用户操作在重同步后保留 ----------

def test_user_removed_check_stays_removed(test_db, repo):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    app.sync_one_project(pid)
    log = get_log(test_db, pid, iso(0))
    app._set_log_disabled(log["id"], "memory_updated", True)  # 用户点 × 移除
    app.sync_one_project(pid)
    disabled = get_log(test_db, pid, iso(0))["disabled_checks"]
    assert "memory_updated" in disabled
    assert "deployed" in disabled  # 建行时的默认 disabled 也还在


def test_manual_memory_check_survives_resync(test_db, repo):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    app.sync_one_project(pid)
    log = get_log(test_db, pid, iso(0))
    with test_db.cursor() as cur:  # 用户手动勾上 memory
        cur.execute("UPDATE daily_logs SET memory_updated=1 WHERE id=?", (log["id"],))
    app.sync_one_project(pid)  # 自动检测测不到 memory（max 保留手动勾）
    assert get_log(test_db, pid, iso(0))["memory_updated"] == 1


# ---------- 水位线 / 默认 disabled ----------

def test_sync_start_date_clamped(test_db):
    today = iso(0)
    floor = date.today() - timedelta(days=app.MAX_SYNC_LOOKBACK_DAYS)
    # 无水位线 → 回看上限
    assert app._sync_start_date(999, today) == floor
    # 正常水位线 → 当天重扫（兜住零点裂缝：那天在上次同步后可能又有新 commit）
    test_db.set_setting("last_sync_date:999", iso(3))
    assert app._sync_start_date(999, today) == date.today() - timedelta(days=3)
    # 很旧的水位线 → 不早于回看上限
    test_db.set_setting("last_sync_date:999", "2020-01-01")
    assert app._sync_start_date(999, today) == floor
    # 坏数据 → 回看上限
    test_db.set_setting("last_sync_date:999", "not-a-date")
    assert app._sync_start_date(999, today) == floor


def test_sync_start_date_respects_project_boundary(test_db, repo):
    pid = add_project(test_db, "fork", repo)
    with test_db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET log_start_date=? WHERE id=?",
            (iso(2), pid),
        )
    test_db.set_setting(f"last_sync_date:{pid}", iso(5))
    assert app._sync_start_date(pid, iso(0)) == date.today() - timedelta(days=2)


def test_default_disabled_for():
    both = app.default_disabled_for({"tracks_deployment": 0}, has_claudemd=False)
    assert "deployed" in both and "claudemd_updated" in both
    assert app.default_disabled_for({"tracks_deployment": 1}, has_claudemd=True) == ""


# ---------- repo 快照读写 ----------

def test_enrich_reads_snapshot_without_scanning(test_db, monkeypatch):
    pid = add_project(test_db, "proj", "/nonexistent")
    app.save_repo_snapshot(pid, {
        "unpushed_count": 7,
        "last_commit_date": "2026-01-01",
        "last_commit_at": "2026-01-01T12:00:00+08:00",
    })
    monkeypatch.setattr(
        app.scanner, "get_repo_info",
        lambda path: pytest.fail("有快照时不该现场扫 git"),
    )
    enriched = app.enrich_project(app.get_project(pid))
    assert enriched["live"]["unpushed_count"] == 7


def test_enrich_upgrades_snapshot_without_full_commit_time(test_db, repo, monkeypatch):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    app.save_repo_snapshot(pid, {"last_commit_date": iso(0), "unpushed_count": 7})
    original_get_repo_info = app.scanner.get_repo_info
    calls = []

    def scan(path):
        calls.append(path)
        return original_get_repo_info(path)

    monkeypatch.setattr(app.scanner, "get_repo_info", scan)
    enriched = app.enrich_project(app.get_project(pid))
    assert calls == [str(repo)]
    assert enriched["live"]["last_commit_at"].startswith(iso(0))
    with test_db.cursor() as cur:
        cur.execute("SELECT repo_snapshot FROM projects WHERE id=?", (pid,))
        assert json.loads(cur.fetchone()["repo_snapshot"])["last_commit_at"]


def test_enrich_scans_and_persists_when_no_snapshot(test_db, repo):
    make_commit(repo, "a.txt", "x", iso(0))
    pid = add_project(test_db, "proj", repo)
    enriched = app.enrich_project(app.get_project(pid))  # 尚无快照 → 现场扫
    assert enriched["live"]["unpushed_count"] == 1
    with test_db.cursor() as cur:  # 并且回写了
        cur.execute("SELECT repo_snapshot FROM projects WHERE id=?", (pid,))
        assert json.loads(cur.fetchone()["repo_snapshot"])["unpushed_count"] == 1


def test_dashboard_sorts_same_day_by_full_commit_time(test_db, monkeypatch):
    older = add_project(test_db, "coding-dashboard", "/nonexistent-older")
    newer = add_project(test_db, "mobai-board", "/nonexistent-newer")
    app.save_repo_snapshot(older, {
        "last_commit_date": "2026-08-03",
        "last_commit_at": "2026-08-03T17:35:11+08:00",
    })
    app.save_repo_snapshot(newer, {
        "last_commit_date": "2026-08-03",
        "last_commit_at": "2026-08-03T19:51:07+08:00",
    })
    monkeypatch.setattr(app, "scan_suggestions", lambda: [])
    monkeypatch.setattr(
        app,
        "render_template",
        lambda template, **context: [p["name"] for p in context["projects"]],
    )

    with app.app.test_request_context("/?sort=updated"):
        assert app.dashboard()[:2] == ["mobai-board", "coding-dashboard"]


# ---------- 后台同步：全局单槽位 ----------

def test_start_sync_single_slot(test_db, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_sync(pid):
        started.set()
        release.wait(5)
        return {"project_id": pid, "ok": True, "skipped": True}

    monkeypatch.setattr(app, "sync_one_project", slow_sync)
    assert app.start_sync([1]) is True
    assert started.wait(5)
    assert app.start_sync([2]) is False  # 跑着的时候拒绝再启动
    release.set()
    for _ in range(100):  # 等后台线程收尾
        with app._sync_lock:
            if not app._sync_state["running"]:
                break
        time.sleep(0.05)
    with app._sync_lock:
        assert app._sync_state["running"] is False
        assert app._sync_state["done"] == 1
        assert app._sync_state["skipped"] == 1
