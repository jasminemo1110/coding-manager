"""Flask app entry. Routes for dashboard, project detail, notes, media, settings, exports, sync."""

import os
import json
import threading
import datetime as dt
from datetime import date, datetime
from urllib.parse import quote
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    Response,
    abort,
    flash,
)

import db
import scanner
import ai
import obsidian

app = Flask(__name__)
app.secret_key = "coding-dashboard-local"


@app.before_request
def ensure_db():
    if not getattr(app, "_db_inited", False):
        db.init_db()
        app._db_inited = True
    # 每天第一次访问时自动备份 data.db（失败则下个请求重试）
    today = date.today().isoformat()
    if db.get_setting("last_backup_date") != today:
        if db.backup_db():
            db.set_setting("last_backup_date", today)


# ---------- Helpers ----------

def project_to_dict(row):
    return dict(row)


def get_project(pid):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE id = ?", (pid,))
        row = cur.fetchone()
        return dict(row) if row else None


def all_projects(include_excluded=False):
    with db.cursor() as cur:
        if include_excluded:
            cur.execute("SELECT * FROM projects ORDER BY sort_order, id")
        else:
            cur.execute(
                "SELECT * FROM projects WHERE excluded_from_scan = 0 ORDER BY sort_order, id"
            )
        return [dict(r) for r in cur.fetchall()]


def known_paths():
    with db.cursor() as cur:
        cur.execute("SELECT path FROM projects WHERE path IS NOT NULL")
        return {r["path"] for r in cur.fetchall()}


def scan_suggestions():
    """Return list of git repos that exist on disk but aren't yet in projects."""
    known = known_paths()
    suggestions = []
    for scan_path in db.get_scan_paths():
        for repo in scanner.list_repos_in(scan_path):
            if repo not in known:
                suggestions.append({
                    "path": repo,
                    "name": os.path.basename(repo),
                    "parent": scan_path,
                })
    return suggestions


def media_count(pid):
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM media_items WHERE project_id = ?", (pid,))
        return cur.fetchone()["c"]


def media_for_project(pid):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM media_items WHERE project_id = ? ORDER BY id DESC", (pid,))
        return [dict(r) for r in cur.fetchall()]


def todos_for_project(pid):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM project_todos WHERE project_id = ? ORDER BY done, id DESC", (pid,)
        )
        return [dict(r) for r in cur.fetchall()]


CHECK_LABELS = {
    "claudemd_updated": "CLAUDE.md",
    "memory_updated": "memory",
    "pushed_to_github": "GitHub",
    "deployed": "部署",
}
CHECK_FIELDS = ["claudemd_updated", "memory_updated", "pushed_to_github", "deployed"]


def log_disabled_set(log):
    """Set of checklist fields the user has removed from this specific day's checklist."""
    raw = (log.get("disabled_checks") or "") if log else ""
    return {f for f in raw.split(",") if f}


def log_active_fields(log):
    """Checklist fields still active (shown / counted) for this day's log."""
    disabled = log_disabled_set(log)
    return [f for f in CHECK_FIELDS if f not in disabled]


def default_disabled_for(project, has_claudemd=True):
    """disabled_checks string for a newly-created daily log of this project."""
    disabled = []
    if not project.get("tracks_deployment"):
        disabled.append("deployed")
    if not has_claudemd:
        disabled.append("claudemd_updated")
    return ",".join(disabled)


