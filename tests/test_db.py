"""db 层：迁移幂等 + 备份。"""

import os
import sqlite3
from datetime import datetime


def test_init_db_idempotent(test_db):
    test_db.init_db()  # 第二次跑不该报错、不该重复迁移
    with test_db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM categories")
        assert cur.fetchone()["c"] == len(test_db.PRESET_CATEGORIES)


def test_backup_creates_overwrites_and_prunes(test_db, tmp_path, monkeypatch):
    cloud = tmp_path / "cloud" / "backups"  # 父目录存在 → 走"iCloud"分支
    (tmp_path / "cloud").mkdir()
    monkeypatch.setattr(test_db, "BACKUP_ICLOUD_DIR", str(cloud))

    target = test_db.backup_db(keep=3)
    assert target and os.path.dirname(target) == str(cloud)
    # 备份是合法的 SQLite 库，能读出 settings 表
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] >= 1
    conn.close()
    # 同日再备份 → 覆盖同一个文件，不新增
    assert test_db.backup_db(keep=3) == target
    assert len(os.listdir(cloud)) == 1
    # 超过 keep 份时裁掉最旧的
    for d in ("20200101", "20200102", "20200103"):
        (cloud / f"data-{d}.db").write_text("旧备份")
    test_db.backup_db(keep=3)
    remaining = sorted(os.listdir(cloud))
    assert len(remaining) == 3
    assert f"data-{datetime.now().strftime('%Y%m%d')}.db" in remaining
    assert "data-20200101.db" not in remaining


def test_backup_falls_back_to_local(test_db, tmp_path, monkeypatch):
    # iCloud 根目录不存在 → 退回本地 backups/
    monkeypatch.setattr(
        test_db, "BACKUP_ICLOUD_DIR", str(tmp_path / "missing" / "x" / "backups")
    )
    monkeypatch.setattr(test_db, "BACKUP_LOCAL_DIR", str(tmp_path / "local-backups"))
    target = test_db.backup_db()
    assert target and target.startswith(str(tmp_path / "local-backups"))


def test_configured_backup_dir_wins(test_db, tmp_path, monkeypatch):
    # 显式设置的备份目录优先于 iCloud 自动检测
    monkeypatch.setattr(test_db, "BACKUP_ICLOUD_DIR", str(tmp_path / "icloud" / "backups"))
    (tmp_path / "icloud").mkdir()  # 让 iCloud 分支"可用"，验证设置确实压过它
    custom = tmp_path / "my-cloud"
    test_db.set_setting("backup_dir", str(custom))
    assert test_db.resolve_backup_dir() == str(custom)
    target = test_db.backup_db()
    assert target and target.startswith(str(custom))


def test_configured_backup_dir_expands_user(test_db, monkeypatch):
    # ~ 会被展开
    test_db.set_setting("backup_dir", "~/some-backup-dir")
    assert test_db.resolve_backup_dir() == os.path.expanduser("~/some-backup-dir")
