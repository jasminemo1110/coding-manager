"""Obsidian 归档：同步生成文件、手动补充覆盖、没配则跳过、backfill 历史。"""

from datetime import date

import app
import obsidian
from conftest import make_commit


def add_project(test_db, name, path):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, stage) VALUES (?, ?, 'growing')",
            (name, str(path)),
        )
        return cur.lastrowid


def add_log(test_db, pid, day, auto_summary=None, manual_notes=None):
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, manual_notes) "
            "VALUES (?, ?, ?, ?)",
            (pid, day, auto_summary, manual_notes),
        )
        return cur.lastrowid


def test_no_vault_skips(test_db, tmp_path):
    """没配 vault 路径时不写任何文件。"""
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="做了点事")
    assert obsidian.write_day(pid, "2026-07-20") is None


def test_write_day_has_frontmatter_and_content(test_db, tmp_path):
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "我的项目", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="今天加了归档功能")

    path = obsidian.write_day(pid, "2026-07-20")
    assert path is not None
    expected = vault / "coding-dashboard" / "我的项目" / "2026-07-20.md"
    assert expected.exists()
    text = expected.read_text(encoding="utf-8")
    assert 'project: "我的项目"' in text
    assert "date: 2026-07-20" in text
    assert "今天加了归档功能" in text


def test_manual_notes_overwrite(test_db, tmp_path):
    """手动补充后重写，文件里应同时有 AI 摘要和手动补充，且是覆盖不是追加。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "proj", tmp_path)
    log_id = add_log(test_db, pid, "2026-07-20", auto_summary="自动内容")

    obsidian.write_for_log(log_id)
    with test_db.cursor() as cur:
        cur.execute(
            "UPDATE daily_logs SET manual_notes=? WHERE id=?", ("我补充的话", log_id)
        )
    obsidian.write_for_log(log_id)

    f = vault / "coding-dashboard" / "proj" / "2026-07-20.md"
    text = f.read_text(encoding="utf-8")
    assert "自动内容" in text
    assert "手动补充" in text
    assert "我补充的话" in text
    # 覆盖式：手动内容只出现一次，没有重复堆叠
    assert text.count("我补充的话") == 1


def test_failed_placeholder_not_archived(test_db, tmp_path):
    """AI 失败占位文本不写进存档，当天无内容时落 (无)。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="[AI 摘要失败：timeout]")

    obsidian.write_day(pid, "2026-07-20")
    text = (vault / "coding-dashboard" / "proj" / "2026-07-20.md").read_text("utf-8")
    assert "AI 摘要失败" not in text
    assert "(无)" in text


def test_linked_note_in_archive(test_db, tmp_path):
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "proj", tmp_path)
    log_id = add_log(test_db, pid, "2026-07-20", auto_summary="自动内容")
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO notes (project_id, title, body, linked_daily_log_id) "
            "VALUES (?, ?, ?, ?)",
            (pid, "踩坑记录", "SQLite WAL 的坑", log_id),
        )
    obsidian.write_for_log(log_id)
    text = (vault / "coding-dashboard" / "proj" / "2026-07-20.md").read_text("utf-8")
    assert "踩坑记录" in text
    assert "SQLite WAL 的坑" in text


def test_backfill_all(test_db, tmp_path):
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-18", auto_summary="a")
    add_log(test_db, pid, "2026-07-19", auto_summary="b")
    add_log(test_db, pid, "2026-07-20", auto_summary="c")

    assert obsidian.backfill_all() == 3
    base = vault / "coding-dashboard" / "proj"
    assert (base / "2026-07-18.md").exists()
    assert (base / "2026-07-19.md").exists()
    assert (base / "2026-07-20.md").exists()


def test_sync_generates_archive(test_db, tmp_path, repo):
    """走完整同步链路：有 commit 的当天应生成归档文件。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    today = date.today().isoformat()
    make_commit(repo, "a.txt", "hello", today)
    pid = add_project(test_db, "proj", repo)

    app.sync_one_project(pid)
    assert (vault / "coding-dashboard" / "proj" / f"{today}.md").exists()
