# search-sources

A "searched N pages" source list for Hermes Desktop, in the style of ChatGPT and Grok on the web. No changes to Hermes core.

After a turn that used web search or page fetching:

1. every external page the turn touched is collected (URL, title, summary, domain, tool) and de-duplicated;
2. the last line of the reply shows a capsule **`Searched N pages`** (stacked site icons, plus how many the reply cites);
3. clicking it brings back and opens the right-hand **Sources** panel: **pages the reply actually links to come first, marked "Cited"**, the other pages searched follow; a card opens the page in your default browser.

The UI is English first and follows the Desktop language (简体中文 / 繁體中文 included; any other language reads English).

## Which tools count

| Kind | Examples |
| --- | --- |
| Hermes built-ins | `web_search`, `web_extract` |
| Plugin catalog | `brave_web_search`, `linkup_search` / `linkup_fetch` / `linkup_research`, `search1api_search` / `_news` / `_ask` / `_crawl`, `web_search_plus`, `web_extract_plus`, `yandex_generative_search`, `serpapi_*_search`, `mrscraper_*` (search / extract / crawl / results), `openalex_*` (search / get / related / fulltext / harvest), `markdown_new` |
| Grok MCP | `mcp__grok__grok_search`, `mcp__grok__web_fetch`, `mcp__grok__post_fetch` |
| Community MCP servers and connectors (`mcp__<server>__<tool>`, `connectors__<connector>__<tool>`) | `tavily_search` / `tavily_extract` / `tavily_crawl`, `web_search_exa` / `exa_search`, `firecrawl_scrape` / `_search` / `_crawl`, `brave_search`, `perplexity_search` / `_ask`, `read_url`, `fetch`, `scrape`, `google_search`, … |

Local and private tools are never counted: file, session, memory, vector, index, code, mail, ticket and database tools (`search_files`, `session_search`, `viking_search`, `hicortex_search`, `post_cortex_search`, `index_search`, …) are ruled out by name, and a page is only recorded when the output holds an http(s) URL (or the call named one). Browser-driving tools (`browser_*`, `browserpaw_*`, `unbrowse_*`) are not "search or fetch" and are not counted.

Results are read as JSON (`results[]` with `url` / `link` / `href` / `doi` / `id`, `sources` / `citations`, MCP text parts, `{"result": "<json>"}` envelopes) and, for search tools, as **Markdown or plain text**: `[Title](url)` lists with summaries underneath, Markdown tables (SerpApi), `Title:` / `URL:` / `Content:` blocks (Tavily, Brave, Exa servers), numbered "Sources:" lists, answers with inline links. Fetch tools record the page they were asked for, never every link on it.

To add a tool, put its exact name in `search_sources/tools.py` (`_EXACT`) or extend the word lists beside it.

## How it works

```
plugins/search-sources/
├── plugin.yaml            agent half: 4 hooks
├── __init__.py            register(ctx)
├── search_sources/
│   ├── tools.py           which tools return web pages (built-ins, catalog, MCP / connector names)
│   ├── extract.py         tool payload -> pages (JSON, MCP parts, envelopes)
│   ├── textlinks.py       Markdown / plain-text search output -> pages
│   ├── urls.py            URL normalisation, links in the reply
│   ├── record.py          merge, "cited" marking and ordering, the directive
│   ├── hooks.py           post_tool_call collection + transform_llm_output injection
│   ├── store.py           records and the Desktop heartbeat (on disk, shared across processes)
│   └── api.py             FastAPI routes
├── dashboard/             manifest.json + plugin_api.py -> /api/plugins/search-sources/
├── desktop/plugin.js      Desktop half (plain ESM, jsx/jsxs, only var(--ui-*) colours)
├── tests/desktop_smoke/   jsdom + React smoke test against a mocked SDK
└── test_plugin.py         pytest
```

