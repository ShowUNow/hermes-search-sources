"""Turn the payload of a web search / fetch tool call into the pages it touched.

Payloads seen here are the raw handler results, before Hermes wraps them for the model: a JSON string
(`web_search`, `brave_web_search`, `linkup_*`), Markdown or plain text (SerpApi, many MCP search servers),
an MCP text part list, or the `{"result": "<json>"}` envelope MCP servers add (`mcp__grok__*`). Parsing is
deliberately tolerant — a result nobody can read yields no pages, never an error.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .textlinks import looks_like_error, parse_text
from .textutil import SNIPPET_CAP, TITLE_CAP, clean_text, heading_title
from .tools import FETCH, SEARCH, WEB, tool_kind
from .urls import clean_url

__all__ = ["BRIDGE_TOOL", "decode", "extract", "find_hits", "tool_kind"]

BRIDGE_TOOL = "tool_call"

_MAX_DECODE_CHARS = 4_000_000
_MAX_HITS = 200

# Order matters: the first URL-shaped key a result carries is the page. `doi`, `oa_url` and `id` are what
# scholarly APIs (OpenAlex) put where a search API puts `url`.
_URL_KEYS = ("url", "link", "href", "source_url", "sourceUrl", "canonical_url", "page_url", "landing_page_url",
             "oa_url", "doi", "uri", "id")
_TITLE_KEYS = ("title", "name", "headline", "display_name")
_SNIPPET_KEYS = ("description", "snippet", "summary", "excerpt", "abstract", "used_text", "text", "content", "body",
                 "markdown")
_URL_LIST_KEYS = frozenset({"citations", "sources", "references", "urls", "links"})
_ENVELOPE_KEYS = frozenset({"result", "isError", "is_error", "structuredContent", "meta", "_meta"})
# String fields of an answer-style payload that may carry Markdown links when no structured hit exists.
_TEXT_FIELDS = ("answer", "content", "text", "result", "output", "summary", "markdown", "message", "response")
_QUERY_KEYS = ("query", "q", "search_query", "searchQuery", "question", "keywords")
_URL_ARGS = ("url", "link", "href", "target_url", "start_url")

_UNTRUSTED_OPEN_RE = re.compile(r"^<untrusted_tool_result\b[^>]*>\s*")
_UNTRUSTED_CLOSE = "</untrusted_tool_result>"
_ERROR_STATUS_RE = re.compile(r"^(?:error|failed|failure|fatal|exception)$", re.IGNORECASE)
_JSON = json.JSONDecoder()


# --- payload decoding --------------------------------------------------------------------------------


def _strip_untrusted(text: str) -> str:
    opening = _UNTRUSTED_OPEN_RE.match(text)
    if not opening:
        return text
    close = text.rfind(_UNTRUSTED_CLOSE)
    if close <= opening.end():
        return text
    body = text[opening.end():close].strip()
    start = body.find("\n\n")  # the boilerplate warning is one paragraph; the payload follows it
    return (body if start == -1 else body[start + 2:]).strip()


def decode(value: Any, depth: int = 0) -> Any:
    """The structured payload inside a tool result: JSON text, MCP part lists and `{"result": ...}`
    envelopes are peeled; text that is not JSON comes back as a plain string."""
    if depth > 4:
        return value
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    if isinstance(value, list):
        texts = [p["text"] for p in value if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return decode("\n".join(texts), depth + 1) if texts else value
    if isinstance(value, str):
        if len(value) > _MAX_DECODE_CHARS:
            return ""
        text = _strip_untrusted(value.strip()).strip()
        if text[:1] in ("{", "["):
            try:
                parsed, _end = _JSON.raw_decode(text)
            except (ValueError, RecursionError):  # malformed, or nested deeper than the parser's stack allows
                return text
            return decode(parsed, depth + 1)
        return text
    if isinstance(value, dict):
        inner = value.get("result")
        if isinstance(inner, (str, dict, list)) and not (set(value) - _ENVELOPE_KEYS):
            return decode(inner, depth + 1)
        return value
    return value


# --- hit discovery in JSON ---------------------------------------------------------------------------


def _first_text(item: dict, keys: Iterable[str], cap: int) -> str:
    for key in keys:
        if text := clean_text(item.get(key), cap):
            return text
    return ""


def _hit(item: dict, url: str) -> dict:
    snippet = _first_text(item, _SNIPPET_KEYS, SNIPPET_CAP)
    title = _first_text(item, _TITLE_KEYS, TITLE_CAP) or next(
        (t for t in (heading_title(item.get(k)) for k in ("markdown", "content", "text")) if t), "")
    return {
        "url": url, "title": title, "snippet": snippet,
        "failed": bool(item.get("error")) and not _first_text(item, ("content", "text", "markdown"), 40),
    }


def find_hits(payload: Any, limit: int = _MAX_HITS) -> list[dict]:
    """Every object carrying a page URL, in document order. A matched object is a leaf: nested lists inside a
    hit (comments, related links) are not that hit's siblings."""
    hits: list[dict] = []
    stack: list[Any] = [payload]
    while stack and len(hits) < limit:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        url = next((u for u in (clean_url(node.get(k)) for k in _URL_KEYS) if u), "")
        if url:
            hits.append(_hit(node, url))
            continue
        children: list[Any] = []
        for key, value in node.items():
            if key in _URL_LIST_KEYS and isinstance(value, list):
                children.extend({"url": v} if isinstance(v, str) else v for v in value)
            elif isinstance(value, (dict, list)):
                children.append(value)
        stack.extend(reversed(children))
    return hits


