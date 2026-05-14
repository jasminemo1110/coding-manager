"""Flask app entry. Routes for dashboard, project detail, notes, media, settings, exports, sync."""

import os
import json
from datetime import date, datetime
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


def latest_iteration(pid):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM iterations WHERE project_id = ? ORDER BY date DESC, id DESC LIMIT 1",
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
    p["latest_iteration"] = latest_iteration(p["id"])
    return p


def build_todos(projects):
    """Aggregate cross-project todos from live state."""
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
        iter_ = p.get("latest_iteration")
        if p.get("path") and live.get("has_claudemd"):
            cm = live.get("claudemd_mtime")
            if iter_ and cm and iter_["date"] and cm < iter_["date"]:
                # CLAUDE.md is older than last iteration completion — possibly stale if code changed
                last_commit = live.get("last_commit_date")
                if last_commit and last_commit > iter_["date"]:
                    todos.append({
                        "project_id": p["id"],
                        "project_name": p["name"],
                        "kind": "claudemd_stale",
                        "text": "CLAUDE.md 自上次迭代后未更新，但代码有新改动",
                    })
        if iter_ and not iter_["deployed"]:
            todos.append({
                "project_id": p["id"],
                "project_name": p["name"],
                "kind": "deploy_pending",
                "text": f"当前迭代「{iter_['name']}」部署尚未勾选",
            })
    return todos


# ---------- Dashboard ----------

@app.route("/")
def dashboard():
    stage_filter = request.args.get("stage", "all")
    projects = all_projects()
    enriched = [enrich_project(p) for p in projects]
    if stage_filter != "all":
        enriched = [p for p in enriched if p.get("stage") == stage_filter]
    todos = build_todos(enriched)
    suggestions = scan_suggestions()
    return render_template(
        "dashboard.html",
        projects=enriched,
        stage_filter=stage_filter,
        stage_labels=db.STAGE_LABELS,
        stage_order=db.STAGE_ORDER,
        todos=todos,
        suggestions=suggestions,
    )


# ---------- Project CRUD ----------

@app.route("/project/add", methods=["POST"])
def project_add():
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip() or None
    stage = request.form.get("stage", "sprout")
    online_url = request.form.get("online_url", "").strip() or None
    if not name:
        flash("项目名不能为空")
        return redirect(url_for("dashboard"))
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path, stage, online_url) VALUES (?, ?, ?, ?)",
            (name, path, stage, online_url),
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
        cur.execute(
            "SELECT * FROM iterations WHERE project_id = ? ORDER BY date DESC, id DESC", (pid,)
        )
        iterations = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (pid,)
        )
        notes = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM media_items WHERE project_id = ? ORDER BY id DESC", (pid,)
        )
        media = [dict(r) for r in cur.fetchall()]
    return render_template(
        "project.html",
        p=enriched,
        logs=logs,
        iterations=iterations,
        notes=notes,
        media=media,
        stage_labels=db.STAGE_LABELS,
        stage_order=db.STAGE_ORDER,
    )