- **Collect.** `post_tool_call` fills a bucket per `turn_id`. Deferred tools reach the hook under their real name (a connector batch fires once per entry; the outer `tool_call` is decoded as a fallback). A subagent's pages join the parent turn; the subagent's own reply gets no directive. Failed or blocked calls are ignored.
- **Inject.** `transform_llm_output` — the one hook that sees the final reply and can extend it — appends one paragraph, `::search-sources{id="ss-…" n="7" c="3"}`: an id and two counts, not the page list.
- **Deliver.** The full record is pushed as a `plugin.search-sources.turn.recorded` event to every connected Desktop window, kept on disk for `GET /api/plugins/search-sources/record/<id>`, and cached by Desktop (last 40 turns). The directive's own counts keep the capsule readable even if both are gone.
- **Stay off other surfaces.** The directive is added only when `agent.platform == "desktop"`; Telegram and every other chat platform are never touched. A `::search-sources` line the model copied from its own history (a stale id) is stripped on every platform.
- **Heartbeat gate.** The Desktop half pings `/ping` (on load, at every turn start, every 4 minutes); the agent half adds the directive only if a ping arrived in the last 15 minutes, so a window without this plugin never shows raw `::search-sources{…}` text. `/bye` on unload carries the instance id, so a hot reload cannot erase its successor's check-in.
- **Two panes.** Desktop disables a plugin whose *only* pane is closed, which would turn every capsule back into raw text. `Sources` and `Searches` (recent searches) keep Close a plain dismiss; the capsule calls `host.undismissPane` then `host.revealPane`.

## Install

1. Add `search-sources` to `plugins.enabled` (`hermes plugins enable search-sources`). The agent half is loaded into the running `hermes serve` (Desktop backend) and `hermes gateway run` without a restart, and the `plugin_api.py` routes hot-mount on their first request.
2. **The Desktop half must be on the machine that runs Desktop:** `$HERMES_HOME/desktop-plugins/search-sources/plugin.js` (loaded within a few seconds). With a local Desktop you can instead enable the package's Desktop half in Capabilities → Plugins. **When Desktop connects to a remote backend over SSH, the remote `plugins/` folder is invisible to Electron: copy `desktop/plugin.js` to the client machine.**
3. Keep `desktop/plugin.js` and `desktop-plugins/search-sources/plugin.js` identical (a test checks it).

Settings → Plugins → *Search Sources* has one setting, **Site icons**: *All* (default), *Off*.

## Disclosure

What you would want to know before installing:

- **Stored locally.** `<HERMES_HOME>/plugin-data/search-sources/records/<id>.json` — for each turn that searched or fetched, the page URLs, titles and short summaries, the session and turn ids and the first query (about the 200 newest are kept) — and `desktop-seen.json`, a timestamp written by the Desktop heartbeat. Desktop caches the last 40 turns' records and your icon setting in its own local storage (`hermes.plugin.search-sources.*`). Nothing is stored anywhere else.
- **No network from the agent half.** It reads tool output through hooks (read-only), writes only the files above and starts no process. It reads no credentials, no `.env`, no config.
- **It edits the reply.** For a Desktop turn that searched, one `::search-sources{…}` paragraph is appended to the final reply. It is saved with the transcript and is part of the history the model sees on later turns. It is not added on any other platform.
- **Site icons (Desktop half) contact third parties.** With *All* (default), icons are looked up in this order, stopping at the first that works:
  1. **Google's `s2/favicons` service and DuckDuckGo's `icons.duckduckgo.com` are asked for the domain, and so learn which domains were searched**;
  2. the site's own `/favicon.ico`, loaded by the panel.

  The panel's image requests carry no referrer, and the icon services are told nothing but the domain. *Off* shows a letter badge and contacts no one.
- **Own backend only.** The heartbeat and record lookups go to this plugin's own `/api/plugins/search-sources/` namespace on the Hermes backend you are connected to. No telemetry, no usage reporting.
- **Open links.** A card opens its page in the default browser through `ctx.os.openExternal`, on click only.

## Known limits

- `transform_llm_output` is first-string-wins across plugins: if another plugin also rewrites the final reply, only one rewrite applies.
- The catalog's `source-tray` plugin overlaps (also a Desktop pane of opened URLs, driven by `post_tool_call`). They work side by side but you would see two source panes.
- Output shapes of third-party tools are read generically; a tool whose output carries no http(s) URL records nothing. Raw HTML output is ignored.
- Records are pruned to about 200; for older turns the capsule still shows its count and the panel says the details are unavailable.
- Under `plugins.isolation: host` the hook cannot broadcast to Desktop; the panel then reads `GET /record/<id>` instead.
- Records and the heartbeat live under each profile's own `plugin-data/`.

## Tests