def latest_log_with_commits(pid):
    """Most recent daily log that actually has commits (skips empty placeholder rows)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_logs WHERE project_id = ? "
            "AND raw_commits_json IS NOT NULL AND raw_commits_json != '' AND raw_commits_json != '[]' "
            "ORDER BY date DESC LIMIT 1",
            (pid,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def save_repo_snapshot(pid, info):
    """把 get_repo_info 的结果落库。看板读快照即可，不用每次开一堆 git 子进程。"""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET repo_snapshot = ?, repo_snapshot_at = ? WHERE id = ?",
            (
                json.dumps(info, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                pid,
            ),
        )


def enrich_project(p, live=False):
    """Add filesystem/git data to a project row (non-mutating).

    默认读同步时落库的 repo 快照；live=True（项目详情页）或尚无快照时
    现场扫一次并回写。快照时效靠同步和详情页访问兜底刷新。
    """
    p = dict(p)
    info = None
    if not live and p.get("repo_snapshot"):
        try:
            info = json.loads(p["repo_snapshot"])
        except Exception:
            info = None
    if info is None:
        info = scanner.get_repo_info(p["path"]) if p.get("path") else {}
        if p.get("path"):
            save_repo_snapshot(p["id"], info)
    p["live"] = info
    p["stage_label"] = db.STAGE_LABELS.get(p.get("stage"), p.get("stage"))
    p["media_count"] = media_count(p["id"])
    p["media_items"] = media_for_project(p["id"])
    p["project_todos"] = todos_for_project(p["id"])
    p["latest_log"] = latest_log_with_commits(p["id"])
    p["categories"] = categories_for_project(p["id"])
    p["category_ids"] = {c["id"] for c in p["categories"]}
    return p


def build_todos(projects):
    """Cross-project checklist status: for each project's latest active day,
    report which (still-active) checklist items are unchecked."""
    todos = []
    for p in projects:
        live = p.get("live") or {}
        if live.get("unpushed_count", 0) > 0:
            todos.append({
                "project_id": p["id"],
                "project_name": p["name"],
                "kind": "unpushed",
                "text": f"{live['unpushed_count']} 个 commit 未推 GitHub",
            })
        log = p.get("latest_log")
        if log and not log.get("checklist_reminder_ignored"):
            fields = log_active_fields(log)
            unchecked = [CHECK_LABELS[f] for f in fields if not log.get(f)]
            if unchecked:
                # 首页只报「最新那天有未完成项」，具体是哪几项留到悬停 tooltip 里
                todos.append({
                    "project_id": p["id"],
                    "project_name": p["name"],
                    "kind": "checklist_pending",
                    "log_id": log["id"],
                    "date": log["date"],
                    "unchecked_labels": unchecked,
                    "text": f"{log['date']} 有未完成清单项",
                })
    return todos


def panel_todos():
    """Checkable todos for the dashboard top panel:
    global manual todos (project_id IS NULL) + important project todos.
    已归档（完成于更早某天）的不再列出——它们进了历史回收站。"""
    with db.cursor() as cur:
        cur.execute(
            "SELECT pt.*, p.name AS project_name FROM project_todos pt "
            "LEFT JOIN projects p ON p.id = pt.project_id "
            "WHERE (pt.project_id IS NULL OR pt.important = 1) "
            "AND NOT (pt.done = 1 AND pt.done_at IS NOT NULL AND pt.done_at < ?) "
            "ORDER BY pt.done, pt.important DESC, pt.id DESC",
            (date.today().isoformat(),),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------- Dashboard ----------

def _make_level_fn(max_commits):
    """Return a fn mapping a day's commit count to a shade level 0-4,
    dynamically scaled to the busiest day in the displayed window so the
    contrast stays meaningful as daily volume grows over time."""
    if max_commits <= 0:
        return lambda n: 0

    def level(n):
        if n <= 0:
            return 0
        ratio = n / max_commits
        if ratio <= 0.25:
            return 1
        if ratio <= 0.5:
            return 2
        if ratio <= 0.75:
            return 3
        return 4

    return level


def _day_commit_index(since_iso=None):
    """date(iso) -> {'projects': [names], 'commits': int}, from daily_logs.

    since_iso 限定下界：热力图/统计只关心一段窗口，别每次全表解析所有历史 JSON。
    """
    with db.cursor() as cur:
        sql = (
            "SELECT dl.date AS d, p.name AS name, dl.raw_commits_json AS rcj FROM daily_logs dl "
            "JOIN projects p ON p.id = dl.project_id"
        )
        params = []
        if since_iso:
            sql += " WHERE dl.date >= ?"
            params.append(since_iso)
        cur.execute(sql + " ORDER BY dl.date", params)
        rows = cur.fetchall()
    by_date = {}
    for r in rows:
        entry = by_date.setdefault(r["d"], {"projects": [], "commits": 0})
        entry["projects"].append(r["name"])
        try:
            entry["commits"] += len(json.loads(r["rcj"])) if r["rcj"] else 0
        except Exception:
            pass
    return by_date


def commit_totals():
    """Total commits across all projects for today / this week / month / year."""
    today = date.today()
    week_start = today - dt.timedelta(days=today.weekday())  # Monday
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    # 一月初的"本周"可能跨到去年，下界取两者更早的
    by_date = _day_commit_index(min(year_start, week_start).isoformat())
    totals = {"today": 0, "week": 0, "month": 0, "year": 0}
    for iso, entry in by_date.items():
        try:
            d = date.fromisoformat(iso)
        except Exception:
            continue
        n = entry["commits"]
        if d == today:
            totals["today"] += n
        if d >= week_start:
            totals["week"] += n
        if d >= month_start:
            totals["month"] += n
        if d >= year_start:
            totals["year"] += n
    return totals


def build_heatmap(weeks_back=26):
    """GitHub-style contribution grid from daily_logs, shaded by commit count.
    Shade levels are scaled dynamically to the busiest day in the window."""
    today = date.today()
    start = today - dt.timedelta(days=weeks_back * 7 - 1)
    start -= dt.timedelta(days=start.weekday())  # align to Monday
    by_date = _day_commit_index(start.isoformat())

    # First pass: find the busiest day within the displayed window
    max_commits = 0
    d = start
    while d <= today:
        iso = d.isoformat()
        c = by_date.get(iso, {}).get("commits", 0)
        if c > max_commits:
            max_commits = c
        d += dt.timedelta(days=1)
    level_fn = _make_level_fn(max_commits)

    weeks = []
    d = start
    prev_month = None
    while d <= today:
        days = []
        month_label = None
        for _ in range(7):
            if d <= today:
                iso = d.isoformat()
                entry = by_date.get(iso, {"projects": [], "commits": 0})
                projs = entry["projects"]
                commits = entry["commits"]
                days.append({
                    "date": iso,
                    "level": level_fn(commits),
                    "commits": commits,
                    "project_count": len(projs),
                    "projects": projs,
                })
                if d.day <= 7 and d.month != prev_month:
                    month_label = f"{d.month}月"
                    prev_month = d.month
            else:
                days.append(None)
            d += dt.timedelta(days=1)
        weeks.append({"days": days, "month": month_label})
    return weeks


def local_project_count():
    """Number of tracked projects that have an actual local folder on disk."""
    count = 0
    for p in all_projects():
        path = p.get("path")
        if path and os.path.isdir(path):
            count += 1
    return count


def all_categories():
    with db.cursor() as cur:
        cur.execute("SELECT id, name FROM categories ORDER BY sort_order, id")
        return [dict(r) for r in cur.fetchall()]


def categories_for_project(pid):
    with db.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.name FROM project_categories pc "
            "JOIN categories c ON c.id = pc.category_id "
            "WHERE pc.project_id = ? ORDER BY c.sort_order, c.id",
            (pid,),
        )
        return [dict(r) for r in cur.fetchall()]


@app.route("/")
def dashboard():
    stage_param = request.args.get("stage", "")
    category_param = request.args.get("category", "")
    stage_filter_list = [s for s in stage_param.split(",") if s and s != "all"]
    category_filter_list = [c for c in category_param.split(",") if c and c != "all"]
    # 「暂停中」和阶段正交，所以单独一组筛选：all 全都要 / active 只看在推进的 / paused 只看停着的
    status_filter = request.args.get("status", "all")
    if status_filter not in ("all", "active", "paused"):
        status_filter = "all"
    sort_by = request.args.get("sort", "updated")
    projects = all_projects()
    enriched = [enrich_project(p) for p in projects]
    if stage_filter_list:
        enriched = [p for p in enriched if p.get("stage") in stage_filter_list]
    if status_filter == "active":
        enriched = [p for p in enriched if not p.get("paused")]
    elif status_filter == "paused":
        enriched = [p for p in enriched if p.get("paused")]
    if category_filter_list:
        enriched = [
            p for p in enriched
            if any(c["name"] in category_filter_list for c in p.get("categories", []))
        ]

    def first_commit(p):
        return (p.get("live") or {}).get("first_commit_date") or ""

    def last_commit(p):
        return (p.get("live") or {}).get("last_commit_date") or ""

    if sort_by == "created":
        # projects with no code (no first commit) sink to the bottom
        enriched.sort(key=lambda p: (first_commit(p) == "", first_commit(p)))
    elif sort_by == "updated":
        enriched.sort(key=lambda p: (last_commit(p) == "", last_commit(p)), reverse=False)
        enriched.sort(key=lambda p: last_commit(p), reverse=True)
    else:  # stage
        order = {s: i for i, s in enumerate(db.STAGE_ORDER)}
        enriched.sort(key=lambda p: order.get(p.get("stage"), 99))

    # 暂停的项目沉底——不管哪种排序。sort 是稳定的，组内顺序照上面排好的来
    enriched.sort(key=lambda p: 1 if p.get("paused") else 0)

    todos = build_todos(enriched)
    suggestions = scan_suggestions()
    heatmap = build_heatmap()
    totals = commit_totals()
    return render_template(
        "dashboard.html",
        projects=enriched,
        commit_totals=totals,
        local_project_count=local_project_count(),
        stage_filter_list=stage_filter_list,
        category_filter_list=category_filter_list,
        stage_filter_str=",".join(stage_filter_list),
        category_filter_str=",".join(category_filter_list),
        status_filter=status_filter,
        sort_by=sort_by,
        stage_labels=db.STAGE_LABELS,
        stage_order=db.STAGE_ORDER,
        categories=all_categories(),
        todos=todos,
        panel_todos=panel_todos(),
        suggestions=suggestions,
        heatmap=heatmap,
    )


@app.route("/todo/global/add", methods=["POST"])
def global_todo_add():
    text = request.form.get("text", "").strip()
    if text:
        with db.cursor() as cur:
            cur.execute("INSERT INTO project_todos (text) VALUES (?)", (text,))
    redirect_to = request.form.get("redirect_to") or url_for("dashboard")
    return redirect(redirect_to)


# ---------- Project CRUD ----------

@app.route("/project/add", methods=["POST"])
def project_add():
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip() or None
    stage = request.form.get("stage", "sprout")
    category_ids = request.form.getlist("category_ids")
    online_url = request.form.get("online_url", "").strip() or None
    if not name:
        flash("项目名不能为空")
        return redirect(url_for("dashboard"))
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, stage, online_url) VALUES (?, ?, ?, ?)",
            (name, path, stage, online_url),
        )
        new_id = cur.lastrowid
        for cid in category_ids:
            cur.execute(
                "INSERT OR IGNORE INTO project_categories (project_id, category_id) VALUES (?, ?)",
                (new_id, cid),
            )
    return redirect(url_for("dashboard"))


@app.route("/project/add-from-scan", methods=["POST"])
def project_add_from_scan():
    path = request.form.get("path")
    if not path:
        return redirect(url_for("dashboard"))
    name = os.path.basename(path)
    info = scanner.get_repo_info(path)
    github_token = db.get_setting("github_token")
    visibility = None
    if info.get("github_repo"):
        visibility = scanner.github_visibility(info["github_repo"], github_token)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, github_repo, github_visibility, stage) "
            "VALUES (?, ?, ?, ?, 'in_progress')",
            (name, path, info.get("github_repo"), visibility),
        )
        new_id = cur.lastrowid
    save_repo_snapshot(new_id, info)
    return redirect(url_for("dashboard"))


@app.route("/project/exclude-scan", methods=["POST"])
def project_exclude_scan():
    path = request.form.get("path")
    if not path:
        return redirect(url_for("dashboard"))
    name = os.path.basename(path)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, excluded_from_scan, stage) VALUES (?, ?, 1, 'sprout')",
            (name, path),
        )
    return redirect(url_for("dashboard"))


@app.route("/project/<int:pid>")
def project_detail(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    enriched = enrich_project(p, live=True)
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_logs WHERE project_id = ? ORDER BY date DESC", (pid,)
        )
        logs = [dict(r) for r in cur.fetchall()]
        for log in logs:
            if log.get("raw_commits_json"):
                try:
                    log["commits"] = json.loads(log["raw_commits_json"])
                except Exception:
                    log["commits"] = []
            else:
                log["commits"] = []
            log["active_fields"] = log_active_fields(log)
            log["disabled_fields"] = sorted(log_disabled_set(log))
        cur.execute(
            "SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (pid,)
        )
        notes = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM media_items WHERE project_id = ? ORDER BY id DESC", (pid,)
        )
        media = [dict(r) for r in cur.fetchall()]
        today = date.today().isoformat()
        cur.execute(
            "SELECT * FROM project_todos WHERE project_id = ? "
            "AND NOT (done = 1 AND done_at IS NOT NULL AND done_at < ?) "
            "ORDER BY done, important DESC, id DESC",
            (pid, today),
        )
        todos = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) AS c FROM project_todos WHERE project_id = ? "
            "AND done = 1 AND done_at IS NOT NULL AND done_at < ?",
            (pid, today),
        )
        archived_todo_count = cur.fetchone()["c"]
    # link notes <-> daily logs both ways
    log_date_by_id = {log["id"]: log["date"] for log in logs}
    notes_by_log = {}
    for n in notes:
        if n.get("linked_daily_log_id"):
            n["linked_date"] = log_date_by_id.get(n["linked_daily_log_id"])
            notes_by_log.setdefault(n["linked_daily_log_id"], []).append(n)
    for log in logs:
        log["linked_notes"] = notes_by_log.get(log["id"], [])
    unpushed_commits = scanner.get_unpushed_commits(p["path"]) if p.get("path") else []
    return render_template(
        "project.html",
        p=enriched,
        logs=logs,
        notes=notes,
        media=media,
        todos=todos,
        archived_todo_count=archived_todo_count,
        unpushed_commits=unpushed_commits,
        stage_labels=db.STAGE_LABELS,
        stage_order=db.STAGE_ORDER,
        categories=all_categories(),
        check_labels=CHECK_LABELS,
        check_fields=CHECK_FIELDS,
    )


@app.route("/project/<int:pid>/todo/add", methods=["POST"])
def project_todo_add(pid):
    text = request.form.get("text", "").strip()
    if text:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO project_todos (project_id, text) VALUES (?, ?)", (pid, text)
            )
    redirect_to = request.form.get("redirect_to") or url_for("project_detail", pid=pid)
    return redirect(redirect_to)


@app.route("/todo/<int:tid>/toggle", methods=["POST"])
def project_todo_toggle(tid):
    with db.cursor() as cur:
        cur.execute("SELECT done FROM project_todos WHERE id = ?", (tid,))
        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False})
        new_done = 0 if row["done"] else 1
        if new_done:
            # 记完成日期：当天仍留在原地，第二天才归档进历史回收站
            cur.execute(
                "UPDATE project_todos SET done = 1, done_at = ? WHERE id = ?",
                (date.today().isoformat(), tid),
            )
        else:
            cur.execute(
                "UPDATE project_todos SET done = 0, done_at = NULL WHERE id = ?", (tid,)
            )
    return jsonify({"ok": True, "done": new_done})


@app.route("/todo/<int:tid>/edit", methods=["POST"])
def project_todo_edit(tid):
    text = (request.form.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "reason": "empty"})
    with db.cursor() as cur:
        cur.execute("UPDATE project_todos SET text = ? WHERE id = ?", (text, tid))
    return jsonify({"ok": True, "text": text})


@app.route("/todo/<int:tid>/delete", methods=["POST"])
def project_todo_delete(tid):
    pid = request.form.get("project_id")
    with db.cursor() as cur:
        cur.execute("DELETE FROM project_todos WHERE id = ?", (tid,))
    redirect_to = request.form.get("redirect_to") or url_for("project_detail", pid=pid)
    return redirect(redirect_to)


@app.route("/todo/<int:tid>/important", methods=["POST"])
def project_todo_important(tid):
    with db.cursor() as cur:
        cur.execute("UPDATE project_todos SET important = 1 - important WHERE id = ?", (tid,))
        cur.execute("SELECT important FROM project_todos WHERE id = ?", (tid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "important": row["important"] if row else 0})


@app.route("/todo/<int:tid>/restore", methods=["POST"])
def project_todo_restore(tid):
    """把归档的待办还原回未完成状态，重新回到活动清单。"""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE project_todos SET done = 0, done_at = NULL WHERE id = ?", (tid,)
        )
    return redirect(request.form.get("redirect_to") or url_for("todos_history"))


@app.route("/log/<int:log_id>/ignore-checklist", methods=["POST"])
def log_ignore_checklist(log_id):
    """把某天日志的清单提醒从首页静音——只影响首页那一条，不动清单本身。"""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE daily_logs SET checklist_reminder_ignored = 1 WHERE id = ?", (log_id,)
        )
    return jsonify({"ok": True})


@app.route("/todos/history")
def todos_history():
    """历史回收站：完成于更早某天的待办，按项目分组（无项目=全局，沉底）。"""
    today = date.today().isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT pt.*, p.name AS project_name FROM project_todos pt "
            "LEFT JOIN projects p ON p.id = pt.project_id "
            "WHERE pt.done = 1 AND pt.done_at IS NOT NULL AND pt.done_at < ? "
            "ORDER BY pt.done_at DESC, pt.id DESC",
            (today,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    groups = {}
    for r in rows:
        g = groups.setdefault(
            r["project_id"],
            {
                "project_id": r["project_id"],
                "project_name": r["project_name"] or "全局 · 无项目",
                "todos": [],
            },
        )
        g["todos"].append(r)
    ordered = sorted(
        groups.values(),
        key=lambda g: (g["project_id"] is None, g["project_name"] or ""),
    )
    return render_template("todos_history.html", groups=ordered, total=len(rows))


@app.route("/project/<int:pid>/update", methods=["POST"])
def project_update(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    new_repo = (request.form.get("github_repo") or "").strip() or None
    new_repo_public = (request.form.get("github_repo_public") or "").strip() or None
    # Always re-check visibility on save — handles stale data without forcing the user to change the field
    if new_repo:
        token = db.get_setting("github_token")
        visibility = scanner.github_visibility(new_repo, token)
    else:
        visibility = None
    category_ids = request.form.getlist("category_ids")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET name=?, path=?, stage=?, paused=?, online_url=?, online_status=?, "
            "tracks_deployment=?, github_repo=?, github_visibility=?, github_repo_public=? WHERE id=?",
            (
                request.form.get("name", p["name"]).strip(),
                (request.form.get("path") or "").strip() or None,
                request.form.get("stage", p["stage"]),
                1 if request.form.get("paused") else 0,
                (request.form.get("online_url") or "").strip() or None,
                1 if request.form.get("online_status") else 0,
                1 if request.form.get("tracks_deployment") else 0,
                new_repo,
                visibility,
                new_repo_public,
                pid,
            ),
        )
        cur.execute("DELETE FROM project_categories WHERE project_id=?", (pid,))
        for cid in category_ids:
            cur.execute(
                "INSERT OR IGNORE INTO project_categories (project_id, category_id) VALUES (?, ?)",
                (pid, cid),
            )
    return redirect(url_for("project_detail", pid=pid))


@app.route("/project/<int:pid>/refresh-github", methods=["POST"])
def project_refresh_github(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    token = db.get_setting("github_token")
    vis = scanner.github_visibility(p["github_repo"], token) if p.get("github_repo") else None
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET github_visibility=? WHERE id=?", (vis, pid))
    return jsonify({"ok": True, "visibility": vis})


@app.route("/project/<int:pid>/stage", methods=["POST"])
def project_stage(pid):
    stage = request.form.get("stage")
    if stage not in db.STAGE_ORDER:
        return jsonify({"ok": False, "reason": "invalid stage"}), 400
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET stage=? WHERE id=?", (stage, pid))
    return jsonify({"ok": True, "stage": stage})


@app.route("/project/<int:pid>/category/toggle", methods=["POST"])
def project_category_toggle(pid):
    cid = request.form.get("category_id")
    if not cid:
        return jsonify({"ok": False, "reason": "no category_id"}), 400
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM project_categories WHERE project_id=? AND category_id=?",
            (pid, cid),
        )
        exists = cur.fetchone()
        if exists:
            cur.execute(
                "DELETE FROM project_categories WHERE project_id=? AND category_id=?",
                (pid, cid),
            )
            on = False
        else:
            cur.execute(
                "INSERT OR IGNORE INTO project_categories (project_id, category_id) VALUES (?, ?)",
                (pid, cid),
            )
            on = True
    return jsonify({"ok": True, "on": on})


@app.route("/project/<int:pid>/pause", methods=["POST"])
def project_pause_toggle(pid):
    """「暂停中」是叠加状态，不动 stage——项目停在哪一步照旧记着。"""
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET paused = 1 - paused WHERE id = ?", (pid,))
        cur.execute("SELECT paused FROM projects WHERE id = ?", (pid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "paused": row["paused"] if row else 0})


@app.route("/project/<int:pid>/star", methods=["POST"])
def project_star_toggle(pid):
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET starred = 1 - starred WHERE id = ?", (pid,))
        cur.execute("SELECT starred FROM projects WHERE id = ?", (pid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "starred": row["starred"] if row else 0})


# ---------- Category management ----------

@app.route("/category/add", methods=["POST"])
def category_add():
    name = (request.form.get("name") or "").strip()
    if name:
        with db.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM categories")
            order = cur.fetchone()["n"]
            cur.execute(
                "INSERT OR IGNORE INTO categories (name, sort_order) VALUES (?, ?)",
                (name, order),
            )
    return redirect(url_for("settings"))


@app.route("/category/rename", methods=["POST"])
def category_rename():
    cid = request.form.get("id")
    new_name = (request.form.get("new_name") or "").strip()
    if cid and new_name:
        with db.cursor() as cur:
            cur.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cid))
    return redirect(url_for("settings"))


@app.route("/category/delete", methods=["POST"])
def category_delete():
    cid = request.form.get("id")
    if cid:
        with db.cursor() as cur:
            # project_categories 里的关联行靠外键 ON DELETE CASCADE 清掉
            cur.execute("DELETE FROM categories WHERE id=?", (cid,))
    return redirect(url_for("settings"))


@app.route("/project/<int:pid>/delete", methods=["POST"])
def project_delete(pid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id=?", (pid,))
    return redirect(url_for("dashboard"))


# ---------- Sync ----------

# 「同步到最新」回看上限：水位线缺失/异常时，一次最多回扫这么多天，避免误扫几个月旧历史浪费 AI 调用
MAX_SYNC_LOOKBACK_DAYS = 60


def _sync_start_date(pid, today):
    """这个项目本次该从哪天开始补：上次水位线当天，但不早于 today - 回看上限。

    从水位线「当天」而非次日开始：那天在上次同步之后可能又出了新 commit
    （典型：干到深夜，23:59 提交、过零点才再同步，这条 commit 属于前一天），
    要能被重扫补进当天日志，否则永远掉在缝里。没变化的天有 commit 集合比对
    兜着（见 sync_project_day），不会重复花 AI 调用。
    """
    floor = (date.fromisoformat(today) - dt.timedelta(days=MAX_SYNC_LOOKBACK_DAYS))
    wm = db.get_setting(f"last_sync_date:{pid}")
    if wm:
        try:
            start = date.fromisoformat(wm)
        except ValueError:
            start = floor
    else:
        start = floor
    return max(start, floor)


def get_ai_config():
    """AI 摘要服务商配置（OpenAI 兼容）。默认 DeepSeek；换服务商只改这三项设置。"""
    return {
        "api_key": db.get_setting("ai_api_key") or os.environ.get("OPENAI_API_KEY", ""),
        "base_url": db.get_setting("ai_base_url") or "https://api.deepseek.com",
        "model": db.get_setting("ai_model") or "deepseek-chat",
    }


def sync_project_day(p, day_iso, today, unpushed_hashes, mem_today, info, ai_cfg):
    """补齐某个项目某一天的日志：commit 列表 + AI 摘要 + 自动检测三项清单。

    清单三项里 CLAUDE.md / GitHub 推送可以按那天精确回算；memory 只有「今天」能测
    （文件系统只知道当前 mtime，无法追溯历史某天是否动过 memory），历史天保持未勾选。
    返回 (commits 数, 是否真正写入)。
    """
    pid = p["id"]
    is_today = day_iso == today
    commits, diff = scanner.get_commits_for_day(p["path"], day_iso)
    if not commits:
        return 0, False

    # 已经有摘要的历史天就别再重复调 AI（今天例外：每次都刷新覆盖）
    with db.cursor() as cur:
        cur.execute(
            "SELECT auto_summary, raw_commits_json FROM daily_logs "
            "WHERE project_id = ? AND date = ?",
            (pid, day_iso),
        )
        row = cur.fetchone()
    summary_text = (row["auto_summary"] or "").strip() if row else ""
    # 失败占位文本（[AI 摘要失败：…]）不算已有摘要，否则一次网络抖动会让历史天永远停在错误文本上
    already_summarized = bool(summary_text) and not summary_text.startswith("[AI")
    if already_summarized and not is_today:
        # 但要比对 commit 集合：那天在上次同步之后又出了新 commit（深夜提交、过零点
        # 才同步的「零点裂缝」）就得重写补齐；集合没变才跳过，不重复花 AI 调用。
        try:
            known = {c.get("hash") for c in json.loads(row["raw_commits_json"] or "[]")}
        except (ValueError, TypeError):
            known = set()
        # 老数据没存 commit 列表的无从比较，按没变处理，别为它们重花 AI
        if not known or known == {c["hash"] for c in commits}:
            return len(commits), False

    summary = (
        ai.summarize(p["name"], commits, diff, cfg=ai_cfg)
        if ai_cfg.get("api_key")
        else None
    )

    # 自动检测清单（可精确测量，故覆盖式写入）；deployed 永远纯手动，不碰
    changed_files = scanner.get_changed_files_for_day(p["path"], day_iso)
    auto_claudemd = 1 if "CLAUDE.md" in changed_files else 0
    # 这天的 commit 是否都已推到 remote：只要有一条还在「未推集合」里，就算没推
    day_hashes = {c["hash"] for c in commits}
    auto_pushed = 0 if (day_hashes & unpushed_hashes) else 1
    # memory 只有今天可测
    auto_memory = 1 if (is_today and mem_today) else 0

    has_claudemd = bool(info.get("has_claudemd"))
    disabled = default_disabled_for(p, has_claudemd=has_claudemd)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, raw_commits_json, "
            "claudemd_updated, memory_updated, pushed_to_github, disabled_checks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, date) DO UPDATE SET "
            "auto_summary=excluded.auto_summary, raw_commits_json=excluded.raw_commits_json, "
            "claudemd_updated=excluded.claudemd_updated, "
            "memory_updated=max(daily_logs.memory_updated, excluded.memory_updated), "
            "pushed_to_github=excluded.pushed_to_github",
            (
                pid, day_iso, summary, json.dumps(commits, ensure_ascii=False),
                auto_claudemd, auto_memory, auto_pushed, disabled,
            ),
        )
    # 落一份 Markdown 到 Obsidian vault（没配路径则内部跳过）
    obsidian.write_day(pid, day_iso)
    return len(commits), True


def sync_one_project(pid):
    """从上次同步水位线补到今天：逐天找出有 commit 但未生成摘要的日子，补齐全套日志。"""
    p = get_project(pid)
    if not p or not p.get("path"):
        return {"project_id": pid, "ok": False, "reason": "no path"}
    today = date.today().isoformat()
    ai_cfg = get_ai_config()
    # 整段时间窗里复用同一份「未推 hash 集合 / memory 今日状态 / repo 信息」，避免逐天重复跑
    unpushed_hashes = scanner.get_unpushed_hashes(p["path"])
    mem_today = scanner.memory_updated_today(p["name"], p["path"], today)
    info = scanner.get_repo_info(p["path"])
    save_repo_snapshot(pid, info)

    # 顺手刷新 GitHub 可见性徽章
    if p.get("github_repo"):
        token = db.get_setting("github_token")
        vis = scanner.github_visibility(p["github_repo"], token)
        if vis:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE projects SET github_visibility = ? WHERE id = ?", (vis, pid)
                )

    start = _sync_start_date(pid, today)
    end = date.fromisoformat(today)
    # 今天永远参与同步：当天清单本就每次覆盖刷新（先同步后推送时要能补上推送勾）。
    # 水位线只用来决定「往前补到哪天」，不该把今天排除掉。
    start = min(start, end)
    total_commits = 0
    synced_days = 0
    cur_day = start
    while cur_day <= end:
        n, wrote = sync_project_day(
            p, cur_day.isoformat(), today, unpushed_hashes, mem_today, info, ai_cfg
        )
        total_commits += n
        if wrote:
            synced_days += 1
        cur_day += dt.timedelta(days=1)

    # 推进水位线到今天
    db.set_setting(f"last_sync_date:{pid}", today)

    if total_commits == 0:
        return {"project_id": pid, "ok": True, "skipped": True, "commits": 0, "days": 0}
    return {
        "project_id": pid, "ok": True,
        "commits": total_commits, "days": synced_days,
    }


# 同步在后台线程跑（AI 摘要一天一调，串行跑完可能要几十秒，不能卡住请求）。
# 单用户单进程，一个全局槽位足够；已有同步在跑时拒绝再次启动。
_sync_lock = threading.Lock()
_sync_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "current": None,     # 正在同步的项目名
    "synced": 0,         # 有新内容的项目数
    "skipped": 0,        # 无新改动的项目数
    "results": [],       # 每个项目 sync_one_project 的返回值
    "started_at": None,
    "finished_at": None,
}


def _run_sync(project_ids):
    try:
        for pid in project_ids:
            p = get_project(pid)
            with _sync_lock:
                _sync_state["current"] = p["name"] if p else str(pid)
            r = sync_one_project(pid)
            with _sync_lock:
                _sync_state["done"] += 1
                _sync_state["results"].append(r)
                if r.get("skipped"):
                    _sync_state["skipped"] += 1
                elif r.get("ok") and r.get("commits"):
                    _sync_state["synced"] += 1
        # 收尾扫一遍日记：兜住「先同步了、日记后来才建」的天（无论隔了多少天）
        obsidian.inject_sweep()
    finally:
        with _sync_lock:
            _sync_state["running"] = False
            _sync_state["current"] = None
            _sync_state["finished_at"] = datetime.now().isoformat(timespec="seconds")


def start_sync(project_ids):
    """启动后台同步线程。已有一个在跑时返回 False。"""
    with _sync_lock:
        if _sync_state["running"]:
            return False
        _sync_state.update(
            running=True, total=len(project_ids), done=0, current=None,
            synced=0, skipped=0, results=[],
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
        )
    threading.Thread(target=_run_sync, args=(project_ids,), daemon=True).start()
    return True


@app.route("/sync/today", methods=["POST"])
def sync_today_all():
    ids = [p["id"] for p in all_projects() if p.get("path")]
    if not start_sync(ids):
        return jsonify({"ok": False, "reason": "already running"}), 409
    return jsonify({"ok": True, "started": True, "total": len(ids)})


@app.route("/project/<int:pid>/sync", methods=["POST"])
def sync_today_one(pid):
    p = get_project(pid)
    if not p or not p.get("path"):
        return jsonify({"ok": False, "reason": "no path"})
    if not start_sync([pid]):
        return jsonify({"ok": False, "reason": "already running"}), 409
    return jsonify({"ok": True, "started": True, "total": 1})


@app.route("/sync/status")
def sync_status():
    with _sync_lock:
        state = dict(_sync_state)
        state["results"] = list(_sync_state["results"])
    return jsonify(state)


@app.route("/sync/history", methods=["POST"])
def sync_history_all():
    """Import all historical commits for all projects, grouped by day. No AI calls."""
    projects = all_projects()
    total_logs = 0
    total_days = 0
    for p in projects:
        if not p.get("path"):
            continue
        by_day = scanner.get_all_commits_by_day(p["path"])
        with db.cursor() as cur:
            for day, commits in by_day.items():
                total_days += 1
                cur.execute(
                    "INSERT INTO daily_logs (project_id, date, raw_commits_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(project_id, date) DO UPDATE SET "
                    "raw_commits_json = CASE WHEN daily_logs.raw_commits_json IS NULL OR daily_logs.raw_commits_json = '' "
                    "THEN excluded.raw_commits_json ELSE daily_logs.raw_commits_json END",
                    (p["id"], day, json.dumps(commits, ensure_ascii=False)),
                )
                total_logs += 1
    return jsonify({"ok": True, "projects": len(projects), "log_entries": total_logs, "days": total_days})


@app.route("/project/<int:pid>/sync-history", methods=["POST"])
def sync_history_one(pid):
    p = get_project(pid)
    if not p or not p.get("path"):
        return jsonify({"ok": False, "reason": "no path"})
    by_day = scanner.get_all_commits_by_day(p["path"])
    with db.cursor() as cur:
        for day, commits in by_day.items():
            cur.execute(
                "INSERT INTO daily_logs (project_id, date, raw_commits_json) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, date) DO UPDATE SET "
                "raw_commits_json = CASE WHEN daily_logs.raw_commits_json IS NULL OR daily_logs.raw_commits_json = '' "
                "THEN excluded.raw_commits_json ELSE daily_logs.raw_commits_json END",
                (pid, day, json.dumps(commits, ensure_ascii=False)),
            )
    return jsonify({"ok": True, "days": len(by_day)})


@app.route("/log/<int:log_id>/regen-summary", methods=["POST"])
def log_regen_summary(log_id):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM daily_logs WHERE id = ?", (log_id,))
        log = cur.fetchone()
        if not log:
            return jsonify({"ok": False, "reason": "not found"})
        p = get_project(log["project_id"])
        if not p or not p.get("path"):
            return jsonify({"ok": False, "reason": "no path"})
    commits, diff = scanner.get_commits_for_day(p["path"], log["date"])
    if not commits:
        return jsonify({"ok": False, "reason": "no commits on that day"})
    ai_cfg = get_ai_config()
    if not ai_cfg.get("api_key"):
        return jsonify({"ok": False, "reason": "no api key"})
    summary = ai.summarize(p["name"], commits, diff, cfg=ai_cfg)
    with db.cursor() as cur:
        cur.execute("UPDATE daily_logs SET auto_summary=? WHERE id=?", (summary, log_id))
    obsidian.write_for_log(log_id)  # 重生成的摘要要覆盖进当天存档
    return jsonify({"ok": True, "summary": summary})


@app.route("/log/<int:log_id>/notes", methods=["POST"])
def update_log_notes(log_id):
    notes = request.form.get("manual_notes", "")
    with db.cursor() as cur:
        cur.execute("UPDATE daily_logs SET manual_notes=? WHERE id=?", (notes, log_id))
    obsidian.write_for_log(log_id)  # 手动补充要覆盖进当天存档
    pid = request.form.get("project_id")
    return redirect(url_for("project_detail", pid=pid))


# ---------- Daily log checklist ----------

@app.route("/log/<int:log_id>/check", methods=["POST"])
def log_check_toggle(log_id):
    field = request.form.get("field")
    if field not in CHECK_FIELDS:
        return jsonify({"ok": False, "reason": "invalid field"}), 400
    with db.cursor() as cur:
        cur.execute(f"UPDATE daily_logs SET {field} = 1 - {field} WHERE id = ?", (log_id,))
        cur.execute(f"SELECT {field} AS v FROM daily_logs WHERE id = ?", (log_id,))
        row = cur.fetchone()
    return jsonify({"ok": True, "value": row["v"] if row else 0})


def _set_log_disabled(log_id, field, disabled):
    """Add or remove a checklist field from a log's disabled_checks set."""
    with db.cursor() as cur:
        cur.execute("SELECT disabled_checks FROM daily_logs WHERE id = ?", (log_id,))
        row = cur.fetchone()
        if not row:
            return None
        current = {f for f in (row["disabled_checks"] or "").split(",") if f}
        if disabled:
            current.add(field)
        else:
            current.discard(field)
        value = ",".join(sorted(current))
        cur.execute("UPDATE daily_logs SET disabled_checks = ? WHERE id = ?", (value, log_id))
        return value