@app.route("/project/<int:pid>/update", methods=["POST"])
def project_update(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    fields = {
        "name": request.form.get("name", p["name"]).strip(),
        "path": (request.form.get("path") or "").strip() or None,
        "stage": request.form.get("stage", p["stage"]),
        "online_url": (request.form.get("online_url") or "").strip() or None,
        "online_status": 1 if request.form.get("online_status") else 0,
        "github_repo": (request.form.get("github_repo") or "").strip() or None,
        "github_repo_public": (request.form.get("github_repo_public") or "").strip() or None,
    }
    with db.cursor() as cur:
        cur.execute(
            "UPDATE projects SET name=?, path=?, stage=?, online_url=?, online_status=?, github_repo=?, github_repo_public=? WHERE id=?",
            (*fields.values(), pid),
        )
    return redirect(url_for("project_detail", pid=pid))


@app.route("/project/<int:pid>/stage", methods=["POST"])
def project_stage(pid):
    stage = request.form.get("stage")
    if stage not in db.STAGE_ORDER:
        return jsonify({"ok": False, "reason": "invalid stage"}), 400
    with db.cursor() as cur:
        cur.execute("UPDATE projects SET stage=? WHERE id=?", (stage, pid))
    return jsonify({"ok": True, "stage": stage})


@app.route("/project/<int:pid>/delete", methods=["POST"])
def project_delete(pid):
    with db.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id=?", (pid,))
    return redirect(url_for("dashboard"))


# ---------- Sync ----------

def sync_one_project(pid):
    """Pull today's commits + AI summary for one project."""
    p = get_project(pid)
    if not p or not p.get("path"):
        return {"project_id": pid, "ok": False, "reason": "no path"}
    today = date.today().isoformat()
    commits, diff = scanner.get_todays_commits(p["path"])
    if not commits:
        return {"project_id": pid, "ok": True, "skipped": True, "commits": 0}
    api_key = db.get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    summary = ai.summarize(p["name"], commits, diff, api_key=api_key) if api_key else None
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_logs (project_id, date, auto_summary, raw_commits_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_id, date) DO UPDATE SET auto_summary=excluded.auto_summary, raw_commits_json=excluded.raw_commits_json",
            (pid, today, summary, json.dumps(commits, ensure_ascii=False)),
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


@app.route("/log/<int:log_id>/notes", methods=["POST"])
def update_log_notes(log_id):
    notes = request.form.get("manual_notes", "")
    with db.cursor() as cur:
        cur.execute("UPDATE daily_logs SET manual_notes=? WHERE id=?", (notes, log_id))
    pid = request.form.get("project_id")
    return redirect(url_for("project_detail", pid=pid))


# ---------- Iterations ----------

@app.route("/project/<int:pid>/iteration/new", methods=["POST"])
def iteration_new(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    name = request.form.get("name", "").strip() or f"迭代 {date.today().isoformat()}"
    today = date.today().isoformat()
    prev = latest_iteration(pid)
    auto = {"claudemd_updated": 0, "memory_updated": 0, "pushed_to_github": 0, "deployed": 0}
    if p.get("path"):
        info = scanner.get_repo_info(p["path"])
        prev_date = prev["date"] if prev else None
        cm = info.get("claudemd_mtime")
        if cm and (not prev_date or cm[:10] >= prev_date):
            auto["claudemd_updated"] = 1
        mem = scanner.memory_mtime(p["path"])
        if mem and (not prev_date or mem[:10] >= prev_date):
            auto["memory_updated"] = 1
        if info.get("unpushed_count", 0) == 0:
            auto["pushed_to_github"] = 1
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO iterations (project_id, name, date, claudemd_updated, memory_updated, pushed_to_github, deployed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, name, today, auto["claudemd_updated"], auto["memory_updated"], auto["pushed_to_github"], 0),
        )
    return redirect(url_for("project_detail", pid=pid))


@app.route("/iteration/<int:iid>/toggle", methods=["POST"])
def iteration_toggle(iid):
    field = request.form.get("field")
    if field not in ("claudemd_updated", "memory_updated", "pushed_to_github", "deployed"):
        return jsonify({"ok": False})
    with db.cursor() as cur:
        cur.execute(f"UPDATE iterations SET {field} = 1 - {field} WHERE id = ?", (iid,))
        cur.execute("SELECT * FROM iterations WHERE id = ?", (iid,))
        row = cur.fetchone()
    return jsonify({"ok": True, "value": row[field] if row else 0})


@app.route("/iteration/<int:iid>/delete", methods=["POST"])
def iteration_delete(iid):
    pid = request.form.get("project_id")
    with db.cursor() as cur:
        cur.execute("DELETE FROM iterations WHERE id=?", (iid,))
    return redirect(url_for("project_detail", pid=pid))


# ---------- Notes ----------

@app.route("/notes")
def notes_list():
    q = request.args.get("q", "").strip()
    with db.cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute(
                "SELECT n.*, p.name AS project_name FROM notes n LEFT JOIN projects p ON p.id = n.project_id "
                "WHERE n.title LIKE ? OR n.body LIKE ? OR n.tags LIKE ? ORDER BY n.updated_at DESC",
                (like, like, like),
            )
        else:
            cur.execute(
                "SELECT n.*, p.name AS project_name FROM notes n LEFT JOIN projects p ON p.id = n.project_id "
                "ORDER BY n.updated_at DESC"
            )
        notes = [dict(r) for r in cur.fetchall()]
    return render_template("notes.html", notes=notes, q=q)


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
        sql += " ORDER BY m.id DESC"
        cur.execute(sql, params)
        items = [dict(r) for r in cur.fetchall()]
    return render_template("media.html", items=items, status=status, scope=scope)


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
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO media_items (project_id, type, title, status, link, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, mtype, title, status, link, notes),
        )
    redirect_to = request.form.get("redirect_to") or url_for("media_list")
    return redirect(redirect_to)


@app.route("/media/<int:mid>/update", methods=["POST"])
def media_update(mid):
    status = request.form.get("status")
    if status:
        with db.cursor() as cur:
            cur.execute("UPDATE media_items SET status=? WHERE id=?", (status, mid))
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
    resp = Response(content, mimetype="text/markdown; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
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
    out = ["# 全局笔记\n"]
    for n in notes:
        out.append(f"## {n['title']}")
        out.append(f"_{n.get('created_at','')}_")
        if n.get("tags"):
            out.append(f"标签：{n['tags']}")
        out.append("")
        out.append(n.get("body", ""))
        out.append("")
    return text_response("\n".join(out), "global-notes.md")


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
    return render_template(
        "settings.html",
        scan_paths="\n".join(paths),
        api_key_set=bool(api_key),
        github_token_set=bool(gh),
        excluded=excluded,
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


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=8765, debug=True)
