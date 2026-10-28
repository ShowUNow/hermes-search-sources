// search-sources — desktop half. Plain ESM, no build: only @hermes/plugin-sdk, react and react/jsx-runtime.
//
// The agent half appends `::search-sources{id="ss-…" n="7" c="3"}` to a Desktop reply that used web search /
// fetch tools and pushes the full page list as a `plugin.search-sources.turn.recorded` event. This half draws
// the "Searched N pages" capsule for that directive and the Sources pane it opens. English first; Chinese
// (simplified / traditional) follows the app language through the SDK's plugin i18n.
import {
  atom,
  host,
  PANES_AREA,
  profileColor,
  profileColorSoft,
  TRANSCRIPT_DIRECTIVE_AREA,
  useValue
} from '@hermes/plugin-sdk'
import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'search-sources'
const VERSION = '0.2.0'
const PANE_SOURCES = `${ID}:sources` // `<pluginId>:<paneId>`, what undismissPane / revealPane take
const MAX_KEPT = 40
const MAX_STORED_CHARS = 900000
const PING_EVERY_MS = 4 * 60 * 1000
const PING_MIN_GAP_MS = 20 * 1000
const PING_RETRY_MS = [3000, 10000, 30000]
const REST_TIMEOUT_MS = 10000
const RECORD_ID = /^ss-[0-9a-f]{12}$/
const ICON_MODES = ['all', 'app', 'off']

// `$records`: id -> record. `$order`: ids, newest first. `$focus`: which turn's sources the pane shows.
// `$loading`: id -> 'loading' | 'missing' | 'error' for records fetched on demand.
const $records = atom({})
const $order = atom([])
const $focus = atom({ id: null, at: 0 })
const $loading = atom({})
const $never = atom(false)
const $iconMode = atom('all') // 'all' | 'app' | 'off' — see README › Disclosure

const runtime = { ctx: null }

// --- copy ----------------------------------------------------------------------------------------------
//
// English is the source of truth: the SDK resolves active locale -> this plugin's `en` bundle -> the key, so a
// language without a bundle here reads English. Values may be functions (interpolation, plural).

const pages = n => (n === 1 ? 'page' : 'pages')

