"""Obsidian 学习库：只读扫描、搜索和旧数据保留式迁移。"""

import json

import app
import learning_library


def configure(test_db, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    test_db.set_setting("obsidian_vault_dir", str(vault))
    test_db.set_setting("obsidian_subdir", "项目日志")
    return vault, vault / "项目日志" / "Vibe Coding 学习库"


def test_scan_reads_all_markdown_as_equal_notes(test_db, tmp_path):
    vault, root = configure(test_db, tmp_path)
    nested = root / "CSS"
    nested.mkdir(parents=True)
    (nested / "grid.md").write_text(
        "---\ntitle: \"Grid 布局\"\ntags:\n  - css\nupdated: 2026-08-27\n---\n\n# 备用标题\n\n学习正文。",
        encoding="utf-8",
    )
    (root / "docs.md").write_text(
        "# 官方文档\n\n[Flask](https://flask.palletsprojects.com/)",
        encoding="utf-8",
    )
    result = learning_library.scan()
    assert {item["title"] for item in result["notes"]} == {"Grid 布局", "官方文档"}
    grid = next(item for item in result["notes"] if item["title"] == "Grid 布局")
    assert grid["tags"] == ["css"]
    assert "obsidian://open?" in grid["obsidian_url"]
    assert "vault=vault" in grid["obsidian_url"]


def test_scan_searches_body_and_ignores_hidden_files(test_db, tmp_path):
    _, root = configure(test_db, tmp_path)
    root.mkdir(parents=True)
    (root / "visible.md").write_text("# 可见\n\nlocalStorage 知识", encoding="utf-8")
    hidden = root / ".private"
    hidden.mkdir()
    (hidden / "secret.md").write_text("# 不应出现\n\nlocalStorage", encoding="utf-8")
    result = learning_library.scan("LOCALstorage")
    assert [item["title"] for item in result["notes"]] == ["可见"]


def test_notes_page_is_read_only_obsidian_index(test_db, tmp_path):
    _, root = configure(test_db, tmp_path)
    root.mkdir(parents=True)
    (root / "新知识.md").write_text("# 新知识\n\n只存在于 Obsidian。", encoding="utf-8")
    html = app.app.test_client().get("/notes").get_data(as_text=True)
    assert "新知识" in html
    assert "在 Obsidian 打开" in html
    assert "新建学习笔记" not in html
    assert "删除这条笔记" not in html


def test_migrate_legacy_preserves_data_and_is_idempotent(test_db, tmp_path):
    _, root = configure(test_db, tmp_path)
    with test_db.cursor() as cur:
        cur.execute(
            "INSERT INTO notes (title, body, tags) VALUES (?, ?, ?)",
            ("学习标题", "学习正文", "css, 布局"),
        )
        cur.execute(
            "INSERT INTO reference_items (title, body, links, starred) VALUES (?, ?, ?, 1)",
            (
                "参考标题",
                "参考说明",
                json.dumps([{"name": "官网", "url": "https://example.com"}], ensure_ascii=False),
            ),
        )
    first = learning_library.migrate_legacy()
    second = learning_library.migrate_legacy()
    assert first["migrated"] == 2
    assert second["migrated"] == 0
    assert learning_library.pending_legacy_count() == 0
    note_text = (root / "学习标题.md").read_text(encoding="utf-8")
    ref_text = (root / "参考标题.md").read_text(encoding="utf-8")
    assert "legacy_id: note-" in note_text
    assert "学习正文" in note_text
    assert "  - css" in note_text
    assert "type:" not in note_text
    assert "starred:" not in ref_text
    assert "[官网](https://example.com)" in ref_text
    with test_db.cursor() as cur:
        assert cur.execute("SELECT count(*) FROM notes WHERE project_id IS NULL").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM reference_items").fetchone()[0] == 1


def test_notes_page_offers_safe_legacy_migration(test_db, tmp_path):
    _, root = configure(test_db, tmp_path)
    with test_db.cursor() as cur:
        cur.execute("INSERT INTO notes (title, body) VALUES ('旧笔记', '正文')")
    client = app.app.test_client()
    html = client.get("/notes").get_data(as_text=True)
    assert "检测到 1 份旧学习资料" in html
    response = client.post("/notes/migrate-legacy", follow_redirects=True)
    assert response.status_code == 200
    assert (root / "旧笔记.md").exists()
    assert "检测到 1 份旧学习资料" not in response.get_data(as_text=True)


def test_migration_never_overwrites_same_named_user_file(test_db, tmp_path):
    _, root = configure(test_db, tmp_path)
    root.mkdir(parents=True)
    original = root / "同名.md"
    original.write_text("# 用户自己的内容\n", encoding="utf-8")
    with test_db.cursor() as cur:
        cur.execute("INSERT INTO notes (title, body) VALUES ('同名', '旧数据库内容')")
    result = learning_library.migrate_legacy()
    assert result["migrated"] == 1
    assert original.read_text(encoding="utf-8") == "# 用户自己的内容\n"
    migrated = list(root.glob("同名（Dashboard note-*）.md"))
    assert len(migrated) == 1
    assert "旧数据库内容" in migrated[0].read_text(encoding="utf-8")
