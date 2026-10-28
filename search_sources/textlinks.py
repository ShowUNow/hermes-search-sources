"""Pages named in plain-text / Markdown search output.

Plenty of search tools do not return JSON: Markdown lists (`1. [Title](https://…) — summary`), SerpApi's
Markdown tables, `Title: … / URL: … / Content: …` blocks (Tavily, Brave, Exa MCP servers), numbered
"Sources:" lists, or an answer with inline links. This reads all of those line by line:

  * a Markdown link or bare URL starts a page; the link text (or text before a bare URL, or an earlier
    `Title:` / heading / bold line) is its title, text after it or the next line its summary;
  * `Title:` / `URL:` / `Description:` style fields fill the page they belong to, in either order;
  * a table row is one page, its other cells the summary;
  * a blank line or a rule ends the page.
"""

from __future__ import annotations

import re
from bisect import bisect_right

from .textutil import SNIPPET_CAP, TITLE_CAP, clean_text, tidy
from .urls import clean_url, scan_urls, url_key

MAX_TEXT_HITS = 60
_MAX_TEXT_CHARS = 200_000
_MIN_SNIPPET = 12
_MAX_PLAIN_TITLE = 100  # a longer plain line is prose, not the title of the link below it

_TITLE_FIELDS = frozenset({"title", "name", "headline"})
_URL_FIELDS = frozenset({"url", "link", "href", "source url", "page url", "uri", "source"})
_SNIPPET_FIELDS = frozenset({"snippet", "description", "desc", "summary", "content", "excerpt", "text",
                             "highlight", "highlights", "abstract", "body"})
_META_FIELDS = frozenset({"published", "published date", "date", "author", "authors", "score", "id", "favicon",
                          "image", "images", "language", "rank", "position", "type", "publisher", "age", "domain"})
_FIELD_RE = re.compile(r"^\s*(?:[-*•>]\s+|\d{1,3}[.)]\s+)?([A-Za-z][A-Za-z ]{0,24}?)\s*[:：]\s*(.*)$")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+([^\r\n]*)")
_BOLD_LINE_RE = re.compile(r"^\s*(?:[-*•]\s+|\d{1,3}[.)]\s+)?(?:\*\*|__)(.+?)(?:\*\*|__)\s*$")
_NUMBERED_RE = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
_RULE_RE = re.compile(r"^\s*(?:[-*_=]{3,}|:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)*)\s*\|?\s*$")
_ERROR_RE = re.compile(r"^\W*(?:error|failed|failure|exception|traceback|http error|unauthorized|forbidden)\b", re.I)
_VAGUE_LINK_TEXT = frozenset({"here", "link", "source", "more", "read more", "this", "website", "site", "url", "page"})
_CELL_JUNK_RE = re.compile(r"^[\d\s.,:/#-]*$")
_HTML_RE = re.compile(r"\s*(?:<!doctype\s+html|<html\b)", re.I)


def looks_like_error(text: str) -> bool:
    """Error messages mention URLs too (the endpoint that failed); they are not search results."""
    return _ERROR_RE.match(text[:200]) is not None


_MAX_TARGET = 2048  # longest `(…)` a link target may have
_MAX_LINK_TEXT = 300  # longest `[…]` before `](`


def _markdown_links(line: str) -> list[tuple[int, int, str, str]]:
    """`(start, end, text, url)` of each `[text](url)` in `line`; the URL may hold balanced parentheses.

    Linear in the line: a target is read at most `_MAX_TARGET` characters, the text is looked for in a bounded
    window, and the whole line shares one scan budget, so a line of nothing but `](` costs no more than
    its length."""
    found = []
    budget = 4 * len(line) + 2048
    pos = 0
    while budget > 0 and (open_at := line.find("](", pos)) != -1:
        start = line.rfind("[", max(0, open_at - _MAX_LINK_TEXT), open_at)
        pos = open_at + 2
        if start == -1:
            continue
        depth, i = 1, pos
        stop = min(len(line), pos + _MAX_TARGET)
        while i < stop and depth:
            char = line[i]
            depth += (char == "(") - (char == ")")
            i += 1
        budget -= i - pos
        if depth:
            continue
        target = line[open_at + 2:i - 1].strip().lstrip("<")
        url = clean_url(re.split(r'[\s>]', target, maxsplit=1)[0])
        if url:
            found.append((start, i, line[start + 1:open_at], url))
            pos = i
    return found


def _meaningful(text: str) -> str:
    cleaned = tidy(text, TITLE_CAP)
    if len(cleaned) < 2 or cleaned.lower() in _VAGUE_LINK_TEXT or _CELL_JUNK_RE.match(cleaned) or clean_url(cleaned):
        return ""
    return cleaned