const MESSAGES = {
  en: {
    panes: { sources: 'Sources', recent: 'Searches' },
    capsule: {
      searched: n => `Searched ${n} ${pages(n)}`,
      cited: n => `${n} cited`,
      aria: (n, m) => `Searched ${n} ${pages(n)}${m > 0 ? `, ${m} cited in the reply` : ''}. Open the sources panel`
    },
    panel: {
      fallbackLabel: 'Web search',
      meta: (total, cited) => `${total} ${pages(total)}${cited > 0 ? ` · ${cited} cited in reply` : ''}`,
      cited: 'Cited in reply',
      other: 'Other searched pages',
      all: 'Searched pages',
      badge: 'Cited',
      loading: 'Loading the sources of this search…',
      error: 'Could not load the sources.',
      retry: 'Retry',
      unavailable: 'The sources of this search are not available on this device (they may have been created elsewhere or cleaned up).',
      empty: 'No searches yet. When a reply uses web search or page fetching, every page it looked at is listed here.'
    },
    recent: {
      empty: 'No searches yet.',
      summary: (total, cited) => `${total} ${pages(total)}${cited > 0 ? ` · ${cited} cited` : ''}`
    },
    openFailed: 'Could not open the link in your browser',
    settings: {
      title: 'Search Sources',
      icons: {
        title: 'Site icons',
        description:
          'Show each page’s own icon next to its domain. “All” asks the app to read the site’s icon and, when that finds none, Google’s and DuckDuckGo’s icon services (they learn the domain) and then the site itself. “App only” skips those services; “Off” shows letters and contacts no one.',
        all: 'All',
        app: 'App only',
        off: 'Off'
      }
    }
  },
  zh: {
    panes: { sources: '来源', recent: '搜索记录' },
    capsule: {
      searched: n => `已搜索 ${n} 个页面`,
      cited: n => `引用 ${n}`,
      aria: (n, m) => `已搜索 ${n} 个页面${m > 0 ? `，正文引用 ${m} 个` : ''}，打开来源面板`
    },
    panel: {
      fallbackLabel: '联网检索',
      meta: (total, cited) => `共 ${total} 个页面${cited > 0 ? ` · 正文引用 ${cited} 个` : ''}`,
      cited: '正文引用',
      other: '其他已检索',
      all: '已检索',
      badge: '已引用',
      loading: '正在加载这轮搜索的来源…',
      error: '加载来源失败。',
      retry: '重试',
      unavailable: '这轮搜索的来源详情在此设备上不可用（可能由其他设备生成，或已被清理）。',
      empty: '还没有搜索记录。回复用到联网搜索或网页抓取时，这里会列出该轮检索到的所有页面。'
    },
    recent: {
      empty: '还没有搜索记录。',
      summary: (total, cited) => `${total} 个页面${cited > 0 ? ` · 引用 ${cited}` : ''}`
    },
    openFailed: '无法用系统浏览器打开该链接',
    settings: {
      title: '搜索来源',
      icons: {
        title: '站点图标',
        description:
          '在域名旁显示页面自己的图标。“全部”先让应用读取站点图标，读不到时再依次尝试 Google、DuckDuckGo 的图标服务（它们会得知该域名）和站点自身；“仅应用”不使用这些服务；“关闭”只显示首字母，不联系任何一方。',
        all: '全部',
        app: '仅应用',
        off: '关闭'
      }
    }
  },
  'zh-hant': {
    panes: { sources: '來源', recent: '搜尋記錄' },
    capsule: {
      searched: n => `已搜尋 ${n} 個頁面`,
      cited: n => `引用 ${n}`,
      aria: (n, m) => `已搜尋 ${n} 個頁面${m > 0 ? `，正文引用 ${m} 個` : ''}，開啟來源面板`
    },
    panel: {
      fallbackLabel: '聯網檢索',
      meta: (total, cited) => `共 ${total} 個頁面${cited > 0 ? ` · 正文引用 ${cited} 個` : ''}`,
      cited: '正文引用',
      other: '其他已檢索',
      all: '已檢索',
      badge: '已引用',
      loading: '正在載入這輪搜尋的來源…',
      error: '載入來源失敗。',
      retry: '重試',
      unavailable: '這輪搜尋的來源詳情在此裝置上不可用（可能由其他裝置產生，或已被清理）。',
      empty: '還沒有搜尋記錄。回覆用到聯網搜尋或網頁擷取時，這裡會列出該輪檢索到的所有頁面。'
    },
    recent: {
      empty: '還沒有搜尋記錄。',
      summary: (total, cited) => `${total} 個頁面${cited > 0 ? ` · 引用 ${cited}` : ''}`
    },
    openFailed: '無法用系統瀏覽器開啟該連結',
    settings: {
      title: '搜尋來源',
      icons: {
        title: '站點圖示',
        description:
          '在網域旁顯示頁面自己的圖示。「全部」先讓應用讀取站點圖示，讀不到時再依序嘗試 Google、DuckDuckGo 的圖示服務（它們會得知該網域）與站點本身；「僅應用」不使用這些服務；「關閉」只顯示首字母，不聯絡任何一方。',
        all: '全部',
        app: '僅應用',
        off: '關閉'
      }
    }
  }
}

// Older desktops lack `usePluginI18n` / `ctx.i18n`; this resolves the same bundles from the document language.
function guessLocale() {
  const lang = String(document.documentElement?.lang || navigator.language || 'en').toLowerCase()

  return lang.startsWith('zh') ? (/hant|-tw|-hk|-mo/.test(lang) ? 'zh-hant' : 'zh') : 'en'
}

