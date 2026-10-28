"""One turn's pages -> the record Desktop renders, and the directive that points at it."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Iterable

from .urls import domain_of, reply_links, url_key

DIRECTIVE_NAME = "search-sources"
MAX_SOURCES = 60
RECORD_VERSION = 1

_ID_RE = re.compile(r"^ss-[0-9a-f]{12}$")
_DIRECTIVE_RE = re.compile(r"(?:^|\s)::" + re.escape(DIRECTIVE_NAME) + r"\{")
_DIRECTIVE_LINE_RE = re.compile(r"^\s*::" + re.escape(DIRECTIVE_NAME) + r"(?:\{[^{}]*\})?\s*$")
_FENCE_RE = re.compile(r"(?m)^\s{0,3}(?:```|~~~)")
_FENCE_LINE_RE = re.compile(r"^\s{0,3}(?:```|~~~)")


def is_record_id(value: object) -> bool:
    return isinstance(value, str) and _ID_RE.match(value) is not None


def record_id(session_id: str, turn_id: str) -> str:
    digest = hashlib.sha1(f"{session_id}\0{turn_id}".encode("utf-8", "replace")).hexdigest()
    return f"ss-{digest[:12]}"


def merge_sources(raw: Iterable[dict]) -> list[dict]:
    """Collapse repeated visits to one page into a single entry, in order of first retrieval. The fullest
    title and snippet win; every tool and query that touched the page is kept."""
    merged: dict[str, dict] = {}
    for item in raw:
        key = url_key(item.get("url"))
        if not key:
            continue
        entry = merged.get(key)
        if entry is None:
            entry = merged[key] = {
                "url": item["url"], "key": key, "title": "", "snippet": "", "domain": domain_of(item["url"]),
                "tools": [], "queries": [], "order": len(merged),
            }
        for field in ("title", "snippet"):
            if len(item.get(field) or "") > len(entry[field]):
                entry[field] = item[field]
        for field, value in (("tools", item.get("tool")), ("queries", item.get("query"))):
            if value and value not in entry[field]:
                entry[field].append(value)
    return list(merged.values())


def mark_cited(sources: list[dict], reply: str) -> list[dict]:
    """Flag the pages the reply links to and put them first, in the order the reply first mentions them;
    the rest follow in retrieval order."""
    position = {key: i for i, key in enumerate(reply_links(reply))}
    for source in sources:
        source["cited"] = source["key"] in position
    return sorted(sources, key=lambda s: (0, position[s["key"]]) if s["cited"] else (1, s["order"]))


def _label(sources: list[dict]) -> str:
    for source in sources:
        if source["queries"]:
            return source["queries"][0]
    first = sources[0]
    return first["title"] or first["domain"]


def build_record(session_id: str, turn_id: str, raw_sources: Iterable[dict], reply: str,
                 now: float | None = None) -> dict | None:
    """The record for one turn, or None when no page was touched."""
    sources = mark_cited(merge_sources(raw_sources), reply)
    if not sources:
        return None
    total, cited = len(sources), sum(1 for s in sources if s["cited"])
    public = [
        {
            "url": s["url"], "title": s["title"] or s["domain"], "snippet": s["snippet"], "domain": s["domain"],
            "tools": s["tools"], "queries": s["queries"], "cited": s["cited"],
        }
        for s in sources[:MAX_SOURCES]
    ]
    return {
        "v": RECORD_VERSION,
        "id": record_id(session_id, turn_id),
        "session_id": session_id,
        "turn_id": turn_id,
        "created": int(now if now is not None else time.time()),
        "label": _label(sources),
        "total": total,
        "cited": cited,
        "sources": public,
    }


def directive(record: dict) -> str:
    """`::search-sources{id="…" n="7" c="3"}` — attributes are counts only; the page list travels separately."""
    return f'::{DIRECTIVE_NAME}{{id="{record["id"]}" n="{record["total"]}" c="{record["cited"]}"}}'


def has_directive(text: str) -> bool:
    return _DIRECTIVE_RE.search(text) is not None


def strip_directives(text: str) -> str:
    """The reply without any `::search-sources` paragraph the model wrote itself (it sees earlier ones in its
    history and may copy one, with a stale id). Only a directive alone on its line, outside code fences, is
    removed: that is what Desktop would draw, and prose or code that merely mentions the syntax stays."""
    lines = text.split("\n")
    kept: list[str] = []
    junctions: list[int] = []  # where a directive line was cut out of `kept`
    fenced = False
    for line in lines:
        if _FENCE_LINE_RE.match(line):
            fenced = not fenced
        elif not fenced and _DIRECTIVE_LINE_RE.match(line):
            junctions.append(len(kept))
            continue
        kept.append(line)
    if not junctions:
        return text
    # Only the seam where a line was removed can have gained a doubled blank line; blank lines elsewhere —
    # inside a code block, say — are the reply's own.
    for at in reversed(junctions):
        while 0 < at < len(kept) and not kept[at - 1].strip() and not kept[at].strip():
            del kept[at]
    return "\n".join(kept).rstrip()


def append_directive(reply: str, record: dict) -> str:
    """The reply with the directive as its own last paragraph. A fence the model left open would swallow it,
    so it is closed first."""
    body = reply.rstrip()
    fences = [m.group(0).strip()[:3] for m in _FENCE_RE.finditer(body)]
    if len(fences) % 2:
        body += f"\n{fences[-1]}"
    return f"{body}\n\n{directive(record)}"
