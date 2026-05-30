"""Filesystem + git scanning. Read-only operations against local repos."""

import os
import subprocess
import json
import hashlib
from datetime import datetime, date
from urllib.parse import urlparse


def run(cmd, cwd=None, timeout=10):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip(), result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", -1


def is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def list_repos_in(scan_path):
    """Return list of immediate subdirectories that are git repos."""
    repos = []
    if not os.path.isdir(scan_path):
        return repos
    try:
        for entry in sorted(os.listdir(scan_path)):
            if entry.startswith("."):
                continue
            full = os.path.join(scan_path, entry)
            if os.path.isdir(full) and is_git_repo(full):
                repos.append(full)
    except PermissionError:
        pass
    return repos


def parse_github_repo(remote_url):
    """git@github.com:user/repo.git or https://github.com/user/repo -> 'user/repo'"""
    if not remote_url:
        return None
    s = remote_url.strip()
    if s.startswith("git@"):
        # git@github.com:user/repo.git
        try:
            _, path = s.split(":", 1)
            return path.removesuffix(".git")
        except ValueError:
            return None
    if s.startswith("http"):
        try:
            p = urlparse(s).path.lstrip("/")
            return p.removesuffix(".git")
        except Exception:
            return None
    return None


def get_repo_info(path):
    """Pull all interesting git/file info about a repo."""
    info = {
        "path": path,
        "name": os.path.basename(path),
        "remote_url": None,
        "github_repo": None,
        "last_commit_date": None,
        "last_commit_msg": None,
        "first_commit_date": None,
        "has_claudemd": False,
        "claudemd_mtime": None,
        "has_unpushed": False,
        "unpushed_count": 0,
        "project_type": None,
    }
    if not is_git_repo(path):
        return info

    remote, _ = run(["git", "config", "--get", "remote.origin.url"], cwd=path)
    if remote:
        info["remote_url"] = remote
        info["github_repo"] = parse_github_repo(remote)

    last, _ = run(["git", "log", "-1", "--format=%ci|%s"], cwd=path)
    if last and "|" in last:
        d, msg = last.split("|", 1)
        info["last_commit_date"] = d[:10]
        info["last_commit_msg"] = msg

    first, _ = run(["git", "log", "--format=%ci", "--reverse"], cwd=path, timeout=15)
    if first:
        info["first_commit_date"] = first.splitlines()[0][:10]

    claudemd = os.path.join(path, "CLAUDE.md")
    if os.path.isfile(claudemd):
        info["has_claudemd"] = True
        info["claudemd_mtime"] = datetime.fromtimestamp(
            os.path.getmtime(claudemd)
        ).isoformat()

    # Check unpushed commits. Robust when there's no upstream/remote:
    # counts HEAD commits not present in ANY remote. With no remote at all,
    # every commit counts as unpushed (which is correct).
    unpushed, rc = run(
        ["git", "rev-list", "HEAD", "--not", "--remotes", "--count"], cwd=path
    )
    if rc == 0 and unpushed.isdigit():
        info["unpushed_count"] = int(unpushed)
        info["has_unpushed"] = int(unpushed) > 0
    info["has_remote"] = bool(info["remote_url"])

    for marker, ptype in [
        ("package.json", "node"),
        ("pyproject.toml", "python"),
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("index.html", "html"),
    ]:
        if os.path.isfile(os.path.join(path, marker)):
            info["project_type"] = ptype
            break

    return info


def get_unpushed_commits(path, limit=20):
    """Return list of commits on HEAD not present on any remote (short hash + subject)."""
    if not is_git_repo(path):
        return []
    out, rc = run(
        ["git", "log", "HEAD", "--not", "--remotes", f"-{limit}", "--format=%h%x1f%s"],
        cwd=path,
    )
    if rc != 0 or not out:
        return []
    commits = []
    for line in out.splitlines():
        if "\x1f" in line:
            h, subject = line.split("\x1f", 1)
            commits.append({"hash": h, "subject": subject})
    return commits


def get_todays_commits(path, since_iso=None):
    """Return list of dicts for today's commits + raw diff (capped)."""
    if not is_git_repo(path):
        return [], ""
    since = since_iso or date.today().isoformat()
    # Get commit list
    out, _ = run(
        ["git", "log", f"--since={since} 00:00", "--format=%H%x1f%ci%x1f%s", "--no-merges"],
        cwd=path,
    )
    commits = []
    for line in out.splitlines():
        if "\x1f" in line:
            parts = line.split("\x1f")
            if len(parts) >= 3:
                commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    # File stats
    stats, _ = run(
        ["git", "log", f"--since={since} 00:00", "--shortstat", "--no-merges", "--format="],
        cwd=path,
    )
    # Diff (limit ~20k chars)
    diff, _ = run(
        ["git", "log", f"--since={since} 00:00", "-p", "--no-merges", "--format=", "--unified=1"],
        cwd=path,
        timeout=15,
    )
    if len(diff) > 20000:
        diff = diff[:20000] + "\n... (truncated)"
    return commits, diff + "\n\n--- stats ---\n" + stats


