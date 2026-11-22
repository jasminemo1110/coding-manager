"""Read and briefly cache public GitHub release notes for project detail pages."""

import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote, urljoin, urlsplit

import requests
from markdown_it import MarkdownIt
from markupsafe import Markup


_CACHE_TTL = 300
_ERROR_TTL = 15
_CACHE_SIZE = 64
_cache = OrderedDict()
_cache_lock = threading.Lock()
_markdown = MarkdownIt("gfm-like", {"html": False}).disable("image")


def _render_link(renderer, tokens, index, options, env):
    token = tokens[index]
    try:
        href = urljoin(env["release_url"], token.attrGet("href"))
        scheme = urlsplit(href).scheme
    except ValueError:
        scheme = None
    if scheme in {"https", "http", "mailto"}:
        token.attrSet("href", href)
        token.attrSet("target", "_blank")
        token.attrSet("rel", "noopener noreferrer")
    else:
        token.attrs.pop("href", None)
    return renderer.renderToken(tokens, index, options, env)


_markdown.add_render_rule("link_open", _render_link)


def _fetch_releases(repo):
    releases = {}
    url = f"https://api.github.com/repos/{repo}/releases"
    with requests.Session() as session:
        # Public reads must stay anonymous even if the machine has GitHub credentials in .netrc.
        session.trust_env = False
        page = 1
        while True:
            response = session.get(
                url, params={"per_page": 100, "page": page}, timeout=5,
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError("Expected a list of GitHub releases")
            for item in batch:
                if item["draft"] or not item["published_at"]:
                    continue
                tag = item["tag_name"]
                release_url = f"https://github.com/{repo}/releases/tag/{quote(tag, safe='')}"
                body = (item.get("body") or "").strip()
                releases[tag] = {
                    "tag": tag,
                    "name": item.get("name"),
                    "published_at": item["published_at"],
                    "prerelease": item["prerelease"],
                    "url": release_url,
                    "notes": Markup(_markdown.render(body, {"release_url": release_url})) if body else None,
                }
            if "next" not in response.links:
                break
            page += 1
    return sorted(releases.values(), key=lambda release: release["published_at"], reverse=True)


def public_releases(repo, *, refresh=False):
    """Return published releases, or None when GitHub cannot provide the list."""
    if not re.fullmatch(r"[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+", repo):
        return None
    key = repo.casefold()
    started = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and not refresh and started < entry[1]:
            _cache.move_to_end(key)
            return entry[2]

    try:
        releases = _fetch_releases(repo)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        releases = None

    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > started:
            return entry[2]
        ttl = _ERROR_TTL if releases is None else _CACHE_TTL
        _cache[key] = (started, time.monotonic() + ttl, releases)
        _cache.move_to_end(key)
        if len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return releases