@app.route("/log/<int:log_id>/check-disable", methods=["POST"])
def log_check_disable(log_id):
    field = request.form.get("field")
    if field not in CHECK_FIELDS:
        return jsonify({"ok": False, "reason": "invalid field"}), 400
    result = _set_log_disabled(log_id, field, True)
    if result is None:
        return jsonify({"ok": False, "reason": "not found"}), 404
    return jsonify({"ok": True, "disabled_checks": result})


@app.route("/log/<int:log_id>/check-enable", methods=["POST"])
def log_check_enable(log_id):
    field = request.form.get("field")
    if field not in CHECK_FIELDS:
        return jsonify({"ok": False, "reason": "invalid field"}), 400
    result = _set_log_disabled(log_id, field, False)
    if result is None:
        return jsonify({"ok": False, "reason": "not found"}), 404
    return jsonify({"ok": True, "disabled_checks": result})


# ---------- Project description ----------

@app.route("/project/<int:pid>/description", methods=["POST"])
def project_description_set(pid):
    desc = (request.form.get("description") or "").strip() or None
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET description=? WHERE id=?", (desc, pid))
    if request.form.get("ajax"):
        return jsonify({"ok": True, "description": desc})
    return redirect(url_for("project_detail", pid=pid))


@app.route("/project/<int:pid>/description/generate", methods=["POST"])
def project_description_generate(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    ai_cfg = get_ai_config()
    if not ai_cfg.get("api_key"):
        return jsonify({"ok": False, "reason": "no api key"})
    # build context: CLAUDE.md content if available, else recent auto-summaries
    context = ""
    if p.get("path"):
        claudemd = os.path.join(p["path"], "CLAUDE.md")
        if os.path.isfile(claudemd):
            try:
                with open(claudemd, encoding="utf-8") as f:
                    context = f.read()
            except Exception:
                context = ""
    if not context.strip():
        with db.cursor() as cur:
            cur.execute(
                "SELECT auto_summary FROM daily_logs WHERE project_id = ? "
                "AND auto_summary IS NOT NULL AND auto_summary != '' ORDER BY date DESC LIMIT 5",
                (pid,),
            )
            context = "\n".join(r["auto_summary"] for r in cur.fetchall())
    desc = ai.describe_project(p["name"], context, cfg=ai_cfg)
    if not desc:
        return jsonify({"ok": False, "reason": "no context to summarize"})
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET description=? WHERE id=?", (desc, pid))
    return jsonify({"ok": True, "description": desc})


# ---------- 学习页：学习笔记（全局笔记）+ 参考资料 ----------

@app.route("/notes")
def notes_list():
    q = request.args.get("q", "").strip()
    with db.cursor() as cur:
        # only global notes (project_id IS NULL); project notes stay inside their projects
        if q:
            like = f"%{q}%"
            cur.execute(
                "SELECT * FROM notes WHERE project_id IS NULL "
                "AND (title LIKE ? OR body LIKE ? OR tags LIKE ?) ORDER BY updated_at DESC",
                (like, like, like),
            )
        else:
            cur.execute(
                "SELECT * FROM notes WHERE project_id IS NULL ORDER BY updated_at DESC"
            )
        notes = [dict(r) for r in cur.fetchall()]
        # reference items
        if q:
            like = f"%{q}%"
            cur.execute(
                "SELECT * FROM reference_items WHERE title LIKE ? OR body LIKE ? OR links LIKE ? "
                "ORDER BY starred DESC, id DESC",
                (like, like, like),
            )
        else:
            cur.execute("SELECT * FROM reference_items ORDER BY starred DESC, id DESC")
        references = [dict(r) for r in cur.fetchall()]
    for r in references:
        try:
            r["link_list"] = json.loads(r["links"]) if r.get("links") else []
        except Exception:
            r["link_list"] = []
    return render_template("notes.html", notes=notes, references=references, q=q)


def _parse_links_from_form():
    """Collect link rows (name[] + url[]) from a reference form into a JSON-ready list."""
    names = request.form.getlist("link_name")
    urls = request.form.getlist("link_url")
    links = []
    for name, url in zip(names, urls):
        url = (url or "").strip()
        if url:
            links.append({"name": (name or "").strip() or url, "url": url})
    return links


@app.route("/reference/new", methods=["POST"])
def reference_new():
    title = request.form.get("title", "").strip() or "(无标题)"
    body = request.form.get("body", "")
    links = _parse_links_from_form()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO reference_items (title, body, links) VALUES (?, ?, ?)",
            (title, body, json.dumps(links, ensure_ascii=False)),
        )
    return redirect(url_for("notes_list"))