function lookup(locale, key, args) {
  for (const code of [locale, 'en']) {
    const found = key.split('.').reduce((node, part) => node?.[part], MESSAGES[code])

    if (typeof found === 'function') {
      return found(...args)
    }

    if (typeof found === 'string') {
      return found
    }
  }

  return key
}

const fallbackT = (key, ...args) => lookup(guessLocale(), key, args)
const tNow = (key, ...args) => (runtime.ctx?.i18n?.t ? runtime.ctx.i18n.t(key, ...args) : fallbackT(key, ...args))
const usePluginT = typeof sdk.usePluginI18n === 'function' ? sdk.usePluginI18n : null

// `usePluginT` is fixed for the life of the module, so the hook order never changes between renders.
const useT = () => (usePluginT ? usePluginT(ID) : fallbackT)

const str = (value, cap) => (typeof value === 'string' ? value.trim().slice(0, cap) : '')
const strings = (value, cap) => (Array.isArray(value) ? value.map(v => str(v, 80)).filter(Boolean).slice(0, cap) : [])
const count = value => Math.max(0, Math.min(999, Number.parseInt(value, 10) || 0))

function safeUrl(value) {
  try {
    const url = new URL(String(value))

    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : ''
  } catch {
    return ''
  }
}

// Records arrive from the backend, from storage and (via the directive) from model text: re-derive every
// field and every count instead of trusting any of them.
function normalizeRecord(raw) {
  if (!raw || typeof raw !== 'object' || !RECORD_ID.test(raw.id) || !Array.isArray(raw.sources)) {
    return null
  }

  const sources = raw.sources
    .slice(0, 80)
    .map(s => ({
      url: safeUrl(s?.url),
      title: str(s?.title, 200),
      snippet: str(s?.snippet, 300),
      domain: str(s?.domain, 100),
      tools: strings(s?.tools, 4),
      cited: s?.cited === true
    }))
    .filter(s => s.url)

  if (sources.length === 0) {
    return null
  }

  return {
    id: raw.id,
    label: str(raw.label, 120),
    created: Number(raw.created) || 0,
    total: Math.max(sources.length, count(raw.total)),
    cited: sources.filter(s => s.cited).length,
    sources
  }
}

function persist() {
  const records = $records.get()
  let kept = $order.get().slice(0, MAX_KEPT).map(id => records[id])

  try {
    while (kept.length > 0 && JSON.stringify(kept).length > MAX_STORED_CHARS) {
      kept = kept.slice(0, -1)
    }

    runtime.ctx?.storage.set('records', kept)
  } catch {
    // Storage is a cache; a full or blocked one only costs a re-fetch.
  }
}

function put(raw) {
  const record = normalizeRecord(raw)

  if (!record) {
    return false
  }

  const order = [record.id, ...$order.get().filter(id => id !== record.id)]
  const records = { ...$records.get(), [record.id]: record }

  for (const id of order.splice(MAX_KEPT)) {
    delete records[id]
  }

  $records.set(records)
  $order.set(order)

  return true
}

function remember(raw) {
  const ok = put(raw)

  if (ok) {
    persist()
  }

  return ok
}

function setLoading(id, state) {
  const next = { ...$loading.get() }

  if (state) {
    next[id] = state
  } else {
    delete next[id]
  }

  $loading.set(next)
}

async function loadRecord(id) {
  const state = $loading.get()[id]

  if (!RECORD_ID.test(id) || state === 'loading' || state === 'missing' || !runtime.ctx) {
    return
  }

  setLoading(id, 'loading')

  try {
    setLoading(id, remember(await runtime.ctx.rest(`/record/${id}`, { timeoutMs: REST_TIMEOUT_MS })) ? null : 'missing')
  } catch (error) {
    setLoading(id, /\b404\b|not found|unknown record/i.test(String(error?.message ?? error)) ? 'missing' : 'error')
  }
}

