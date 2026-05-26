"""Flask app entry. Routes for dashboard, project detail, notes, media, settings, exports, sync."""

import os
import json
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

app = Flask(__name__)
app.secret_key = "coding-dashboard-local"


@app.before_request
def ensure_db():
    if not getattr(app, "_db_inited", False):
        db.init_db()
        app._db_inited = True


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


def enrich_project(p):
    """Add live filesystem/git data to a project row (non-mutating)."""
    p = dict(p)
    info = scanner.get_repo_info(p["path"]) if p.get("path") else {}
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
        if log:
            fields = log_active_fields(log)
            unchecked = [CHECK_LABELS[f] for f in fields if not log.get(f)]
            if unchecked:
                if len(unchecked) == len(fields):
                    detail = "全部尚未勾选"
                else:
                    detail = "、".join(unchecked) + " 尚未勾选"
                todos.append({
                    "project_id": p["id"],
                    "project_name": p["name"],
                    "kind": "checklist_pending",
                    "text": f"{log['date']} 的清单：{detail}",
                })
    return todos


def panel_todos():
    """Checkable todos for the dashboard top panel:
    global manual todos (project_id IS NULL) + important project todos."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT pt.*, p.name AS project_name FROM project_todos pt "
            "LEFT JOIN projects p ON p.id = pt.project_id "
            "WHERE pt.project_id IS NULL OR pt.important = 1 "
            "ORDER BY pt.done, pt.important DESC, pt.id DESC"
        )
        return [dict(r) for r in cur.fetchall()]


# ---------- Dashboard ----------

def build_heatmap(weeks_back=26):
    """GitHub-style contribution grid from daily_logs."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT dl.date AS d, p.name AS name FROM daily_logs dl "
            "JOIN projects p ON p.id = dl.project_id ORDER BY dl.date"
        )
        rows = cur.fetchall()
    by_date = {}
    for r in rows:
        by_date.setdefault(r["d"], []).append(r["name"])
    today = date.today()
    start = today - dt.timedelta(days=weeks_back * 7 - 1)
    start -= dt.timedelta(days=start.weekday())  # align to Monday
    weeks = []
    d = start
    prev_month = None
    while d <= today:
        days = []
        month_label = None
        for _ in range(7):
            if d <= today:
                iso = d.isoformat()
                projs = by_date.get(iso, [])
                days.append({"date": iso, "count": len(projs), "projects": projs})
                if d.day <= 7 and d.month != prev_month:
                    month_label = f"{d.month}月"
                    prev_month = d.month
            else:
                days.append(None)
            d += dt.timedelta(days=1)
        weeks.append({"days": days, "month": month_label})
    return weeks


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
    sort_by = request.args.get("sort", "updated")
    projects = all_projects()
    enriched = [enrich_project(p) for p in projects]
    if stage_filter_list:
        enriched = [p for p in enriched if p.get("stage") in stage_filter_list]
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

    todos = build_todos(enriched)
    suggestions = scan_suggestions()
    heatmap = build_heatmap()
    return render_template(
        "dashboard.html",
        projects=enriched,
        stage_filter_list=stage_filter_list,
        category_filter_list=category_filter_list,
        stage_filter_str=",".join(stage_filter_list),
        category_filter_str=",".join(category_filter_list),
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
    api_key = db.get_setting("anthropic_api_key")
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
    enriched = enrich_project(p)
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
        cur.execute(
            "SELECT * FROM project_todos WHERE project_id = ? ORDER BY done, important DESC, id DESC",
            (pid,),
        )
        todos = [dict(r) for r in cur.fetchall()]
    # link notes <-> daily logs both ways
    log_date_by_id = {log["id"]: log["date"] for log in logs}
    notes_by_log = {}
    for n in notes:
        if n.get("linked_daily_log_id"):
            n["linked_date"] = log_date_by_id.get(n["linked_daily_log_id"])
            notes_by_log.setdefault(n["linked_daily_log_id"], []).append(n)
    for log in logs:
        log["linked_notes"] = notes_by_log.get(log["id"], [])
    return render_template(
        "project.html",
        p=enriched,
        logs=logs,
        notes=notes,
        media=media,
        todos=todos,
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
        cur.execute("UPDATE project_todos SET done = 1 - done WHERE id = ?", (tid,))
        cur.execute("SELECT done FROM project_todos WHERE id = ?", (tid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "done": row["done"] if row else 0})


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
            "UPDATE projects SET name=?, path=?, stage=?, online_url=?, online_status=?, "
            "tracks_deployment=?, github_repo=?, github_visibility=?, github_repo_public=? WHERE id=?",
            (
                request.form.get("name", p["name"]).strip(),
                (request.form.get("path") or "").strip() or None,
                request.form.get("stage", p["stage"]),
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
            cur.execute("SELECT name FROM categories WHERE id=?", (cid,))
            row = cur.fetchone()
            if row:
                old_name = row["name"]
                cur.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cid))
                cur.execute(
                    "UPDATE projects SET category=? WHERE category=?", (new_name, old_name)
                )
    return redirect(url_for("settings"))


@app.route("/category/delete", methods=["POST"])
def category_delete():
    cid = request.form.get("id")
    if cid:
        with db.cursor() as cur:
            cur.execute("SELECT name FROM categories WHERE id=?", (cid,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE projects SET category=NULL WHERE category=?", (row["name"],))
                cur.execute("DELETE FROM categories WHERE id=?", (cid,))
    return redirect(url_for("settings"))


@app.route("/project/<int:pid>/delete", methods=["POST"])
def project_delete(pid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id=?", (pid,))
    return redirect(url_for("dashboard"))


# ---------- Sync ----------

def sync_one_project(pid):
    """Pull today's commits + AI summary for one project, and auto-detect checklist items."""
    p = get_project(pid)
    if not p or not p.get("path"):
        return {"project_id": pid, "ok": False, "reason": "no path"}
    today = date.today().isoformat()
    commits, diff = scanner.get_todays_commits(p["path"])
    if not commits:
        return {"project_id": pid, "ok": True, "skipped": True, "commits": 0}
    api_key = db.get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    summary = ai.summarize(p["name"], commits, diff, api_key=api_key) if api_key else None
    # auto-detect checklist items from git/filesystem state.
    # These 3 are precisely detectable, so a re-sync OVERWRITES them with fresh truth.
    # `deployed` is never touched here — it stays purely manual.
    info = scanner.get_repo_info(p["path"])
    changed_files = scanner.get_todays_changed_files(p["path"])
    # CLAUDE.md counts as "updated today" only if it was actually modified in today's commits
    auto_claudemd = 1 if "CLAUDE.md" in changed_files else 0
    # pushed only if there are zero commits missing from remotes (0 also means: no commits;
    # but get_todays_commits already guaranteed commits exist, so 0 here means truly pushed)
    auto_pushed = 1 if info.get("unpushed_count", 0) == 0 else 0
    mem = scanner.memory_mtime(p["path"])
    auto_memory = 1 if (mem and mem[:10] == today) else 0
    # default checklist for a *new* log: drop 'deployed' if not tracked, drop CLAUDE.md if no file
    has_claudemd = bool(info.get("has_claudemd"))
    disabled = default_disabled_for(p, has_claudemd=has_claudemd)
    # re-check GitHub visibility while we're here (keeps the public/private badge accurate)
    if p.get("github_repo"):
        token = db.get_setting("github_token")
        vis = scanner.github_visibility(p["github_repo"], token)
        if vis:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE projects SET github_visibility = ? WHERE id = ?", (vis, pid)
                )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, raw_commits_json, "
            "claudemd_updated, memory_updated, pushed_to_github, disabled_checks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, date) DO UPDATE SET "
            "auto_summary=excluded.auto_summary, raw_commits_json=excluded.raw_commits_json, "
            "claudemd_updated=excluded.claudemd_updated, "
            "memory_updated=excluded.memory_updated, "
            "pushed_to_github=excluded.pushed_to_github",
            (
                pid, today, summary, json.dumps(commits, ensure_ascii=False),
                auto_claudemd, auto_memory, auto_pushed, disabled,
            ),
        )
    return {"project_id": pid, "ok": True, "commits": len(commits), "summary": summary}


@app.route("/sync/today", methods=["POST"])
def sync_today_all():
    projects = all_projects()
    results = []
    synced = 0
    skipped = 0
    for p in projects:
        if not p.get("path"):
            continue
        r = sync_one_project(p["id"])
        results.append(r)
        if r.get("skipped"):
            skipped += 1
        elif r.get("ok") and r.get("commits"):
            synced += 1
    return jsonify({"synced": synced, "skipped": skipped, "results": results})


@app.route("/project/<int:pid>/sync", methods=["POST"])
def sync_today_one(pid):
    return jsonify(sync_one_project(pid))


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
    api_key = db.get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"ok": False, "reason": "no api key"})
    summary = ai.summarize(p["name"], commits, diff, api_key=api_key)
    with db.cursor() as cur:
        cur.execute("UPDATE daily_logs SET auto_summary=? WHERE id=?", (summary, log_id))
    return jsonify({"ok": True, "summary": summary})