```sh
pytest ~/.hermes/plugins/search-sources/test_plugin.py -q
hermes plugins doctor search-sources
hermes plugins validate ~/.hermes/plugins/search-sources
```

`HERMES_HOME` is redirected to a temp dir and `broadcast_plugin_event` is stubbed (the real one imports `tui_gateway.server`). Covers tool classification (including every tool the relevant catalog plugins declare), JSON / Markdown / plain-text results, citation matching, platform isolation, the real `PluginManager`, the HTTP routes, the Desktop SDK contract and a jsdom run of `plugin.js` in English, 简体中文 and 繁體中文, with and without the optional SDK doors.

---

# 中文说明

仿 ChatGPT / Grok 网页版的「搜索来源」体验，用于 Hermes Desktop，不修改 Hermes 核心源码。

- **采集**：某一轮用到联网搜索或网页抓取后，收集该轮所有外部网页（URL、标题、摘要、域名、来源工具），规范化去重。
- **胶囊**：回复最底部显示 **`已搜索 N 个页面`**（叠放站点图标 + 正文引用数）；界面语言默认英文，随 Desktop 的语言设置显示简体 / 繁体中文。
- **面板**：点击胶囊展开右侧栏 Sources，**回复正文里实际链接到的页面置顶并标「已引用」**，其余已检索页面在下方；点卡片用系统默认浏览器打开。
- **工具范围**：内置 `web_search` / `web_extract`；插件市场的搜索 / 抓取工具（Brave、Linkup、Search1API、web_search_plus、Yandex、SerpApi、MrScraper、OpenAlex、markdown_new）；Grok MCP；社区常见 MCP / connector（Tavily、Exa、Firecrawl、Brave、Perplexity、`read_url`、`fetch`、`scrape` 等）。本地 / 私有工具（文件、会话、记忆、向量、索引、代码、邮件、工单、数据库）按名称排除，且只有输出里真有 http(s) 链接才会记录。除 JSON 外，搜索类工具返回的 Markdown / 纯文本（列表、表格、`Title:`/`URL:` 块、带内联链接的回答）也能解析。
- **只作用于 Desktop**：仅当 `agent.platform == "desktop"` 才在回复末尾追加 `::search-sources{…}`，Telegram 等其他平台绝不追加；Desktop 半边定期向后端报到，没装该插件的窗口不会看到裸指令文本。
- **安装**：`hermes plugins enable search-sources`（运行中的 Desktop 后端和网关即时热加载）。**桌面半边必须在运行 Desktop 的机器上**：`$HERMES_HOME/desktop-plugins/search-sources/plugin.js`；经 SSH 连远端时，请把 `desktop/plugin.js` 复制到客户端本机。设置 → 插件 → Search Sources 可选「站点图标」：全部（默认）/ 关闭。

## 披露（Disclosure）

- **本地存储**：`<HERMES_HOME>/plugin-data/search-sources/records/*.json`（每轮检索的页面 URL、标题、简短摘要、会话与轮次 id、首个查询词，约保留最新 200 条）和 `desktop-seen.json`（心跳时间戳）；Desktop 在自己的本地存储里缓存最近 40 轮记录和图标设置。别处不存。
- **Python 半边不联网**：只通过 hook 只读工具输出，只写上述文件，不启动子进程，不读取凭据、`.env` 或配置。
- **会改写回复**：Desktop 上搜索过的那一轮，最终回复末尾会多一个 `::search-sources{…}` 段落，随会话保存，也会出现在之后模型看到的历史里；其他平台不会添加。
- **站点图标会联系第三方**：「全部」模式下依次尝试（取第一个成功的）——① **Google `s2/favicons` 与 DuckDuckGo `icons.duckduckgo.com` 会收到域名，因而知道你检索过哪些域名**；② 站点自己的 `/favicon.ico`。面板发出的图片请求不带 Referer，图标服务只会得知域名。「关闭」只显示首字母，不联系任何一方。
- **仅访问自己的后端命名空间**（心跳与记录读取），无遥测、无用量上报；卡片只在点击时通过 `ctx.os.openExternal` 打开链接。

已知限制见上文 *Known limits*：与市场里的 `source-tray` 功能有重叠；第三方工具输出按通用规则解析；`plugins.isolation: host` 下事件推送不可用，面板改走 REST。