function openSources(id) {
  $focus.set({ id, at: Date.now() })

  if ($records.get()[id] === undefined) {
    if ($loading.get()[id] === 'error') {
      setLoading(id, null)
    }

    void loadRecord(id)
  }

  // The pane may have been closed (dismissed) since the last click; undismiss before fronting it.
  host.undismissPane?.(PANE_SOURCES)
  host.revealPane?.(PANE_SOURCES)
}

function openExternal(url) {
  const open = runtime.ctx?.os.openExternal(url)

  void Promise.resolve(open).then(ok => {
    if (ok === false) {
      host.notify({ kind: 'error', message: tNow('openFailed') })
    }
  })
}

const paneVisible = () => (typeof host.paneVisibility === 'function' ? host.paneVisibility(PANE_SOURCES) : $never)

// --- UI -----------------------------------------------------------------------------------------------

const faviconCache = new Map() // host -> data URL from the app's resolver, or '' when it found none
const faviconPending = new Map()

// A bare DNS name or IPv4 address: the only thing allowed into an icon-service or /favicon.ico URL.
const HOST_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/

// The host to look an icon up for: the one the page URL really has (the URL parser punycodes it), else the
// record's `domain`; empty — letter badge, no request — unless it is a plain host name.
function iconHost(domain, url) {
  let host = ''

  try {
    host = new URL(url).hostname
  } catch {
    host = String(domain || '')
  }

  host = host.trim().toLowerCase().replace(/^www\./, '')

  return host.length <= 253 && HOST_RE.test(host) ? host : ''
}

const appResolver = () => (typeof window !== 'undefined' ? window.hermesDesktop?.resolveFavicon : undefined)

function siteOrigin(url, hostName) {
  try {
    return `${new URL(url).origin}/`
  } catch {
    return `https://${hostName}/`
  }
}

// Third-party tiers, only reached when the app's own resolver found nothing (or does not exist) and the user
// left Site icons on "All". The domain is the only thing they are told.
function thirdPartyIcons(hostName) {
  const enc = encodeURIComponent(hostName)

  return [
    `https://www.google.com/s2/favicons?domain=${enc}&sz=64`,
    `https://icons.duckduckgo.com/ip3/${enc}.ico`,
    `https://${hostName}/favicon.ico`
  ]
}

function resolveSiteIcon(hostName, url) {
  const resolve = appResolver()

  if (typeof resolve !== 'function') {
    return Promise.resolve('')
  }

  if (!faviconPending.has(hostName)) {
    faviconPending.set(
      hostName,
      Promise.resolve()
        .then(() => resolve(siteOrigin(url, hostName)))
        .then(found => (typeof found === 'string' ? found.trim() : ''))
        .catch(() => '')
        .then(found => {
          faviconCache.set(hostName, found)
          faviconPending.delete(hostName)

          return found
        })
    )
  }

  return faviconPending.get(hostName)
}

// Letter badge first, then the best real icon that settings allow: the app's resolver (reads the site's own
// markup and well-known paths), then — "All" only — Google / DuckDuckGo icon services and the site's
// /favicon.ico; a failed image steps to the next tier, and the letter stays if every tier fails.
function Avatar({ domain, url }) {
  const mode = useValue($iconMode)
  const hostName = iconHost(domain, url)
  const asks = mode !== 'off' && hostName && typeof appResolver() === 'function'
  const [resolved, setResolved] = useState(() => (asks ? faviconCache.get(hostName) : ''))
  const [failedStep, setFailedStep] = useState(0)

  useEffect(() => {
    setFailedStep(0)

    if (!asks) {
      setResolved('')

      return
    }

    const cached = faviconCache.get(hostName)

    if (cached !== undefined) {
      setResolved(cached)

      return
    }

    let live = true

    setResolved(undefined)
    void resolveSiteIcon(hostName, url).then(found => {
      if (live) {
        setResolved(found)
      }
    })

    return () => {
      live = false
    }
  }, [asks, hostName, mode, url])

  const waiting = asks && resolved === undefined
  const chain =
    mode === 'off' || !hostName || waiting
      ? []
      : [resolved || '', ...(mode === 'all' ? thirdPartyIcons(hostName) : [])].filter(Boolean)
  const activeSrc = chain[failedStep] || ''

  if (activeSrc) {
    return jsx('span', {
      className: 'ss-avatar has-img',
      'aria-hidden': true,
      children: jsx('img', {
        alt: '',
        className: 'ss-avatar-img',
        src: activeSrc,
        loading: 'lazy',
        referrerPolicy: 'no-referrer',
        onError: () => setFailedStep(step => step + 1)
      })
    })
  }

  const color = profileColor(domain)
  const style = color ? { color, background: profileColorSoft(color, 22) } : undefined

  return jsx('span', {
    className: 'ss-avatar',
    'aria-hidden': true,
    style,
    children: (domain || '?').slice(0, 1).toUpperCase()
  })
}

