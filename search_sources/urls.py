"""URL normalisation and reply-citation matching."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "ref_src", "ref_url",
    "spm", "_hsenc", "_hsmi", "mkt_tok", "vero_id", "s_cid",
})
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Bare-URL scanner: stops at whitespace, angle brackets, quotes, backticks and square brackets so a
# Markdown `[https://a.com](https://a.com)` yields two clean candidates instead of one run-on match.
_URL_RE = re.compile(r"https?://[^\s<>\"'`\[\]]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?*_~'\""
_CLOSERS = {")": "(", "}": "{"}


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMS


def clean_url(raw: object) -> str:
    """The http(s) URL to show and open, without credentials; ``""`` for anything else."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return ""
    if not host:
        return ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def url_key(raw: object) -> str:
    """Identity of a page for de-duplication and citation matching.

    Scheme, ``www.``, default port, fragment, trailing slash, tracking parameters and query order do not
    change which page a URL names; everything else does.
    """
    url = clean_url(raw)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ""
    host = domain_of(url)
    if port and port != _DEFAULT_PORTS.get(parts.scheme):
        host = f"{host}:{port}"
    path = quote(unquote(parts.path), safe="/:@!$&'()*+,;=-._~").rstrip("/")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)),
        quote_via=quote,
    )
    return f"{host}{path}?{query}" if query else f"{host}{path}"


_MAX_URL_CHARS = 2048


def _trim(candidate: str) -> str:
    """Drop the sentence punctuation and unbalanced closers a prose URL drags along; `""` for one longer than
    any real URL. One pass: bracket counts are taken once and kept up to date as closers are dropped."""
    end = len(candidate)
    counts = {bracket: candidate.count(bracket) for bracket in "(){}"}
    while end:
        last = candidate[end - 1]
        if last in _TRAILING_PUNCT:
            end -= 1
        elif last in _CLOSERS and counts[last] > counts[_CLOSERS[last]]:
            counts[last] -= 1
            end -= 1
        else:
            break
    return candidate[:end] if end <= _MAX_URL_CHARS else ""


def scan_urls(text: str) -> list[tuple[int, int, str]]:
    """`(start, end, url)` of every http(s) URL in prose, trimmed of sentence punctuation, in text order."""
    spans = []
    for match in _URL_RE.finditer(text):
        trimmed = _trim(match.group(0))
        if trimmed:
            spans.append((match.start(), match.start() + len(trimmed), trimmed))
    return spans


def reply_links(text: object) -> list[str]:
    """Normalised keys of every http(s) link in a reply (Markdown links, autolinks, bare URLs), in order of
    first appearance and without repeats."""
    if not isinstance(text, str) or "http" not in text.lower():
        return []
    keys: dict[str, None] = {}
    for _start, _end, url in scan_urls(text):
        if key := url_key(url):
            keys.setdefault(key)
    return list(keys)
