"""测试公共设施：临时 DB + 临时 git 仓库。

所有测试都跑在 pytest 的 tmp_path 里，不碰真实 data.db、
真实 iCloud 备份目录和 ~/code 下的真实仓库。
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module  # noqa: E402


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """把 db 层指到临时文件并建好 schema。"""
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test-data.db"))
    # 环境变量里的 key 会让 get_ai_config 拿到真 key、意外调 AI
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db_module.init_db()
    return db_module


_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test",
}


def git(cwd, *args, env_extra=None):
    env = os.environ.copy()
    env.update(_GIT_IDENTITY)
    env.update(env_extra or {})
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, env=env,
    )


def make_commit(repo, filename, content, day_iso, msg=None):
    """在指定日期造一条 commit（回溯 author/committer date，好测按天查询）。"""
    (repo / filename).write_text(content, encoding="utf-8")
    git(repo, "add", filename)
    stamp = f"{day_iso} 12:00:00"
    git(
        repo, "commit", "-m", msg or f"edit {filename}",
        env_extra={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )


@pytest.fixture
def repo(tmp_path):
    """空的临时 git 仓库（无 remote、无 commit）。"""
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q")
    return r