@app.route("/reference/<int:rid>/edit", methods=["POST"])
def reference_edit(rid):
    title = request.form.get("title", "").strip() or "(无标题)"
    body = request.form.get("body", "")
    links = _parse_links_from_form()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE reference_items SET title=?, body=?, links=? WHERE id=?",
            (title, body, json.dumps(links, ensure_ascii=False), rid),
        )
    return redirect(url_for("notes_list"))


@app.route("/reference/<int:rid>/delete", methods=["POST"])
def reference_delete(rid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM reference_items WHERE id=?", (rid,))
    return redirect(url_for("notes_list"))


@app.route("/reference/<int:rid>/star", methods=["POST"])
def reference_star_toggle(rid):
    with db.cursor() as cur:
        cur.execute("UPDATE reference_items SET starred = 1 - starred WHERE id = ?", (rid,))
        cur.execute("SELECT starred FROM reference_items WHERE id = ?", (rid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "starred": row["starred"] if row else 0})


@app.route("/note/new", methods=["POST"])
def note_new():
    project_id = request.form.get("project_id") or None
    if project_id == "":
        project_id = None
    title = request.form.get("title", "").strip() or "(无标题)"
    body = request.form.get("body", "")
    tags = request.form.get("tags", "")
    linked_log = request.form.get("linked_daily_log_id") or None
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO notes (project_id, title, body, tags, linked_daily_log_id) VALUES (?, ?, ?, ?, ?)",
            (project_id, title, body, tags, linked_log),
        )
    if linked_log:  # 关联到某天的笔记要进当天存档的「当天笔记」
        obsidian.write_for_log(linked_log)
    redirect_to = request.form.get("redirect_to") or url_for("notes_list")
    return redirect(redirect_to)