function Capsule({ attrs }) {
  const t = useT()
  const id = attrs.id
  const valid = RECORD_ID.test(id ?? '')
  const record = useValue($records)[id]
  const focus = useValue($focus)
  const visible = useValue(paneVisible())

  useEffect(() => {
    if (valid && !record) {
      void loadRecord(id)
    }
  }, [valid, record, id])

  const total = record ? record.total : count(attrs.n)
  const cited = record ? record.cited : count(attrs.c)

  if (!valid || total < 1) {
    return null
  }

  const sites = []
  const seenDomains = new Set()

  if (record) {
    for (const s of record.sources) {
      if (s.domain && !seenDomains.has(s.domain)) {
        seenDomains.add(s.domain)
        sites.push({ domain: s.domain, url: s.url })
        if (sites.length >= 3) {
          break
        }
      }
    }
  }

  const open = visible && focus.id === id

  return jsx('div', {
    className: 'ss-capsule-row',
    children: jsxs('button', {
      type: 'button',
      className: 'ss-capsule',
      'aria-expanded': open,
      'aria-label': t('capsule.aria', total, cited),
      onClick: () => openSources(id),
      children: [
        sites.length > 0
          ? jsx('span', {
              className: 'ss-stack',
              children: sites.map(site => jsx(Avatar, { domain: site.domain, url: site.url }, site.domain))
            })
          : null,
        jsx('span', { className: 'ss-capsule-label', children: t('capsule.searched', total) }),
        cited > 0 ? jsx('span', { className: 'ss-capsule-cited', children: t('capsule.cited', cited) }) : null
      ]
    })
  })
}

function Card({ source }) {
  const t = useT()

  return jsxs('button', {
    type: 'button',
    className: source.cited ? 'ss-card is-cited' : 'ss-card',
    title: source.url,
    onClick: () => openExternal(source.url),
    children: [
      jsxs('span', {
        className: 'ss-card-head',
        children: [
          jsx(Avatar, { domain: source.domain, url: source.url }),
          jsx('span', { className: 'ss-card-domain', children: source.domain }),
          source.cited ? jsx('span', { className: 'ss-badge', children: t('panel.badge') }) : null
        ]
      }),
      jsx('span', { className: 'ss-card-title', children: source.title }),
      source.snippet ? jsx('span', { className: 'ss-card-snippet', children: source.snippet }) : null,
      source.tools.length > 0 ? jsx('span', { className: 'ss-card-tools', children: source.tools.join(' · ') }) : null
    ]
  })
}

const Section = ({ title, sources }) =>
  sources.length === 0
    ? null
    : jsxs('section', {
        className: 'ss-section',
        children: [
          jsx('h3', { className: 'ss-section-title', children: `${title} · ${sources.length}` }),
          jsx('div', { className: 'ss-cards', children: sources.map(s => jsx(Card, { source: s }, s.url)) })
        ]
      })

const Empty = ({ children }) => jsx('div', { className: 'ss-empty', children })

