"""Which tools return web pages, and what kind of call they are.

Kinds: `search` (a query in, pages out), `fetch` (a URL in, that page out) and `web` (one tool that does
either, told apart only by its arguments). Everything else — local files, memory, databases, mail, tickets —
is `None`, so a local `search_files` never becomes a "searched page".

Three layers, most specific first:
  1. exact names: Hermes built-ins, the plugin catalog's web/search plugins, the Grok MCP server;
  2. a few catalog families spelled as patterns (`serpapi_*_search`);
  3. community MCP / connector tools (`mcp__<server>__<tool>`, `connectors__<connector>__<tool>`) and
     plugin tools named the same way, judged by the words in the tool and server names — after a
     deny-list of local/private nouns has had its say.
A wrong guess costs little: a source is only recorded when the output holds an http(s) URL (or the call
named one), never from the tool's name alone.
"""

from __future__ import annotations

import re

SEARCH = "search"
FETCH = "fetch"
WEB = "web"

_EXACT = {
    # Hermes built-ins
    "web_search": SEARCH, "web_extract": FETCH,
    # Grok MCP server (catbus)
    "mcp__grok__grok_search": SEARCH, "mcp__grok__web_fetch": FETCH, "mcp__grok__post_fetch": FETCH,
    # plugin catalog, category `web`
    "brave_web_search": SEARCH,
    "linkup_search": SEARCH, "linkup_research": SEARCH, "linkup_research_status": SEARCH, "linkup_fetch": FETCH,
    "search1api_search": SEARCH, "search1api_news": SEARCH, "search1api_ask": SEARCH, "search1api_crawl": FETCH,
    "web_search_plus": SEARCH, "web_extract_plus": FETCH,
    "yandex_generative_search": SEARCH,
    "markdown_new": WEB,  # search / read / crawl / convert in one tool
    "mrscraper_search_google_serp": SEARCH,
    "mrscraper_crawl_website_urls": FETCH, "mrscraper_extract_page_by_prompt": FETCH,
    "mrscraper_extract_listings": FETCH, "mrscraper_extract_structured_data": FETCH,
    "mrscraper_fetch_rendered_html": FETCH, "mrscraper_run_existing_scraper": FETCH,
    "mrscraper_run_existing_scraper_batch": FETCH, "mrscraper_get_results": FETCH,
    "mrscraper_get_latest_results": FETCH, "mrscraper_get_result_detail": FETCH,
    # plugin catalog, other categories whose output is web pages
    "acedatacloud_google_search_search": SEARCH,
    "openalex_search": SEARCH, "openalex_related": SEARCH, "openalex_harvest": SEARCH,
    "openalex_get": FETCH, "openalex_fulltext": FETCH,
}
_PATTERNS = ((re.compile(r"^serpapi_[a-z0-9_]+_search$"), SEARCH),)

_NAMESPACES = ("mcp__", "connectors__")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Nouns that mean "not the public web": any of them in the tool or server name rules the tool out.
_LOCAL = frozenset({
    "file", "files", "session", "sessions", "memory", "memories", "viking", "hicortex", "cortex", "index",
    "knowledge", "codebase", "code", "repo", "repos", "repository", "issue", "issues", "ticket", "tickets",
    "message", "messages", "email", "mail", "thread", "threads", "chat", "db", "database", "sql", "vector",
    "embedding", "embeddings", "graph", "collection", "collections", "history", "transcript", "transcripts",
    "skill", "skills", "note", "notes", "task", "tasks", "contact", "contacts", "calendar", "vault",
    # systems of record that are not the web
    "github", "gitlab", "git", "slack", "notion", "jira", "confluence", "linear", "gmail", "drive", "sheet",
    "sheets", "postgres", "mysql", "sqlite", "redis", "mongo", "filesystem", "obsidian", "terminal", "shell",
    # verbs that change state or report on the tool itself (`create_website_crawl_scraper`, `account_info`)
    "create", "delete", "remove", "update", "set", "add", "register", "cancel", "stop", "login", "auth",
    "account", "usage", "billing", "credits", "status", "settings", "config",
})
# Names of search / fetch vendors and engines. `_BOTH` vendors do both; `_SEARCH_ONLY` names are too general
# to mean "fetch" (a Google Sheets `read_sheet` is not a web fetch).
_BOTH = frozenset({
    "brave", "duckduckgo", "ddg", "searxng", "serper", "tavily", "exa", "perplexity", "kagi", "jina",
    "firecrawl", "linkup", "search1api", "bocha", "metaso", "scrapingbee", "brightdata", "crawl4ai", "grok",
})
_SEARCH_ONLY = frozenset({"google", "bing", "baidu", "sogou", "yandex", "you", "web", "internet", "online"})
_SEARCH_WORDS = frozenset({"search", "research", "ask", "reason", "answer", "news", "serp"})
_FETCH_WORDS = frozenset({"fetch", "scrape", "crawl", "crawling", "extract", "read", "reader", "map", "visit", "browse"})
_WEB_WORDS = frozenset({"web", "webpage", "url", "website", "internet", "online"})
# Whole tool names that are unambiguous even without a vendor in the name.
_FETCH_STEMS = frozenset({
    "fetch", "scrape", "crawl", "read_url", "url_read", "fetch_url", "fetch_web", "fetch_webpage", "fetch_content",
    "web_fetch", "web_extract", "web_scrape", "web_reader", "read_webpage", "read_web_page", "visit_page",
    "visit_url", "open_url", "scrape_url", "scrape_page", "extract_url", "crawl_url", "post_fetch",
    "url_to_markdown", "webpage_to_markdown", "web_url_read",
})
_SEARCH_STEMS = frozenset({
    "web_search", "search_web", "websearch", "internet_search", "search_internet", "online_search", "grok_search",
})


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


def _split_namespaced(name: str) -> tuple[str, str] | None:
    for prefix in _NAMESPACES:
        if name.startswith(prefix):
            server, sep, tool = name[len(prefix):].partition("__")
            return (server, tool) if sep and tool else None
    return None


def _judge(server: str, tool: str, namespaced: bool) -> str | None:
    tool_tokens, server_tokens = _tokens(tool), _tokens(server)
    names = set(tool_tokens) | set(server_tokens)
    if names & _LOCAL:
        return None
    stem = "_".join(tool_tokens)
    # A one-word stem ("fetch") means something only under a server's own namespace.
    if stem in _FETCH_STEMS and (namespaced or "_" in stem):
        return FETCH
    if stem in _SEARCH_STEMS:
        return SEARCH
    words = set(tool_tokens)
    if words & _WEB_WORDS and words & _FETCH_WORDS:
        return FETCH
    if names & (_BOTH | _SEARCH_ONLY) and words & _SEARCH_WORDS:
        return SEARCH
    if names & _BOTH and words & _FETCH_WORDS:
        return FETCH
    return None


def tool_kind(name: object) -> str | None:
    """`"search"`, `"fetch"` or `"web"` for a tool whose output names web pages, else None."""
    if not isinstance(name, str) or not name:
        return None
    if kind := _EXACT.get(name):
        return kind
    for pattern, kind in _PATTERNS:
        if pattern.match(name):
            return kind
    parts = _split_namespaced(name)
    return _judge(parts[0], parts[1], True) if parts else _judge("", name, False)