def get_all_commits_by_day(path):
    """Return dict of {YYYY-MM-DD: [commit dicts]} for full git history."""
    if not is_git_repo(path):
        return {}
    out, _ = run(
        ["git", "log", "--format=%H%x1f%ci%x1f%s", "--no-merges"],
        cwd=path,
        timeout=30,
    )
    by_day = {}
    for line in out.splitlines():
        if "\x1f" not in line:
            continue
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        h, ci, subject = parts[0], parts[1], parts[2]
        day = ci[:10]
        by_day.setdefault(day, []).append({"hash": h, "date": ci, "subject": subject})
    return by_day


def get_todays_changed_files(path, since_iso=None):
    """Set of file paths modified in today's commits (precise — not a diff text search)."""
    if not is_git_repo(path):
        return set()
    since = since_iso or date.today().isoformat()
    out, _ = run(
        ["git", "log", f"--since={since} 00:00", "--name-only", "--format=", "--no-merges"],
        cwd=path,
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def get_changed_files_for_day(path, day_iso):
    """Set of file paths modified in a specific day's commits (precise, not diff search)."""
    if not is_git_repo(path):
        return set()
    after = f"{day_iso} 00:00"
    before = f"{day_iso} 23:59:59"
    out, _ = run(
        ["git", "log", f"--since={after}", f"--until={before}",
         "--name-only", "--format=", "--no-merges"],
        cwd=path,
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def get_unpushed_hashes(path):
    """Set of full commit hashes on HEAD not present on any remote.

    With no remote at all, every commit is 'unpushed' (correct). Lets us decide,
    for any given day, whether that day's commits have made it to a remote.
    """
    if not is_git_repo(path):
        return set()
    out, rc = run(["git", "rev-list", "HEAD", "--not", "--remotes"], cwd=path)
    if rc != 0 or not out:
        return set()
    return {h.strip() for h in out.splitlines() if h.strip()}


def get_commits_for_day(path, day_iso):
    """Return commits + diff for a specific date (YYYY-MM-DD)."""
    if not is_git_repo(path):
        return [], ""
    after = f"{day_iso} 00:00"
    before = f"{day_iso} 23:59:59"
    out, _ = run(
        ["git", "log", f"--since={after}", f"--until={before}",
         "--format=%H%x1f%ci%x1f%s", "--no-merges"],
        cwd=path,
    )
    commits = []
    for line in out.splitlines():
        if "\x1f" in line:
            parts = line.split("\x1f")
            if len(parts) >= 3:
                commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    diff, _ = run(
        ["git", "log", f"--since={after}", f"--until={before}",
         "-p", "--no-merges", "--format=", "--unified=1"],
        cwd=path,
        timeout=15,
    )
    if len(diff) > 20000:
        diff = diff[:20000] + "\n... (truncated)"
    return commits, diff


def memory_dir_for_project(path):
    """Map a project path to its ~/.claude memory directory.

    Claude Code stores per-project state under ~/.claude/projects/<encoded>/
    where encoded replaces / with - and prefixes with -.
    """
    if not path:
        return None
    abspath = os.path.abspath(path)
    encoded = "-" + abspath.lstrip("/").replace("/", "-")
    candidate = os.path.expanduser(f"~/.claude/projects/{encoded}/memory")
    return candidate


def memory_mtime(path):
    mem = memory_dir_for_project(path)
    if mem and os.path.isdir(mem):
        latest = 0
        for root, _, files in os.walk(mem):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    m = os.path.getmtime(fp)
                    if m > latest:
                        latest = m
                except OSError:
                    pass
        if latest:
            return datetime.fromtimestamp(latest).isoformat()
    return None


def ping_url(url, timeout=5):
    if not url:
        return False
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


def github_visibility(repo_slug, token=None):
    """Return 'public' / 'private' / None."""
    if not repo_slug:
        return None
    try:
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(f"https://api.github.com/repos/{repo_slug}", headers=headers, timeout=5)
        if r.status_code == 200:
            return "private" if r.json().get("private") else "public"
        if r.status_code == 404:
            return "private"  # 404 usually means private without auth
    except Exception:
        pass
    return None