function SourcesPane() {
  const t = useT()
  const focus = useValue($focus)
  const records = useValue($records)
  const order = useValue($order)
  const loading = useValue($loading)
  const scroller = useRef(null)
  const id = focus.id ?? order[0] ?? null
  const record = id ? records[id] : null

  useEffect(() => {
    scroller.current?.scrollTo?.({ top: 0 })
  }, [id, focus.at])

  let body

  if (record) {
    body = [
      jsxs(
        'header',
        {
          className: 'ss-header',
          children: [
            jsx('div', { className: 'ss-header-label', title: record.label, children: record.label || t('panel.fallbackLabel') }),
            jsx('div', {
              className: 'ss-header-meta',
              children: t('panel.meta', record.total, record.cited)
            })
          ]
        },
        'head'
      ),
      jsx(Section, { title: t('panel.cited'), sources: record.sources.filter(s => s.cited) }, 'cited'),
      jsx(Section, { title: record.cited > 0 ? t('panel.other') : t('panel.all'), sources: record.sources.filter(s => !s.cited) }, 'rest')
    ]
  } else if (id && loading[id] === 'loading') {
    body = jsx(Empty, { children: t('panel.loading') })
  } else if (id && loading[id] === 'error') {
    body = jsxs(Empty, {
      children: [
        t('panel.error'),
        jsx('button', { type: 'button', className: 'ss-link', onClick: () => openSources(id), children: t('panel.retry') })
      ]
    })
  } else if (id) {
    body = jsx(Empty, { children: t('panel.unavailable') })
  } else {
    body = jsx(Empty, { children: t('panel.empty') })
  }

  return jsx('div', { className: 'ss-pane', ref: scroller, children: body })
}

function RecentPane() {
  const t = useT()
  const order = useValue($order)
  const records = useValue($records)
  const focus = useValue($focus)

  if (order.length === 0) {
    return jsx('div', { className: 'ss-pane', children: jsx(Empty, { children: t('recent.empty') }) })
  }

  return jsx('div', {
    className: 'ss-pane',
    children: jsx('div', {
      className: 'ss-cards',
      children: order.map(id => {
        const r = records[id]

        return r
          ? jsxs(
              'button',
              {
                type: 'button',
                className: focus.id === id ? 'ss-card is-focus' : 'ss-card',
                onClick: () => openSources(id),
                children: [
                  jsx('span', { className: 'ss-card-title', children: r.label || t('panel.fallbackLabel') }),
                  jsx('span', {
                    className: 'ss-card-tools',
                    children: `${t('recent.summary', r.total, r.cited)}${
                      r.created ? ` · ${new Date(r.created * 1000).toLocaleString()}` : ''
                    }`
                  })
                ]
              },
              id
            )
          : null
      })
    })
  })
}