@app.route("/log/<int:log_id>/notes", methods=["POST"])
def update_log_notes(log_id):
    notes = request.form.get("manual_notes", "")
    with db.cursor() as cur:
        cur.execute("UPDATE daily_logs SET manual_notes=? WHERE id=?", (notes, log_id))
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
    api_key = db.get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
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
    desc = ai.describe_project(p["name"], context, api_key=api_key)
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
                "ORDER BY id DESC",
                (like, like, like),
            )
        else:
            cur.execute("SELECT * FROM reference_items ORDER BY id DESC")
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
    redirect_to = request.form.get("redirect_to") or url_for("notes_list")
    return redirect(redirect_to)


@app.route("/note/<int:nid>/delete", methods=["POST"])
def note_delete(nid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM notes WHERE id=?", (nid,))
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
        api_key = request.form.get("anthropic_api_key", "").strip()
        if api_key:
            db.set_setting("anthropic_api_key", api_key)
        elif "clear_api_key" in request.form:
            db.set_setting("anthropic_api_key", "")
        gh = request.form.get("github_token", "").strip()
        if gh:
            db.set_setting("github_token", gh)
        return redirect(url_for("settings"))
    paths = db.get_scan_paths()
    api_key = db.get_setting("anthropic_api_key", "")
    gh = db.get_setting("github_token", "")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE excluded_from_scan = 1 ORDER BY name")
        excluded = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM categories ORDER BY sort_order, id")
        categories = [dict(r) for r in cur.fetchall()]
    return render_template(
        "settings.html",
        scan_paths="\n".join(paths),
        api_key_set=bool(api_key),
        github_token_set=bool(gh),
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
    app.run(host="127.0.0.1", port=8765, debug=True)