@app.route("/note/<int:nid>/edit", methods=["POST"])
def note_edit(nid):
    title = request.form.get("title", "").strip() or "(无标题)"
    body = request.form.get("body", "")
    tags = request.form.get("tags", "")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE notes SET title=?, body=?, tags=?, updated_at=datetime('now','localtime') WHERE id=?",
            (title, body, tags, nid),
        )
        cur.execute("SELECT linked_daily_log_id FROM notes WHERE id=?", (nid,))
        row = cur.fetchone()
    if row and row["linked_daily_log_id"]:  # 改到某天的关联笔记要覆盖当天存档
        obsidian.write_for_log(row["linked_daily_log_id"])
    redirect_to = request.form.get("redirect_to") or url_for("notes_list")
    return redirect(redirect_to)


@app.route("/note/<int:nid>/delete", methods=["POST"])
def note_delete(nid):
    with db.cursor() as cur:
        # 删前先记住它关联哪天，删完好把那天存档里的笔记同步掉
        cur.execute("SELECT linked_daily_log_id FROM notes WHERE id=?", (nid,))
        row = cur.fetchone()
        linked_log = row["linked_daily_log_id"] if row else None
        cur.execute("DELETE FROM notes WHERE id=?", (nid,))
    if linked_log:
        obsidian.write_for_log(linked_log)
    redirect_to = request.form.get("redirect_to") or url_for("notes_list")
    return redirect(redirect_to)