# --- per-kind extraction -----------------------------------------------------------------------------


def _absolute(value: Any) -> str:
    """Reddit-style permalinks arrive as paths."""
    text = value if isinstance(value, str) else ""
    return clean_url("https://www.reddit.com" + text) if text.startswith("/") else clean_url(text)


def _fetched_page(payload: dict, arg_url: str) -> list[dict]:
    """The page a social/web fetch tool returned: `{url, title?, post?: {...}, content|text|transcript}`."""
    post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
    url = (clean_url(payload.get("url")) or _absolute(post.get("permalink")) or clean_url(post.get("url"))
           or arg_url)
    if not url:
        return []
    body_keys = ("content", "markdown", "text", "body", "full_text", "selftext", "description", "summary")
    raw = next((s for s in (payload.get(k) for k in body_keys) if isinstance(s, str) and s.strip()), None)
    if raw is None:
        raw = next((s for s in (post.get(k) for k in body_keys) if isinstance(s, str) and s.strip()), "")
    if not raw and isinstance(payload.get("transcript"), (str, dict, list)):
        raw = json.dumps(payload["transcript"], ensure_ascii=False)
    title = clean_text(payload.get("title"), TITLE_CAP) or clean_text(post.get("title"), TITLE_CAP) or heading_title(raw)
    return [{"url": url, "title": title, "snippet": clean_text(raw, SNIPPET_CAP), "failed": False}]


def _failed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    return (payload.get("success") is False or payload.get("ok") is False or bool(payload.get("error"))
            or (isinstance(status, str) and _ERROR_STATUS_RE.match(status.strip()) is not None))


def _arg_urls(args: dict) -> list[str]:
    values: list[Any] = [args.get(k) for k in _URL_ARGS]
    urls = args.get("urls")
    values += urls if isinstance(urls, list) else [urls]
    return list(dict.fromkeys(u for u in (clean_url(v) for v in values) if u))


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text_blocks(payload: Any) -> str:
    """The answer-style string fields of a payload that has no structured hits (Perplexity-style
    `{"answer": "…[1](https://…)"}` or `{"content": "1. [Title](…)"}`)."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return "\n\n".join(v for k in _TEXT_FIELDS if isinstance(v := payload.get(k), str) and v.strip())
    return ""


def _hits_for(kind: str, tool_name: str, args: dict, payload: Any) -> list[dict]:
    """Pages in one decoded payload. JSON first; Markdown / plain text next (search-like kinds); the pages the
    call was asked for last (fetch-like kinds)."""
    searching, fetching = kind in (SEARCH, WEB), kind in (FETCH, WEB)
    arg_urls = _arg_urls(args)
    if fetching and not searching and isinstance(payload, dict) and tool_name.startswith("mcp__"):
        # A failed fetch still echoes the URL it was asked for; that page was not read.
        hits = [] if _failed(payload) else _fetched_page(payload, arg_urls[0] if arg_urls else "")
        return hits + find_hits(payload.get("citations") or [])
    hits = find_hits(payload) if isinstance(payload, (dict, list)) else []
    if hits or _failed(payload):
        return hits
    if searching and not (isinstance(payload, str) and looks_like_error(payload)):
        if hits := parse_text(_text_blocks(payload)):
            return hits
    if fetching and not looks_like_error(payload if isinstance(payload, str) else ""):
        if isinstance(payload, dict):  # `{"url"-less content}`, e.g. linkup_fetch's `markdown`
            return _fetched_page(payload, arg_urls[0]) if arg_urls else []
        text = payload if isinstance(payload, str) else ""
        return [{"url": u, "title": "", "snippet": clean_text(text, SNIPPET_CAP), "failed": False} for u in arg_urls]
    return []


def _from_call(tool_name: str, kind: str, args: dict, result: Any) -> list[dict]:
    query = next((q.strip() for q in (args.get(k) for k in _QUERY_KEYS) if isinstance(q, str) and q.strip()), "")
    return [
        {"url": h["url"], "title": h["title"], "snippet": h["snippet"], "tool": tool_name, "query": query}
        for h in _hits_for(kind, tool_name, args, decode(result)) if not h["failed"]
    ]


def _from_bridge(args: dict, result: Any) -> list[dict]:
    """`tool_call` wrapper of a connector batch: `{"results": [{"index", "name", "response"|"error"}]}`."""
    entries = args.get("calls")
    if entries is None:
        entries = [{"name": args.get("name"), "arguments": args.get("arguments")}]
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except ValueError:
            return []
    if isinstance(entries, dict):
        entries = [entries]
    payload = decode(result)
    answers = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not isinstance(answers, list):
        return []
    sources: list[dict] = []
    for answer in answers:
        if not isinstance(answer, dict) or "response" not in answer:
            continue
        index = answer.get("index")
        entry = entries[index] if isinstance(index, int) and 0 <= index < len(entries) else {}
        name = answer.get("name") or (entry.get("name") if isinstance(entry, dict) else None)
        if (kind := tool_kind(name)) is None:
            continue
        call_args = _json_object(entry.get("arguments")) if isinstance(entry, dict) else {}
        sources += _from_call(str(name), kind, call_args, answer["response"])
    return sources


def extract(tool_name: object, args: Any, result: Any) -> list[dict]:
    """Pages touched by one tool call: `[{url, title, snippet, tool, query}]`, empty for any other tool."""
    call_args = _json_object(args)
    if tool_name == BRIDGE_TOOL:
        return _from_bridge(call_args, result)
    kind = tool_kind(tool_name)
    return _from_call(str(tool_name), kind, call_args, result) if kind else []
