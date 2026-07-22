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
    # 默认 tags：项目日志 + 项目名
    assert "tags:" in text
    assert "  - 项目日志" in text
    assert "  - 我的项目" in text


def test_tag_sanitized_for_obsidian(test_db, tmp_path):
    """项目名含空格/标点时，tag 转成 Obsidian 合法形式（空格→连字符、去标点）。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "My Cool App!", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="x")
    obsidian.write_day(pid, "2026-07-20")
    # 文件名/目录用原名的安全化，tag 用 Obsidian 化后的名字
    text = (vault / "coding-dashboard" / "My Cool App!" / "2026-07-20.md").read_text("utf-8")
    assert "  - My-Cool-App" in text


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


def test_custom_subdir(test_db, tmp_path):
    """改了总文件夹名后，文件落到新名字下，默认 coding-dashboard 不再出现。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    test_db.set_setting("obsidian_subdir", "我的编码日志")
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="x")

    obsidian.write_day(pid, "2026-07-20")
    assert (vault / "我的编码日志" / "proj" / "2026-07-20.md").exists()
    assert not (vault / "coding-dashboard").exists()


def test_subdir_defaults_when_blank(test_db, tmp_path):
    """总文件夹名留空时回落默认 coding-dashboard。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    test_db.set_setting("obsidian_subdir", "")
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-20", auto_summary="x")

    obsidian.write_day(pid, "2026-07-20")
    assert (vault / "coding-dashboard" / "proj" / "2026-07-20.md").exists()


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


def _setup_diary(test_db, tmp_path, diary_name="日记随笔"):
    """配好 vault + 日记文件夹，返回日记目录 Path。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    test_db.set_setting("obsidian_diary_subdir", diary_name)
    d = vault / diary_name
    d.mkdir(parents=True)
    return d


DIARY_TEMPLATE = "# 我的一天\n\n今天心情不错。\n\n<!-- vibe:start -->\n<!-- vibe:end -->\n\n晚安。\n"


def test_inject_day_fills_block_and_keeps_diary_text(test_db, tmp_path):
    diary = _setup_diary(test_db, tmp_path)
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-22", auto_summary="x")
    (diary / "2026-07-22.md").write_text(DIARY_TEMPLATE, encoding="utf-8")

    assert obsidian.inject_day("2026-07-22") is not None
    text = (diary / "2026-07-22.md").read_text(encoding="utf-8")
    # 用户手写的内容原样保留
    assert "今天心情不错。" in text and "晚安。" in text
    # 托管块：标题 + 项目链接 + 嵌入「当天更新」段
    assert "## 今日 Vibe Coding 成果" in text
    assert "[[coding-dashboard/proj/2026-07-22|proj]]" in text
    assert "![[coding-dashboard/proj/2026-07-22#当天更新]]" in text


def test_inject_day_idempotent_no_rewrite(test_db, tmp_path):
    """内容没变时第二次注入不落盘（返回 None），对文件同步方案友好。"""
    diary = _setup_diary(test_db, tmp_path)
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-22", auto_summary="x")
    (diary / "2026-07-22.md").write_text(DIARY_TEMPLATE, encoding="utf-8")

    assert obsidian.inject_day("2026-07-22") is not None
    assert obsidian.inject_day("2026-07-22") is None


def test_inject_day_skips_without_anchor_or_file(test_db, tmp_path):
    diary = _setup_diary(test_db, tmp_path)
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-22", auto_summary="x")
    # 日记不存在 → 跳过
    assert obsidian.inject_day("2026-07-22") is None
    # 有日记但没锚点 → 原样不动
    raw = "# 手写日记，没有锚点\n"
    (diary / "2026-07-22.md").write_text(raw, encoding="utf-8")
    assert obsidian.inject_day("2026-07-22") is None
    assert (diary / "2026-07-22.md").read_text(encoding="utf-8") == raw


def test_inject_day_empty_day(test_db, tmp_path):
    """当天没有任何项目更新时，块里写占位说明。"""
    _setup_diary(test_db, tmp_path)
    diary = tmp_path / "vault" / "日记随笔"
    (diary / "2026-07-22.md").write_text(DIARY_TEMPLATE, encoding="utf-8")
    obsidian.inject_day("2026-07-22")
    text = (diary / "2026-07-22.md").read_text(encoding="utf-8")
    assert "（今天没有项目更新）" in text


def test_inject_disabled_without_setting(test_db, tmp_path):
    """没填日记文件夹时整个日记集成关闭。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-22", auto_summary="x")
    assert obsidian.inject_day("2026-07-22") is None
    assert obsidian.inject_sweep() == 0


def test_inject_sweep_backfills_late_diary(test_db, tmp_path):
    """日记补建（哪怕隔了很多天）后，sweep 会把对应天填上；非日期命名文件跳过。"""
    diary = _setup_diary(test_db, tmp_path)
    pid = add_project(test_db, "proj", tmp_path)
    add_log(test_db, pid, "2026-07-10", auto_summary="老日志")
    # 过了很多天才补建 7-10 的日记；同目录还有周记/月记
    (diary / "2026-07-10.md").write_text(DIARY_TEMPLATE, encoding="utf-8")
    (diary / "2026-W28.md").write_text("周记", encoding="utf-8")
    (diary / "2026-07.md").write_text("月记", encoding="utf-8")

    assert obsidian.inject_sweep() == 1
    text = (diary / "2026-07-10.md").read_text(encoding="utf-8")
    assert "[[coding-dashboard/proj/2026-07-10|proj]]" in text
    assert (diary / "2026-W28.md").read_text(encoding="utf-8") == "周记"


def test_write_day_refreshes_diary(test_db, tmp_path):
    """归档重写（如手动补充）会连带刷新当天日记的托管块。"""
    diary = _setup_diary(test_db, tmp_path)
    pid = add_project(test_db, "proj", tmp_path)
    log_id = add_log(test_db, pid, "2026-07-22", auto_summary="x")
    (diary / "2026-07-22.md").write_text(DIARY_TEMPLATE, encoding="utf-8")

    obsidian.write_for_log(log_id)
    text = (diary / "2026-07-22.md").read_text(encoding="utf-8")
    assert "[[coding-dashboard/proj/2026-07-22|proj]]" in text


def test_sync_generates_archive(test_db, tmp_path, repo):
    """走完整同步链路：有 commit 的当天应生成归档文件。"""
    vault = tmp_path / "vault"
    test_db.set_setting("obsidian_vault_dir", str(vault))
    today = date.today().isoformat()
    make_commit(repo, "a.txt", "hello", today)
    pid = add_project(test_db, "proj", repo)

    app.sync_one_project(pid)
    assert (vault / "coding-dashboard" / "proj" / f"{today}.md").exists()