// Theme variables only (`--ui-*`): light and dark follow the app.
const CSS = `
.ss-capsule-row{margin:8px 0 2px}
.ss-capsule{display:inline-flex;align-items:center;gap:8px;padding:4px 12px 4px 6px;border:1px solid var(--ui-stroke-secondary);border-radius:999px;background:transparent;color:var(--ui-text-secondary);font:inherit;font-size:12.5px;line-height:1.3;cursor:pointer;transition:background .12s,color .12s,border-color .12s}
.ss-capsule:hover{background:var(--ui-control-hover-background);color:var(--ui-text-primary)}
.ss-capsule[aria-expanded="true"]{background:var(--ui-control-active-background);color:var(--ui-text-primary);border-color:var(--ui-stroke-primary)}
.ss-capsule:focus-visible,.ss-card:focus-visible,.ss-link:focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
.ss-capsule-cited{padding:0 7px;border-radius:999px;font-size:11px;color:var(--ui-accent);background:color-mix(in srgb,var(--ui-accent) 14%,transparent)}
.ss-stack{display:inline-flex}
.ss-stack .ss-avatar{margin-left:-6px;box-shadow:0 0 0 2px var(--ui-bg-elevated)}
.ss-stack .ss-avatar:first-child{margin-left:0}
.ss-avatar{flex:none;display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:10px;font-weight:600;line-height:1;color:var(--ui-text-secondary);background:var(--ui-bg-tertiary);overflow:hidden}
.ss-avatar.has-img{background:var(--ui-bg-elevated);box-shadow:inset 0 0 0 1px var(--ui-stroke-tertiary)}
.ss-stack .ss-avatar.has-img{box-shadow:0 0 0 2px var(--ui-bg-elevated),inset 0 0 0 1px var(--ui-stroke-tertiary)}
.ss-avatar-img{width:14px;height:14px;object-fit:contain;border-radius:2px;display:block}
.ss-pane{height:100%;min-height:0;overflow:auto;padding:10px 12px 16px;color:var(--ui-text-primary);font-size:13px;line-height:1.45}
.ss-header{padding:2px 2px 10px}
.ss-header-label{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ss-header-meta{margin-top:2px;font-size:11.5px;color:var(--ui-text-tertiary)}
.ss-section{margin-top:10px}
.ss-section-title{margin:0 2px 6px;font-size:11px;font-weight:600;letter-spacing:.04em;color:var(--ui-text-tertiary)}
.ss-cards{display:flex;flex-direction:column;gap:6px}
.ss-card{display:flex;flex-direction:column;gap:3px;width:100%;padding:8px 10px;border:1px solid var(--ui-stroke-tertiary);border-left-width:2px;border-radius:8px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer;transition:background .12s,border-color .12s}
.ss-card:hover{background:var(--ui-row-hover-background);border-color:var(--ui-stroke-secondary)}
.ss-card.is-cited{border-left-color:var(--ui-accent);background:color-mix(in srgb,var(--ui-accent) 6%,transparent)}
.ss-card.is-cited:hover{background:color-mix(in srgb,var(--ui-accent) 12%,transparent)}
.ss-card.is-focus{background:var(--ui-row-active-background)}
.ss-card-head{display:flex;align-items:center;gap:6px;min-width:0}
.ss-card-domain{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;color:var(--ui-text-tertiary)}
.ss-badge{flex:none;padding:0 6px;border-radius:999px;font-size:10.5px;color:var(--ui-accent);background:color-mix(in srgb,var(--ui-accent) 14%,transparent)}
.ss-card-title{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-weight:500;overflow-wrap:anywhere}
.ss-card-snippet{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-size:12px;color:var(--ui-text-secondary);overflow-wrap:anywhere}
.ss-card-tools{font-size:11px;color:var(--ui-text-quaternary)}
.ss-empty{padding:18px 6px;font-size:12.5px;color:var(--ui-text-tertiary)}
.ss-select{margin-top:8px;padding:3px 6px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:var(--ui-bg-tertiary);color:var(--ui-text-primary);font:inherit}
.ss-link{margin-left:6px;padding:0;border:0;background:transparent;color:var(--ui-accent);font:inherit;cursor:pointer;text-decoration:underline}
`

function setIconMode(mode) {
  if (!ICON_MODES.includes(mode)) {
    return
  }

  $iconMode.set(mode)

  try {
    runtime.ctx?.storage.set('iconMode', mode)
  } catch {
    // The choice still holds for this session.
  }
}

