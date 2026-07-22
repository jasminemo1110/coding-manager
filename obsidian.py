"""把每日日志落成 Markdown 存档到 Obsidian vault：每个项目每天一个文件，覆盖式写入。

初衷：让每日日志（AI 摘要 + 手动补充 + 当天关联笔记）可以直接在 Obsidian 里检索。

- 路径：<vault>/coding-dashboard/<项目名>/<日期>.md
- 触发时机：同步（手动/夜间）写当天；手动补充笔记、重生成摘要、当天笔记增改删后即时重写
- 覆盖式：内容每次由 DB 现状整体重算再覆盖写，AI 摘要重试 / 手动补充编辑都会反映到文件
- 没在设置里填 vault 路径就整体跳过，不影响不用 Obsidian 的人
- 写失败静默返回 None（仿 db.backup_db），绝不打断同步主流程

另有「日记托管块」：往用户自己的每日日记（<vault>/<日记文件夹>/<日期>.md）里注入
「今日 Vibe Coding 成果」模块。日记是用户拥有并编辑的文件，绝不整篇覆盖——只替换
<!-- vibe:start --> 和 <!-- vibe:end --> 两个锚点之间的内容，锚点由用户放在日记模板里。
日记不存在或没有锚点就跳过；重算后内容没变化就不落盘（对 iCloud/Obsidian Sync 友好）。
"""

import os
import re

import db

DEFAULT_SUBDIR = "coding-dashboard"


def vault_dir():
    """用户在设置里填的 Obsidian vault 根目录；没填返回 None（= 不归档）。"""
    configured = (db.get_setting("obsidian_vault_dir") or "").strip()
    if not configured:
        return None
    return os.path.expanduser(configured)


def subdir():
    """归档落在 vault 下的总文件夹名，用户可在设置里改；留空回落默认 coding-dashboard。"""
    name = (db.get_setting("obsidian_subdir") or "").strip()
    return _safe_name(name) if name else DEFAULT_SUBDIR


def _safe_name(name):
    """项目名可能含 / 或别的路径非法字符，压成一个安全的目录/文件名片段。"""
    name = (name or "").strip() or "未命名项目"
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)


def _yaml_quote(value):
    """给 frontmatter 的值套双引号并转义，避免项目名里有冒号/引号时 YAML 失效。"""
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tag(text):
    """转成 Obsidian 合法 tag：空白换连字符，去掉标签不支持的标点（中文/字母/数字/_/- 都保留）。

    Obsidian 里 tag 遇空格即截断，所以项目名有空格/标点时不能原样当 tag。
    """
    text = re.sub(r"\s+", "-", (text or "").strip())
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    return text.strip("-")


def _build_markdown(project_name, log, linked_notes):
    """按「frontmatter + 当天更新（AI 摘要 + 手动补充）+ 当天笔记」拼一份单日 Markdown。"""
    day = log["date"]
    # 每篇默认打上「项目日志」+ 项目名两个 tag，方便在 Obsidian 里按项目/类别聚合
    tags = ["项目日志"]
    proj_tag = _tag(project_name)
    if proj_tag and proj_tag not in tags:
        tags.append(proj_tag)
    lines = [
        "---",
        f"project: {_yaml_quote(project_name)}",
        f"date: {day}",
        "tags:",
        *[f"  - {t}" for t in tags],
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
        proj_dir = os.path.join(root, subdir(), _safe_name(project_name))
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
    path = _write_file(project_name, day_iso, _build_markdown(project_name, log, linked))
    inject_day(day_iso)  # 归档变了，当天日记的托管块也跟着刷新（日记没建/没锚点则内部跳过）
    return path


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


# ---------- 日记托管块 ----------

DIARY_ANCHOR_START = "<!-- vibe:start -->"
DIARY_ANCHOR_END = "<!-- vibe:end -->"
_DIARY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def diary_dir():
    """用户日记所在文件夹（vault 内相对路径，设置项 obsidian_diary_subdir）。

    没配 vault 或没填日记文件夹 → None（= 日记集成整体关闭）。
    """
    root = vault_dir()
    sub = (db.get_setting("obsidian_diary_subdir") or "").strip()
    if not root or not sub:
        return None
    return os.path.join(root, sub)


def _diary_block(day_iso):
    """生成某天的「今日 Vibe Coding 成果」块：每个有更新的项目一条链接 + 嵌入。

    嵌入用完整路径——vault 里每个项目每天的归档都叫 <日期>.md，连日记本身也是，
    光写 [[2026-07-22]] 会歧义，必须 [[总文件夹/项目名/日期]]。
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT p.name AS name FROM daily_logs dl "
            "JOIN projects p ON p.id = dl.project_id "
            "WHERE dl.date = ? ORDER BY p.name",
            (day_iso,),
        )
        names = [r["name"] for r in cur.fetchall()]
    lines = ["## 今日 Vibe Coding 成果", ""]
    if not names:
        lines.append("（今天没有项目更新）")
    else:
        sub = subdir()
        for name in names:
            ref = f"{sub}/{_safe_name(name)}/{day_iso}"
            lines.append(f"### [[{ref}|{name}]]")
            lines.append(f"![[{ref}#当天更新]]")
            lines.append("")
    return "\n".join(lines).rstrip()


def inject_day(day_iso):
    """把某天的托管块写进当天日记。

    日记不存在、没放锚点、或重算后内容没变 → 都返回 None 不动文件。
    只替换锚点之间的内容，用户手写的部分一个字不碰。失败静默（仿 write_day）。
    """
    d = diary_dir()
    if not d:
        return None
    path = os.path.join(d, f"{day_iso}.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        start = text.find(DIARY_ANCHOR_START)
        if start == -1:
            return None
        inner_from = start + len(DIARY_ANCHOR_START)
        end = text.find(DIARY_ANCHOR_END, inner_from)
        if end == -1:
            return None
        rebuilt = (
            text[:inner_from] + "\n" + _diary_block(day_iso) + "\n" + text[end:]
        )
        if rebuilt == text:
            return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(rebuilt)
        return path
    except Exception:
        return None


def inject_sweep():
    """扫日记文件夹里所有日期命名的文件，逐个刷新托管块。

    兜住「日记晚建/补建」：不管日记什么时候建，下一次同步跑到这里就会填上。
    周记/月记等非 YYYY-MM-DD 命名的文件自动跳过。返回实际改写的文件数。
    """
    d = diary_dir()
    if not d or not os.path.isdir(d):
        return 0
    written = 0
    try:
        entries = os.listdir(d)
    except Exception:
        return 0
    for fn in entries:
        m = _DIARY_FILE_RE.match(fn)
        if m and inject_day(m.group(1)):
            written += 1
    return written
