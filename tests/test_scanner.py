"""scanner 的按天 git 查询和未推检测——同步清单的事实来源。"""

from datetime import date, timedelta

import scanner
from conftest import git, make_commit


def iso(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_get_commits_for_day_only_that_day(repo):
    make_commit(repo, "a.txt", "前天的", iso(2))
    make_commit(repo, "b.txt", "昨天的", iso(1))
    commits, diff = scanner.get_commits_for_day(str(repo), iso(1))
    assert len(commits) == 1
    assert commits[0]["subject"] == "edit b.txt"
    assert "b.txt" in diff
    # 没有 commit 的日子返回空
    commits, _ = scanner.get_commits_for_day(str(repo), iso(5))
    assert commits == []


def test_get_changed_files_for_day(repo):
    make_commit(repo, "CLAUDE.md", "说明", iso(2))
    make_commit(repo, "code.py", "print(1)", iso(1))
    assert "CLAUDE.md" in scanner.get_changed_files_for_day(str(repo), iso(2))
    assert "CLAUDE.md" not in scanner.get_changed_files_for_day(str(repo), iso(1))


def test_unpushed_without_remote_counts_all(repo):
    make_commit(repo, "a.txt", "v1", iso(1))
    make_commit(repo, "a.txt", "v2", iso(0))
    assert len(scanner.get_unpushed_hashes(str(repo))) == 2
    info = scanner.get_repo_info(str(repo))
    assert info["unpushed_count"] == 2
    assert info["has_unpushed"] is True
    assert info["has_remote"] is False


def test_unpushed_after_partial_push(repo, tmp_path):
    git(tmp_path, "init", "--bare", "-q", "origin.git")
    make_commit(repo, "a.txt", "v1", iso(2))
    git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    make_commit(repo, "a.txt", "v2", iso(1))
    unpushed = scanner.get_unpushed_hashes(str(repo))
    assert len(unpushed) == 1


def test_get_repo_info_basics(repo):
    make_commit(repo, "CLAUDE.md", "说明", iso(3))
    make_commit(repo, "b.txt", "x", iso(1), msg="最后一条")
    info = scanner.get_repo_info(str(repo))
    assert info["first_commit_date"] == iso(3)
    assert info["last_commit_date"] == iso(1)
    assert info["last_commit_msg"] == "最后一条"
    assert info["has_claudemd"] is True