function SettingsPage() {
  const t = useT()
  const mode = useValue($iconMode)
  const options = ICON_MODES.map(id => ({ id, label: t(`settings.icons.${id}`) }))

  // The app's own settings rows when this desktop exports them; plain controls on older builds.
  const control = sdk.SegmentedControl
    ? jsx(sdk.SegmentedControl, { options, value: mode, onChange: setIconMode })
    : jsx('select', {
        className: 'ss-select',
        value: mode,
        'aria-label': t('settings.icons.title'),
        onChange: event => setIconMode(event.target.value),
        children: options.map(o => jsx('option', { value: o.id, children: o.label }, o.id))
      })

  return sdk.ListRow
    ? jsx(sdk.ListRow, {
        title: t('settings.icons.title'),
        description: t('settings.icons.description'),
        action: control
      })
    : jsxs('div', {
        className: 'ss-pane',
        children: [
          jsx('div', { className: 'ss-header-label', children: t('settings.icons.title') }),
          jsx('div', { className: 'ss-header-meta', children: t('settings.icons.description') }),
          control
        ]
      })
}

function installStyle(ctx) {
  const el = document.createElement('style')

  el.setAttribute('data-plugin', ID)
  el.textContent = CSS
  document.head.appendChild(el)
  ctx.onDispose(() => el.remove())
}

// The agent half only appends the directive while a client that can draw it keeps checking in, so a window
// without this plugin never sees the raw `::search-sources{…}` text. Each load names itself, so a hot-reload's
// late `bye` cannot retire its successor.
function startHeartbeat(ctx) {
  const instance = Math.random().toString(36).slice(2, 12)
  let last = 0
  let alive = true

  const ping = (attempt = 0) => {
    const now = Date.now()

    if (!alive || (attempt === 0 && now - last < PING_MIN_GAP_MS)) {
      return
    }

    last = now
    Promise.resolve().then(() => ctx.rest('/ping', { method: 'POST', body: { version: VERSION, instance }, timeoutMs: REST_TIMEOUT_MS })).catch(() => {
      if (attempt < PING_RETRY_MS.length) {
        ctx.setTimeout(() => ping(attempt + 1), PING_RETRY_MS[attempt])
      }
    })
  }

  ping()
  ctx.setInterval(ping, PING_EVERY_MS)
  ctx.onEvent('message.start', () => ping())
  ctx.onEvent('gateway.ready', () => ping())
  ctx.onDispose(() => {
    alive = false
    void Promise.resolve().then(() => ctx.rest('/bye', { method: 'POST', body: { instance }, timeoutMs: REST_TIMEOUT_MS })).catch(() => {})
  })
}

export default {
  id: ID,
  name: 'Search Sources',
  register(ctx) {
    runtime.ctx = ctx
    ctx.onDispose(() => {
      if (runtime.ctx === ctx) {
        runtime.ctx = null
      }
    })

    ctx.i18n?.register?.(MESSAGES)

    const storedMode = ctx.storage.get('iconMode', 'all')

    $iconMode.set(ICON_MODES.includes(storedMode) ? storedMode : 'all')

    const saved = ctx.storage.get('records', [])

    for (const raw of Array.isArray(saved) ? [...saved].reverse() : []) {
      put(raw)
    }

    installStyle(ctx)
    ctx.onEvent(`plugin.${ID}.turn.recorded`, event => void remember(event?.payload?.record))

    // Two panes on purpose: Desktop disables a plugin whose only pane is closed, which would turn every
    // capsule in the transcript back into raw directive text. With two, Close just dismisses that pane.
    ctx.registerMany([
      {
        id: 'sources',
        area: PANES_AREA,
        title: tNow('panes.sources'),
        data: { placement: 'right', width: '340px' },
        render: () => jsx(SourcesPane, {})
      },
      {
        id: 'recent',
        area: PANES_AREA,
        title: tNow('panes.recent'),
        data: { placement: 'right', width: '340px' },
        render: () => jsx(RecentPane, {})
      },
      {
        id: 'directive',
        area: TRANSCRIPT_DIRECTIVE_AREA,
        data: { name: ID, render: ({ attrs }) => jsx(Capsule, { attrs }) }
      }
    ])

    ctx.registerSettingsPage?.({
      id: 'settings',
      title: tNow('settings.title'),
      icon: 'search',
      render: () => jsx(SettingsPage, {})
    })

    startHeartbeat(ctx)
  }
}
