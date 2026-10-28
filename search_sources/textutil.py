"""Plain-text cleanup shared by the JSON and the Markdown/plain-text readers."""

from __future__ import annotations

import html
import re
from typing import Any

TITLE_CAP = 160
SNIPPET_CAP = 220

# Every pattern here is linear on hostile input: line prefixes use `[ \t]` (never `\s`, which crosses newlines),
# bracketed spans are length-bounded, and `clean_text` looks at a bounded prefix of its input.
_MD_IMAGE_RE = re.compile(r"!\[[^\]\n]{0,200}\]\([^)\n]{0,2048}\)")
_MD_LINK_RE = re.compile(r"\[([^\]\n]{0,200})\]\([^)\n]{0,2048}\)")
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_MD_LINE_PREFIX_RE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|>[ \t]*|[-*+][ \t]+|\d{1,9}\.[ \t]+)|\|")
_MD_EMPHASIS_RE = re.compile(r"\*{1,3}|`{1,3}|~~")  # underscores stay: snake_case is text, not emphasis
_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,3}[ \t]+([^\r\n]{1,400})")
_HEADING_SCAN = 8192
_EDGE_PUNCT = " \t-–—:|·•([<>"


def clean_text(value: Any, cap: int) -> str:
    """One plain-text line from Markdown/HTML-ish `value`, at most `cap` characters."""
    if not isinstance(value, str):
        return ""
    value = value[: max(cap * 8, 4096)]  # only `cap` characters survive; never read the rest
    if not value.strip():
        return ""
    text = html.unescape(value)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub(" ", text)
    text = _MD_EMPHASIS_RE.sub("", _MD_LINE_PREFIX_RE.sub(" ", text))
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def tidy(value: str, cap: int) -> str:
    """`clean_text` without the separators and half-open brackets a link leaves at the edges of the text
    around it (`(Title) https://…` keeps "Title"; `Title (2024)` keeps its parentheses)."""
    text = clean_text(value, cap).strip(_EDGE_PUNCT).strip()
    for opener, closer in (("(", ")"), ("[", "]")):
        if text.endswith(closer) and text.count(closer) > text.count(opener):
            text = text[:-1].rstrip()
        if text.startswith(opener) and text.count(opener) > text.count(closer):
            text = text[1:].lstrip()
    return text


def heading_title(markdown: Any) -> str:
    """The first `#`/`##`/`###` heading of Markdown text, cleaned; `""` when there is none."""
    if isinstance(markdown, str) and (found := _HEADING_RE.search(markdown[:_HEADING_SCAN])):
        return clean_text(found.group(1).rstrip(" #\t"), TITLE_CAP)
    return ""