def _items(line: str) -> list[tuple[int, int, str, str]]:
    """Links in `line`, Markdown or bare, in order: `(start, end, link_text, url)`."""
    marked = _markdown_links(line)
    starts = [s for s, _e, _t, _u in marked]
    bare = []
    for s, e, u in scan_urls(line):
        at = bisect_right(starts, s) - 1  # `marked` is ordered and disjoint: only its neighbour can cover `s`
        if at < 0 or s >= marked[at][1]:
            bare.append((s, e, "", u))
    return sorted(marked + bare)[: MAX_TEXT_HITS * 3]


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if stripped.count("|") < 2 or not (stripped.startswith("|") or stripped.endswith("|")):
        return None
    return [c.strip() for c in stripped.strip("|").split("|")]


class _Reader:
    def __init__(self) -> None:
        self.hits: list[dict] = []
        self.current: dict | None = None
        self.title = ""
        self.snippet = ""

    def reset(self) -> None:
        self.current, self.title, self.snippet = None, "", ""

    def add(self, url: str, title: str, snippet: str) -> dict:
        hit = {"url": url, "title": title or self.title, "snippet": snippet or self.snippet, "failed": False}
        self.title = self.snippet = ""
        self.hits.append(hit)
        self.current = hit
        return hit

    # one kind of line each -------------------------------------------------------------------------
    def table_row(self, cells: list[str]) -> None:
        found = [_items(cell) for cell in cells]
        items = [it for cell_items in found for it in cell_items]
        if not items:
            return
        texts = [t for cell, cell_items in zip(cells, found)
                 if not cell_items and (t := tidy(cell, SNIPPET_CAP)) and not _CELL_JUNK_RE.match(t)]
        single = len(items) == 1
        for _s, _e, text, url in items:
            title = _meaningful(text) or (max(texts, key=len)[:TITLE_CAP] if texts and single else "")
            summary = " · ".join(t for t in texts if t != title)[:SNIPPET_CAP] if single else ""
            self.add(url, title, summary)

    def field(self, key: str, value: str) -> bool:
        if key in _TITLE_FIELDS:
            self.current, self.snippet = None, ""
            self.title = tidy(value, TITLE_CAP)
        elif key in _URL_FIELDS:
            urls = scan_urls(value)
            if not urls:
                return key == "source"  # `Source: Reuters` is metadata, not a page
            self.add(urls[0][2], "", "")
        elif key in _SNIPPET_FIELDS:
            snippet = tidy(value, SNIPPET_CAP)
            if self.current is not None and not self.current["snippet"]:
                self.current["snippet"] = snippet
            else:
                self.snippet = snippet
        else:
            return key in _META_FIELDS
        return True

    def links(self, line: str, items: list[tuple[int, int, str, str]]) -> None:
        single = len(items) == 1
        for start, end, text, url in items:
            # `[1](url)` is a citation marker, not a title: the page keeps its domain as the label. Text before a
            # bare URL ("Title - https://…") is the title.
            title = _meaningful(text) if text else (_meaningful(line[:start]) if single else "")
            snippet = tidy(line[end:], SNIPPET_CAP) if single else ""
            self.add(url, title, snippet if len(snippet) >= _MIN_SNIPPET else "")

    def prose(self, line: str) -> None:
        heading = _HEADING_RE.match(line) or _BOLD_LINE_RE.match(line) or _NUMBERED_RE.match(line)
        text = tidy(heading.group(1).rstrip(" #\t") if heading else line, SNIPPET_CAP)
        if not text:
            return
        if self.current is None:
            if not text.endswith((":", "：")) and (heading or len(text) <= _MAX_PLAIN_TITLE):
                self.title = text[:TITLE_CAP]
        elif not heading and not self.current["snippet"]:
            self.current["snippet"] = text
        elif not heading and len(self.current["snippet"]) < SNIPPET_CAP:
            self.current["snippet"] = clean_text(f'{self.current["snippet"]} {text}', SNIPPET_CAP)
        elif heading:  # a heading after a finished page starts the next one
            self.current, self.snippet = None, ""
            self.title = text[:TITLE_CAP]


def parse_text(text: object) -> list[dict]:
    """`[{url, title, snippet, failed}]` for every page named in `text`, de-duplicated, in order."""
    if not isinstance(text, str) or "http" not in text.lower() or len(text) > _MAX_TEXT_CHARS or looks_like_error(text):
        return []
    if _HTML_RE.match(text):  # a raw HTML page is full of asset and navigation URLs, none of them results
        return []
    reader = _Reader()
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            reader.reset()
        elif _RULE_RE.match(line):
            if "|" not in line:  # a table's separator row ends nothing
                reader.reset()
        elif (cells := _table_cells(line)) is not None:
            reader.table_row(cells)
        elif (m := _FIELD_RE.match(line.replace("**", "").replace("__", ""))) and reader.field(
                m.group(1).strip().lower(), m.group(2)):
            continue
        elif items := _items(line):
            reader.links(line, items)
        else:
            reader.prose(line)
        if len(reader.hits) >= MAX_TEXT_HITS * 3:
            break
    seen: dict[str, dict] = {}
    for hit in reader.hits:
        seen.setdefault(url_key(hit["url"]), hit)
    return list(seen.values())[:MAX_TEXT_HITS]
