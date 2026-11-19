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


def normalize_github_repo(value):
    """Accept either owner/repo or a GitHub remote/URL and return owner/repo."""
    if not value:
        return None
    value = value.strip()
    parsed = parse_github_repo(value)
    return parsed or value.strip("/").removesuffix(".git")


ROADMAP_FILES = ("ROADMAP.md", "docs/ROADMAP.md")


def roadmap_file(path):
    """Return the preferred Roadmap path relative to the repository."""
    if not path:
        return None
    for relative in ROADMAP_FILES:
        candidate = os.path.join(path, relative)
        if relative.startswith("docs/") and os.path.islink(os.path.dirname(candidate)):
            continue
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            return relative
    return None


def roadmap_at_commit(path, commit_hash):
    entries, rc = run(["git", "ls-tree", commit_hash, "--", *ROADMAP_FILES], cwd=path)
    if rc != 0:
        return None
    regular_files = {
        entry.partition("\t")[2] for entry in entries.splitlines()
        if entry.startswith(("100644 blob ", "100755 blob "))
    }
    return next((name for name in ROADMAP_FILES if name in regular_files), None)


def get_repo_info(path):
    """Pull all interesting git/file info about a repo."""
    info = {
        "path": path,
        "name": os.path.basename(path),
        "remote_url": None,
        "github_repo": None,
        "upstream_url": None,
        "upstream_repo": None,
        "last_commit_date": None,
        "last_commit_at": None,
        "last_commit_msg": None,
        "first_commit_date": None,
        "has_claudemd": False,
        "claudemd_mtime": None,
        "has_agentsmd": False,
        "roadmap_file": roadmap_file(path),
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

    upstream, _ = run(["git", "config", "--get", "remote.upstream.url"], cwd=path)
    if upstream:
        info["upstream_url"] = upstream
        info["upstream_repo"] = parse_github_repo(upstream)

    last, _ = run(["git", "log", "-1", "--format=%cI|%s"], cwd=path)
    if last and "|" in last:
        d, msg = last.split("|", 1)
        info["last_commit_date"] = d[:10]
        info["last_commit_at"] = d
        info["last_commit_msg"] = msg

    first, _ = run(["git", "log", "--format=%ci", "--reverse"], cwd=path, timeout=15)
    if first:
        info["first_commit_date"] = first.splitlines()[0][:10]

    # AGENTS.md is canonical. CLAUDE.md remains a fallback for older/reference
    # repositories that have not migrated their project guide.
    instruction_file = next(
        (
            os.path.join(path, name)
            for name in ("AGENTS.md", "CLAUDE.md")
            if os.path.isfile(os.path.join(path, name))
        ),
        None,
    )
    if instruction_file:
        info["has_claudemd"] = True
        info["has_agentsmd"] = os.path.basename(instruction_file) == "AGENTS.md"
        info["claudemd_mtime"] = datetime.fromtimestamp(
            os.path.getmtime(instruction_file)
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


def _github_fetch_remotes(path):
    """Map GitHub repository names to remotes whose tracking refs come from them."""
    output, _ = run(["git", "remote", "-v"], cwd=path)
    remotes = {}
    for line in output.splitlines():
        name, _, entry = line.partition("\t")
        url, _, direction = entry.rpartition(" ")
        if direction != "(fetch)":
            continue
        if url.lower().startswith("git@github.com:"):
            repo_path = url.partition(":")[2]
        else:
            try:
                parsed = urlparse(url)
                if parsed.hostname not in {"github.com", "ssh.github.com"}:
                    continue
                repo_path = parsed.path
            except ValueError:
                continue
        repo = normalize_github_repo(repo_path)
        if repo:
            remotes.setdefault(repo.lower(), name)
    return remotes


def _remote_default_branch(path, remote, repo, token):
    prefix = f"refs/remotes/{remote}/"
    head_ref = prefix + "HEAD"
    target, rc = run(["git", "rev-parse", "--verify", "--quiet", "--symbolic-full-name", head_ref], cwd=path)
    if rc == 0 and target.startswith(prefix) and target != head_ref:
        return target.removeprefix(prefix)

    # A remote added after cloning may have tracking branches but no local HEAD.
    refs, rc = run(["git", "for-each-ref", "--count=1", "--format=%(refname)", prefix], cwd=path)
    if rc != 0 or not refs:
        return None
    metadata = github_repo_metadata(repo, token)
    return metadata.get("default_branch") if metadata else None


def compare_github_default_branches(path, private_repo, public_repo, token=None):
    """Compare tracked default branches that share a Git ancestor."""
    if not path or not is_git_repo(path):
        return None
    remotes = _github_fetch_remotes(path)
    sides = []
    refs = []
    for value in (private_repo, public_repo):
        repo = normalize_github_repo(value)
        remote = remotes.get(repo.lower()) if repo else None
        if not remote:
            return None
        branch = _remote_default_branch(path, remote, repo, token)
        if not branch:
            return None
        refs.append(f"refs/remotes/{remote}/{branch}")
        sides.append({"repo": repo, "branch": branch, "commits": []})

    # Use the same branch tips for both queries even if a fetch updates the refs.
    tip_hashes, rc = run(["git", "show-ref", "--verify", "--hash", *refs], cwd=path)
    if rc != 0:
        return None
    tips = tip_hashes.splitlines()
    _, rc = run(["git", "merge-base", *tips], cwd=path)
    if rc != 0:
        return None

    output, rc = run(
        ["git", "log", "--left-right", "--no-notes", "--no-show-signature",
         "--format=%m%x00%H%x00%s", "...".join(tips), "--"],
        cwd=path,
    )
    if rc != 0:
        return None
    commits_by_side = {"<": sides[0]["commits"], ">": sides[1]["commits"]}
    for line in output.split("\n") if output else []:
        side, commit_hash, subject = line.split("\0", 2)
        commits_by_side[side].append({"hash": commit_hash, "subject": subject})
    return sides


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


def github_repo_metadata(repo_slug, token=None):
    """Return visibility, fork source and default branch when GitHub provides them."""
    repo_slug = normalize_github_repo(repo_slug)
    if not repo_slug:
        return None
    try:
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(f"https://api.github.com/repos/{repo_slug}", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            source = data.get("source") or data.get("parent") or {}
            return {
                "visibility": "private" if data.get("private") else "public",
                "forked_from": source.get("full_name") if data.get("fork") else None,
                "default_branch": data.get("default_branch"),
            }
        if r.status_code == 404:
            # 未带权限访问私有仓库通常是 404；这时只能保留原有的可见性推断，
            # 不能判断 fork 来源
            return {"visibility": "private", "forked_from": None, "default_branch": None}
    except Exception:
        pass
    return None


def github_visibility(repo_slug, token=None):
    """Backward-compatible visibility-only wrapper."""
    metadata = github_repo_metadata(repo_slug, token)
    return metadata.get("visibility") if metadata else None