# ---------- Media ----------

@app.route("/media")
def media_list():
    status = request.args.get("status", "all")
    scope = request.args.get("scope", "all")
    sort = request.args.get("sort", "publish_desc")  # publish_desc | publish_asc | default
    with db.cursor() as cur:
        sql = (
            "SELECT m.*, p.name AS project_name FROM media_items m "
            "LEFT JOIN projects p ON p.id = m.project_id WHERE 1=1"
        )
        params = []
        if status != "all":
            sql += " AND m.status = ?"
            params.append(status)
        if scope == "global":
            sql += " AND m.project_id IS NULL"
        elif scope == "project":
            sql += " AND m.project_id IS NOT NULL"
        # NULL publish_date 永远排在最后；其他按方向排
        if sort == "publish_desc":
            sql += " ORDER BY m.publish_date IS NULL, m.publish_date DESC, m.id DESC"
        elif sort == "publish_asc":
            sql += " ORDER BY m.publish_date IS NULL, m.publish_date ASC, m.id DESC"
        else:
            sql += " ORDER BY m.id DESC"
        cur.execute(sql, params)
        items = [dict(r) for r in cur.fetchall()]
    return render_template("media.html", items=items, status=status, scope=scope, sort=sort)


@app.route("/media/new", methods=["POST"])
def media_new():
    project_id = request.form.get("project_id") or None
    if project_id == "":
        project_id = None
    title = request.form.get("title", "").strip() or "(无标题)"
    mtype = request.form.get("type", "video")
    status = request.form.get("status", "planned")
    link = request.form.get("link", "").strip() or None
    notes = request.form.get("notes", "")
    publish_date = request.form.get("publish_date", "").strip() or None
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO media_items (project_id, type, title, status, link, notes, publish_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, mtype, title, status, link, notes, publish_date),
        )
    redirect_to = request.form.get("redirect_to") or url_for("media_list")
    return redirect(redirect_to)


