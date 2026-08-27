"""Obsidian 学习库：Obsidian 是事实来源，Dashboard 只读索引。

目录位于 ``<vault>/<项目日志总目录>/<学习库目录>``。里面的所有 Markdown 都是
平等的笔记，分类交给 Obsidian tags。扫描不写数据库，因此用户在 Obsidian 里的
编辑、移动和删除会在下一次打开学习页时直接反映出来。
"""

import json
import os
import re
from datetime import datetime
from urllib.parse import urlencode

import db
import obsidian


DEFAULT_LEARNING_SUBDIR = "Vibe Coding 学习库"


def learning_subdir():
    configured = (db.get_setting("obsidian_learning_subdir") or "").strip()
    return obsidian._safe_name(configured) if configured else DEFAULT_LEARNING_SUBDIR


def library_root():
    vault = obsidian.vault_dir()
    if not vault:
        return None
    return os.path.join(vault, obsidian.subdir(), learning_subdir())


def _decode_scalar(value):
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _split_frontmatter(text):
    """解析索引需要的少量 YAML properties；正文始终原样保留。"""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    props = {}
    current_list = None
    for line in raw.splitlines():
        list_item = re.match(r"^\s+-\s+(.*)$", line)
        if list_item and current_list:
            props.setdefault(current_list, []).append(_decode_scalar(list_item.group(1)))
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            current_list = None
            continue
        key, value = match.groups()
        if value:
            props[key] = _decode_scalar(value)
            current_list = None
        else:
            props[key] = []
            current_list = key
    return props, body


def _title_from_body(body, fallback):
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return fallback


def _summary(body):
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
        if sum(len(part) for part in lines) >= 240:
            break
    text = "\n".join(lines)
    return text[:300] + ("…" if len(text) > 300 else "")


def _obsidian_url(path):
    vault = obsidian.vault_dir()
    relative = os.path.relpath(path, vault).replace(os.sep, "/")
    return "obsidian://open?" + urlencode(
        {"vault": os.path.basename(os.path.normpath(vault)), "file": relative}
    )


def _read_item(path, root):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        stat = os.stat(path)
    except (OSError, UnicodeError):
        return None
    props, body = _split_frontmatter(text)
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    fallback_title = os.path.splitext(os.path.basename(path))[0]
    title = str(props.get("title") or _title_from_body(body, fallback_title))
    tags = props.get("tags", [])
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.strip("[]").split(",") if part.strip()]
    updated = str(props.get("updated") or props.get("date") or "")
    if not updated:
        updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
    return {
        "title": title,
        "body": body.strip(),
        "summary": _summary(body),
        "tags": tags,
        "updated": updated[:10],
        "relative_path": relative,
        "obsidian_url": _obsidian_url(path),
    }


def scan(query=""):
    """读取学习库中的 Markdown；未配置或目录不存在时返回空结果。"""
    root = library_root()
    result = {"notes": [], "root": root}
    if not root or not os.path.isdir(root):
        return result
    needle = (query or "").strip().casefold()
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for filename in sorted(files):
            if filename.startswith(".") or not filename.lower().endswith(".md"):
                continue
            item = _read_item(os.path.join(current, filename), root)
            if not item:
                continue
            haystack = "\n".join(
                [item["title"], item["body"], " ".join(item["tags"]), item["relative_path"]]
            ).casefold()
            if needle and needle not in haystack:
                continue
            result["notes"].append(item)
    result["notes"].sort(key=lambda item: (item["updated"], item["relative_path"]), reverse=True)
    return result


def _safe_filename(title):
    return obsidian._safe_name(title).strip(". ") or "未命名"


def _tags(raw):
    values = re.split(r"[,，]\s*", raw or "")
    return [tag for value in values if (tag := obsidian._tag(value))]


def _frontmatter(title, legacy_id, created, updated=None, tags=None):
    lines = [
        "---",
        f"title: {obsidian._yaml_quote(title)}",
        "source: coding-dashboard",
        f"legacy_id: {legacy_id}",
    ]
    if created:
        lines.append(f"created: {str(created)[:10]}")
    if updated:
        lines.append(f"updated: {str(updated)[:10]}")
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {tag}" for tag in tags)
    lines.extend(["---", ""])
    return lines


def _legacy_ids(root):
    found = set()
    if not os.path.isdir(root):
        return found
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in files:
            if not filename.lower().endswith(".md"):
                continue
            try:
                path = os.path.join(current, filename)
                with open(path, encoding="utf-8") as handle:
                    props, _ = _split_frontmatter(handle.read())
            except (OSError, UnicodeError):
                continue
            if props.get("legacy_id"):
                found.add(str(props["legacy_id"]))
    return found


def _available_path(folder, title, legacy_id):
    base = _safe_filename(title)
    candidate = os.path.join(folder, f"{base}.md")
    if not os.path.exists(candidate):
        return candidate
    candidate = os.path.join(folder, f"{base}（Dashboard {legacy_id}）.md")
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base}（Dashboard {legacy_id}-{suffix}）.md")
        suffix += 1
    return candidate


def pending_legacy_count():
    """尚未生成对应 Markdown 的旧全局笔记/参考资料数量。"""
    root = library_root()
    known_ids = _legacy_ids(root) if root else set()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM notes WHERE project_id IS NULL")
        ids = [f"note-{row['id']}" for row in cur.fetchall()]
        cur.execute("SELECT id FROM reference_items")
        ids.extend(f"reference-{row['id']}" for row in cur.fetchall())
    return sum(legacy_id not in known_ids for legacy_id in ids)


def migrate_legacy():
    """把旧全局学习数据保留式迁移为 Markdown；不删 DB，也不覆盖现有文件。"""
    root = library_root()
    if not root:
        return {"migrated": 0, "skipped": 0, "root": None}
    os.makedirs(root, exist_ok=True)
    known_ids = _legacy_ids(root)
    counts = {"migrated": 0, "skipped": 0, "root": root}
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE project_id IS NULL ORDER BY id")
        notes = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM reference_items ORDER BY id")
        references = [dict(row) for row in cur.fetchall()]
    for note in notes:
        legacy_id = f"note-{note['id']}"
        if legacy_id in known_ids:
            counts["skipped"] += 1
            continue
        lines = _frontmatter(
            note["title"], legacy_id, note.get("created_at"),
            updated=note.get("updated_at"), tags=_tags(note.get("tags")),
        )
        lines.extend([f"# {note['title']}", "", (note.get("body") or "").rstrip(), ""])
        path = _available_path(root, note["title"], legacy_id)
        with open(path, "x", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        counts["migrated"] += 1
        known_ids.add(legacy_id)
    for ref in references:
        legacy_id = f"reference-{ref['id']}"
        if legacy_id in known_ids:
            counts["skipped"] += 1
            continue
        lines = _frontmatter(ref["title"], legacy_id, ref.get("created_at"))
        lines.extend([f"# {ref['title']}", ""])
        body = (ref.get("body") or "").rstrip()
        if body:
            lines.extend([body, ""])
        try:
            links = json.loads(ref.get("links") or "[]")
        except (ValueError, TypeError):
            links = []
        if links:
            lines.extend(["## 链接", ""])
            for link in links:
                url = (link.get("url") or "").strip()
                if url:
                    lines.append(f"- [{link.get('name') or url}]({url})")
            lines.append("")
        path = _available_path(root, ref["title"], legacy_id)
        with open(path, "x", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        counts["migrated"] += 1
        known_ids.add(legacy_id)
    return counts
