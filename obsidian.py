"""把每日日志落成 Markdown 存档到 Obsidian vault：每个项目每天一个文件，覆盖式写入。

初衷：让每日日志（AI 摘要 + 手动补充 + 当天关联笔记）可以直接在 Obsidian 里检索。

- 路径：<vault>/coding-dashboard/<项目名>/<日期>.md
- 触发时机：同步（手动/夜间）写当天；手动补充笔记、重生成摘要、当天笔记增改删后即时重写
- 覆盖式：内容每次由 DB 现状整体重算再覆盖写，AI 摘要重试 / 手动补充编辑都会反映到文件
- 没在设置里填 vault 路径就整体跳过，不影响不用 Obsidian 的人
- 写失败静默返回 None（仿 db.backup_db），绝不打断同步主流程
"""

import os
import re

import db

VAULT_SUBDIR = "coding-dashboard"


def vault_dir():
    """用户在设置里填的 Obsidian vault 根目录；没填返回 None（= 不归档）。"""
    configured = (db.get_setting("obsidian_vault_dir") or "").strip()
    if not configured:
        return None
    return os.path.expanduser(configured)


def _safe_name(name):
    """项目名可能含 / 或别的路径非法字符，压成一个安全的目录/文件名片段。"""
    name = (name or "").strip() or "未命名项目"
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)


def _yaml_quote(value):
    """给 frontmatter 的值套双引号并转义，避免项目名里有冒号/引号时 YAML 失效。"""
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_markdown(project_name, log, linked_notes):
    """按「frontmatter + 当天更新（AI 摘要 + 手动补充）+ 当天笔记」拼一份单日 Markdown。"""
    day = log["date"]
    lines = [
        "---",
        f"project: {_yaml_quote(project_name)}",
        f"date: {day}",
        "---",
        "",
        f"# {project_name} · {day}",
        "",
        "## 当天更新",
        "",
    ]
    auto = (log.get("auto_summary") or "").strip()
    manual = (log.get("manual_notes") or "").strip()
    # AI 摘要失败的占位文本不算内容，别把它写进存档
    if auto.startswith("[AI"):
        auto = ""
    if auto:
        lines.append(auto)
    if manual:
        if auto:
            lines += ["", "### 手动补充", ""]
        lines.append(manual)
    if not auto and not manual:
        lines.append("(无)")

    lines += ["", "## 当天笔记", ""]
    if linked_notes:
        for n in linked_notes:
            lines.append(f"### {n.get('title') or '(无标题)'}")
            body = (n.get("body") or "").strip()
            if body:
                lines += ["", body]
            lines.append("")
    else:
        lines.append("(无)")

    return "\n".join(lines).rstrip() + "\n"


def _write_file(project_name, day_iso, content):
    """把内容写到 <vault>/coding-dashboard/<项目名>/<日期>.md。失败静默返回 None。"""
    root = vault_dir()
    if not root:
        return None
    try:
        proj_dir = os.path.join(root, VAULT_SUBDIR, _safe_name(project_name))
        os.makedirs(proj_dir, exist_ok=True)
        path = os.path.join(proj_dir, f"{day_iso}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except Exception:
        return None


def write_day(project_id, day_iso):
    """写/覆盖某项目某天的 Markdown 存档。没配 vault、当天无日志或写失败则返回 None。"""
    if not vault_dir():
        return None
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM daily_logs WHERE project_id = ? AND date = ?",
            (project_id, day_iso),
        )
        row = cur.fetchone()
        if not row:
            return None
        log = dict(row)
        cur.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
        prow = cur.fetchone()
        if not prow:
            return None
        project_name = prow["name"]
        cur.execute(
            "SELECT * FROM notes WHERE linked_daily_log_id = ? ORDER BY created_at, id",
            (log["id"],),
        )
        linked = [dict(r) for r in cur.fetchall()]
    return _write_file(project_name, day_iso, _build_markdown(project_name, log, linked))


def write_for_log(log_id):
    """按 daily_log id 重写对应那天的存档（笔记/手动补充路由手头只有 log_id 时用）。"""
    if not vault_dir():
        return None
    with db.cursor() as cur:
        cur.execute("SELECT project_id, date FROM daily_logs WHERE id = ?", (log_id,))
        row = cur.fetchone()
    if not row:
        return None
    return write_day(row["project_id"], row["date"])


def backfill_all():
    """把现有全部每日日志一次性落地成文件（配置好 vault 路径后补历史）。

    幂等：内容由 DB 现状重算再覆盖，重跑不会产生重复或损坏。返回写成功的文件数。
    """
    if not vault_dir():
        return 0
    with db.cursor() as cur:
        cur.execute("SELECT project_id, date FROM daily_logs ORDER BY project_id, date")
        rows = [(r["project_id"], r["date"]) for r in cur.fetchall()]
    written = 0
    for project_id, day_iso in rows:
        if write_day(project_id, day_iso):
            written += 1
    return written