@app.route("/media/<int:mid>/update", methods=["POST"])
def media_update(mid):
    """Quick partial update — only the fields actually sent are written.

    Used by the inline status / publish-date controls on the media card header.
    """
    fields = []
    values = []
    if "status" in request.form:
        fields.append("status=?")
        values.append(request.form.get("status"))
    if "publish_date" in request.form:
        fields.append("publish_date=?")
        values.append(request.form.get("publish_date", "").strip() or None)
    if fields:
        values.append(mid)
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE media_items SET {', '.join(fields)} WHERE id=?", values
            )
    redirect_to = request.form.get("redirect_to") or url_for("media_list")
    return redirect(redirect_to)


@app.route("/media/<int:mid>/star", methods=["POST"])
def media_star_toggle(mid):
    with db.cursor() as cur:
        cur.execute("UPDATE media_items SET starred = 1 - starred WHERE id = ?", (mid,))
        cur.execute("SELECT starred FROM media_items WHERE id = ?", (mid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "starred": row["starred"] if row else 0})


@app.route("/media/<int:mid>/edit", methods=["POST"])
def media_edit(mid):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE media_items SET title=?, type=?, status=?, notes=?, publish_date=? WHERE id=?",
            (
                request.form.get("title", "").strip() or "(无标题)",
                request.form.get("type", "video"),
                request.form.get("status", "planned"),
                request.form.get("notes", ""),
                request.form.get("publish_date", "").strip() or None,
                mid,
            ),
        )
    redirect_to = request.form.get("redirect_to") or url_for("media_list")
    return redirect(redirect_to)


@app.route("/media/<int:mid>/delete", methods=["POST"])
def media_delete(mid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM media_items WHERE id=?", (mid,))
    redirect_to = request.form.get("redirect_to") or url_for("media_list")
    return redirect(redirect_to)


# ---------- Export ----------

def text_response(content, filename):
    resp = Response(content, mimetype="text/markdown")
    # RFC 5987: non-ASCII filenames need filename* encoding, with an ASCII fallback
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "export.md"
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return resp


@app.route("/export/project/<int:pid>")
def export_project(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_logs WHERE project_id = ? ORDER BY date DESC", (pid,)
        )
        logs = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM notes WHERE project_id = ? ORDER BY created_at DESC", (pid,)
        )
        notes = [dict(r) for r in cur.fetchall()]
    log_notes_by_log = {}
    for n in notes:
        if n.get("linked_daily_log_id"):
            log_notes_by_log.setdefault(n["linked_daily_log_id"], []).append(n)
    out = [f"# {p['name']}\n"]
    for log in logs:
        out.append(f"## {log['date']}")
        out.append("\n【当天更新】")
        update_text = (log.get("auto_summary") or "") + (
            ("\n\n" + log["manual_notes"]) if log.get("manual_notes") else ""
        )
        out.append(update_text.strip() or "(无)")
        out.append("\n【当天笔记】")
        linked = log_notes_by_log.get(log["id"], [])
        if linked:
            for n in linked:
                out.append(f"- **{n['title']}**\n  {n.get('body','')}")
        else:
            out.append("(无)")
        out.append("")
    unlinked = [n for n in notes if not n.get("linked_daily_log_id")]
    if unlinked:
        out.append("\n## 未关联日志的项目笔记")
        for n in unlinked:
            out.append(f"### {n['title']}")
            if n.get("tags"):
                out.append(f"_标签：{n['tags']}_")
            out.append(n.get("body", ""))
            out.append("")
    filename = f"{p['name']}-notes.md"
    return text_response("\n".join(out), filename)


@app.route("/export/global-notes")
def export_global_notes():
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE project_id IS NULL ORDER BY created_at DESC"
        )
        notes = [dict(r) for r in cur.fetchall()]
    out = ["# 学习笔记\n"]
    for n in notes:
        out.append(f"## {n['title']}")
        out.append(f"_{n.get('created_at','')}_")
        if n.get("tags"):
            out.append(f"标签：{n['tags']}")
        out.append("")
        out.append(n.get("body", ""))
        out.append("")
    return text_response("\n".join(out), "学习笔记.md")


@app.route("/export/note/<int:nid>")
def export_note(nid):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE id = ?", (nid,))
        n = cur.fetchone()
    if not n:
        abort(404)
    n = dict(n)
    out = [f"# {n['title']}\n", f"_{n.get('created_at','')}_"]
    if n.get("tags"):
        out.append(f"标签：{n['tags']}")
    out.append("")
    out.append(n.get("body", "") or "")
    return text_response("\n".join(out), f"{n['title']}.md")


@app.route("/export/all-notes")
def export_all_notes():
    out = ["# 全部笔记\n"]
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE project_id IS NULL ORDER BY created_at DESC"
        )
        global_notes = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM projects ORDER BY name")
        projects = [dict(r) for r in cur.fetchall()]
    if global_notes:
        out.append("## 全局笔记\n")
        for n in global_notes:
            out.append(f"### {n['title']}")
            if n.get("tags"):
                out.append(f"_标签：{n['tags']}_")
            out.append(n.get("body", ""))
            out.append("")
    for p in projects:
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM notes WHERE project_id = ? ORDER BY created_at DESC",
                (p["id"],),
            )
            ns = [dict(r) for r in cur.fetchall()]
        if ns:
            out.append(f"\n## {p['name']}\n")
            for n in ns:
                out.append(f"### {n['title']}")
                if n.get("tags"):
                    out.append(f"_标签：{n['tags']}_")
                out.append(n.get("body", ""))
                out.append("")
    return text_response("\n".join(out), "all-notes.md")


# ---------- Settings ----------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        paths_raw = request.form.get("scan_paths", "")
        paths = [p.strip() for p in paths_raw.splitlines() if p.strip()]
        db.set_scan_paths(paths)
        api_key = request.form.get("ai_api_key", "").strip()
        if api_key:
            db.set_setting("ai_api_key", api_key)
        elif "clear_api_key" in request.form:
            db.set_setting("ai_api_key", "")
        db.set_setting("ai_base_url", request.form.get("ai_base_url", "").strip())
        db.set_setting("ai_model", request.form.get("ai_model", "").strip())
        db.set_setting("backup_dir", request.form.get("backup_dir", "").strip())
        # Obsidian 归档：vault 路径或总文件夹名有变化时，把历史日志全量补写一遍
        old_vault = (db.get_setting("obsidian_vault_dir") or "").strip()
        new_vault = request.form.get("obsidian_vault_dir", "").strip()
        old_subdir = (db.get_setting("obsidian_subdir") or "").strip()
        new_subdir = request.form.get("obsidian_subdir", "").strip()
        db.set_setting("obsidian_vault_dir", new_vault)
        db.set_setting("obsidian_subdir", new_subdir)  # 先写设置，backfill 才落到新文件夹
        if new_vault and (new_vault != old_vault or new_subdir != old_subdir):
            obsidian.backfill_all()
        # 日记文件夹：首次填入或改动时，立即把已有日记全扫一遍填托管块
        old_diary = (db.get_setting("obsidian_diary_subdir") or "").strip()
        new_diary = request.form.get("obsidian_diary_subdir", "").strip()
        db.set_setting("obsidian_diary_subdir", new_diary)
        if new_vault and new_diary and (new_diary != old_diary or new_vault != old_vault):
            obsidian.inject_sweep()
        gh = request.form.get("github_token", "").strip()
        if gh:
            db.set_setting("github_token", gh)
        return redirect(url_for("settings"))
    paths = db.get_scan_paths()
    api_key = db.get_setting("ai_api_key", "")
    ai_base_url = db.get_setting("ai_base_url", "") or "https://api.deepseek.com"
    ai_model = db.get_setting("ai_model", "") or "deepseek-chat"
    gh = db.get_setting("github_token", "")
    backup_dir = db.get_setting("backup_dir", "")
    backup_dir_active = db.resolve_backup_dir()
    obsidian_vault_dir = db.get_setting("obsidian_vault_dir", "")
    obsidian_subdir = db.get_setting("obsidian_subdir", "")
    obsidian_diary_subdir = db.get_setting("obsidian_diary_subdir", "")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE excluded_from_scan = 1 ORDER BY name")
        excluded = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM categories ORDER BY sort_order, id")
        categories = [dict(r) for r in cur.fetchall()]
    return render_template(
        "settings.html",
        scan_paths="\n".join(paths),
        api_key_set=bool(api_key),
        ai_base_url=ai_base_url,
        ai_model=ai_model,
        github_token_set=bool(gh),
        backup_dir=backup_dir,
        backup_dir_active=backup_dir_active,
        obsidian_vault_dir=obsidian_vault_dir,
        obsidian_subdir=obsidian_subdir,
        obsidian_diary_subdir=obsidian_diary_subdir,
        excluded=excluded,
        categories=categories,
    )


@app.route("/project/<int:pid>/restore-scan", methods=["POST"])
def project_restore_scan(pid):
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET excluded_from_scan=0 WHERE id=?", (pid,))
    return redirect(url_for("settings"))


# ---------- Online ping ----------

@app.route("/project/<int:pid>/ping", methods=["POST"])
def project_ping(pid):
    p = get_project(pid)
    if not p or not p.get("online_url"):
        return jsonify({"ok": False, "reason": "no url"})
    alive = scanner.ping_url(p["online_url"])
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET online_status=? WHERE id=?", (1 if alive else 0, pid))
    return jsonify({"ok": True, "alive": alive})


@app.route("/guide")
def guide():
    return render_template("guide.html")


if __name__ == "__main__":
    db.init_db()
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="127.0.0.1", port=8765, debug=debug)
