// Usage: node smoke.mjs <plugin.js> [old]   — `old` runs against a desktop without plugin i18n / settings rows.
import { JSDOM } from 'jsdom'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const OLD = process.argv[3] === 'old'
const dom = new JSDOM('<!doctype html><html lang="en"><body><div id="root"></div></body></html>', { url: 'http://localhost/' })
globalThis.window = dom.window
globalThis.document = dom.window.document

const sdkFile = OLD ? './sdk-mock-old.mjs' : './sdk-mock.mjs'
fs.writeFileSync('plugin.mjs', fs.readFileSync(process.argv[2], 'utf8').replaceAll("'@hermes/plugin-sdk'", `'${sdkFile}'`))
const { default: plugin } = await import('./plugin.mjs')
const sdk = await import('./sdk-mock.mjs')
const React = await import('react')
const { createRoot } = await import('react-dom/client')
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { act } = React

function makeCtx(initialStorage = {}) {
  const ctx = {
    contributions: [], listeners: {}, disposers: [], rests: [], opened: [], store: { ...initialStorage }, timers: [], settingsPages: [],
    register(c) { ctx.contributions.push(c); return () => {} },
    registerMany(cs) { cs.forEach(c => ctx.contributions.push(c)); return () => {} },
    storage: { get: (k, f) => (k in ctx.store ? ctx.store[k] : f), set: (k, v) => { ctx.store[k] = JSON.parse(JSON.stringify(v)) }, remove: k => delete ctx.store[k] },
    rest: async (path, opts) => {
      ctx.rests.push([path, opts])
      if (path.startsWith('/record/')) { if (ctx.serve && ctx.serve[path]) return ctx.serve[path]; throw new Error('404 Not Found: Unknown record') }
      return { ok: true }
    },
    onEvent(type, fn) { (ctx.listeners[type] ||= []).push(fn); return () => {} },
    onDispose(fn) { ctx.disposers.push(fn) },
    setTimeout(fn, ms) { ctx.timers.push(['t', ms]); return () => {} },
    setInterval(fn, ms) { ctx.timers.push(['i', ms]); return () => {} },
    os: { openExternal: async url => { ctx.opened.push(url); return true } },
    emit(type, payload) { (ctx.listeners[type] || []).forEach(fn => fn({ type, payload })) },
    find: id => ctx.contributions.find(c => c.id === id)
  }
  if (!OLD) {
    ctx.i18n = sdk.i18n
    ctx.registerSettingsPage = page => { ctx.settingsPages.push(page); return () => {} }
  }
  return ctx
}

const ID = 'ss-0123456789ab'
const record = {
  v: 1, id: ID, session_id: 's', turn_id: 't', created: 1730000000, label: 'alpha guide', total: 5, cited: 2,
  sources: [
    { url: 'https://alpha.example/guide', title: 'Alpha Guide', snippet: 'All about alpha', domain: 'alpha.example', tools: ['web_search'], queries: ['alpha'], cited: true },
    { url: 'https://beta.example/a', title: 'Beta', snippet: '', domain: 'beta.example', tools: ['web_search', 'web_extract'], queries: [], cited: true },
    { url: 'https://gamma.example/g', title: 'Gamma', snippet: 'gamma text', domain: 'gamma.example', tools: ['mcp__grok__grok_search'], queries: [], cited: false },
    { url: 'javascript:alert(1)', title: 'evil', snippet: '', domain: 'x', tools: [], cited: false },
    { url: 'https://delta.example/d', title: 'Delta', snippet: '', domain: 'delta.example', tools: [], cited: false }
  ]
}

const root = createRoot(document.getElementById('root'))
const mount = async node => { await act(async () => { root.render(node) }) }
const settle = (ms = 10) => new Promise(r => setTimeout(r, ms))
const text = () => document.getElementById('root').textContent
const setLang = async (lang) => {
  if (OLD) { document.documentElement.lang = lang } else { await act(async () => { sdk.locale.set(lang) }) }
}

// 1. register -----------------------------------------------------------------------------------------
const ctx = makeCtx()
plugin.register(ctx)
const panes = ctx.contributions.filter(c => c.area === 'panes')
assert.equal(panes.length, 2)
assert.ok(panes.every(p => p.data.placement === 'right'))
assert.deepEqual(panes.map(p => p.title), ['Sources', 'Searches'], 'English pane titles by default')
const directive = ctx.find('directive')
assert.equal(directive.area, 'transcript.directives')
assert.equal(directive.data.name, 'search-sources')
assert.ok(document.head.querySelector('style[data-plugin="search-sources"]'))
await settle(0)
const ping = ctx.rests.find(r => r[0] === '/ping')
assert.ok(ping && ping[1].method === 'POST' && ping[1].body.version === '0.2.0' && ping[1].body.instance, 'heartbeat ping')
assert.ok(ctx.timers.some(t => t[0] === 'i'), 'heartbeat interval')
assert.ok(ctx.listeners['message.start'] && ctx.listeners['plugin.search-sources.turn.recorded'])
if (!OLD) {
  assert.equal(ctx.settingsPages.length, 1)
  // every language ships every string English has, as the same kind of value
  const flat = (node, prefix = '') => Object.entries(node).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, `${prefix}${k}.`) : [[`${prefix}${k}`, typeof v]]))
  const bundles = sdk.bundlesOf()
  const en = flat(bundles.en)
  for (const code of ['zh', 'zh-hant']) assert.deepEqual(flat(bundles[code]), en, `${code} bundle matches en`)
  assert.ok(en.length > 20)
}

// 2. live record event --------------------------------------------------------------------------------
ctx.emit('plugin.search-sources.turn.recorded', { record })
assert.equal(ctx.store.records.length, 1)
assert.equal(ctx.store.records[0].sources.length, 4, 'javascript: URL dropped on ingest')

// 3. capsule: English first ----------------------------------------------------------------------------
await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false }))
const btn = document.querySelector('.ss-capsule')
assert.ok(btn, 'capsule rendered')
assert.match(text(), /Searched 5 pages/)
assert.match(text(), /2 cited/)
assert.equal(btn.getAttribute('aria-label'), 'Searched 5 pages, 2 cited in the reply. Open the sources panel')
assert.equal(btn.getAttribute('aria-expanded'), 'false')
assert.ok(document.querySelectorAll('.ss-stack .ss-avatar').length >= 1, 'stacked site icons')
await mount(directive.data.render({ attrs: { id: ID, n: '1', c: '0' }, source: '', streaming: false }))
ctx.emit('plugin.search-sources.turn.recorded', { record: { ...record, id: 'ss-1111111111aa', total: 1, sources: [record.sources[0]] } })
await mount(directive.data.render({ attrs: { id: 'ss-1111111111aa', n: '1', c: '0' }, source: '', streaming: false }))
assert.match(text(), /Searched 1 page(?!s)/, 'singular')

// 3b. Chinese follows the app language; unknown languages read English
await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false }))
await setLang('zh')
if (OLD) await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false }))
assert.match(text(), /已搜索 5 个页面/)
assert.match(text(), /引用 2/)
await setLang('zh-hant')
if (OLD) { document.documentElement.lang = 'zh-TW'; await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false })) }
assert.match(text(), /已搜尋 5 個頁面/)
await setLang('ja')
if (OLD) await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false }))
assert.match(text(), /Searched 5 pages/, 'no Japanese bundle: English')
await setLang('en')
if (OLD) await mount(directive.data.render({ attrs: { id: ID, n: '5', c: '2' }, source: '', streaming: false }))

// 4. click -> state, undismiss, reveal (in that order), pane shows cited first ---------------------------
await act(async () => { document.querySelector('.ss-capsule').click() })
assert.deepEqual(sdk.calls.map(c => c.slice(0, 2).join(':')), ['undismiss:search-sources:sources', 'reveal:search-sources:sources'])
assert.equal(document.querySelector('.ss-capsule').getAttribute('aria-expanded'), 'true')
await mount(ctx.find('sources').render())
let cards = [...document.querySelectorAll('.ss-card')]
assert.deepEqual(cards.map(c => c.querySelector('.ss-card-title').textContent), ['Alpha Guide', 'Beta', 'Gamma', 'Delta'])
assert.deepEqual(cards.map(c => c.classList.contains('is-cited')), [true, true, false, false])
assert.match(cards[0].textContent, /Cited/)
assert.match(text(), /Cited in reply · 2/)
assert.match(text(), /Other searched pages · 2/)
assert.match(text(), /5 pages · 2 cited in reply/)
await setLang('zh')
if (OLD) await mount(ctx.find('sources').render())
assert.match(text(), /正文引用 · 2/)
assert.match(text(), /其他已检索 · 2/)
assert.match(text(), /共 5 个页面 · 正文引用 2 个/)
assert.match(cards[0].textContent, /已引用/)
await setLang('en')
if (OLD) await mount(ctx.find('sources').render())

// 5. card click opens the system browser ------------------------------------------------------------------
cards = [...document.querySelectorAll('.ss-card')]
await act(async () => { cards[2].click() })
assert.deepEqual(ctx.opened, ['https://gamma.example/g'])
ctx.os.openExternal = async () => false
await act(async () => { cards[2].click() })
await settle(0)
assert.equal(sdk.calls.at(-1)[0], 'notify')
assert.equal(sdk.calls.at(-1)[1].message, 'Could not open the link in your browser')

// 6. unknown id: capsule from attrs, on-demand fetch, honest pane state ----------------------------------
await mount(directive.data.render({ attrs: { id: 'ss-aaaaaaaaaaaa', n: '3', c: '0' }, source: '', streaming: false }))
await settle()
assert.match(text(), /Searched 3 pages/)
assert.ok(ctx.rests.some(r => r[0] === '/record/ss-aaaaaaaaaaaa'))
await act(async () => { document.querySelector('.ss-capsule').click() })
await mount(ctx.find('sources').render())
assert.match(text(), /not available on this device/)
ctx.serve = { '/record/ss-bbbbbbbbbbbb': { ...record, id: 'ss-bbbbbbbbbbbb', label: 'from rest' } }
await mount(directive.data.render({ attrs: { id: 'ss-bbbbbbbbbbbb', n: '5', c: '2' }, source: '', streaming: false }))
await settle()
await act(async () => { document.querySelector('.ss-capsule').click() })
await mount(ctx.find('sources').render())
assert.match(text(), /from rest/)

// 7. garbage attributes render nothing ----------------------------------------------------------------------
for (const attrs of [{}, { id: 'x' }, { id: '../etc/passwd', n: '9' }, { id: 'ss-ffffffffffff', n: '0' }]) {
  await mount(directive.data.render({ attrs, source: '', streaming: false }))
  assert.equal(document.querySelector('.ss-capsule'), null, JSON.stringify(attrs))
}

// 8. recent pane ----------------------------------------------------------------------------------------------
await mount(ctx.find('recent').render())
assert.ok(document.querySelectorAll('.ss-card').length >= 1)
assert.match(text(), /\d+ pages?( · \d+ cited)?/)
sdk.calls.length = 0
await act(async () => { document.querySelector('.ss-card').click() })
assert.equal(sdk.calls.at(-1)[1], 'search-sources:sources')

// 9. site icons: Google / DuckDuckGo / site /favicon.ico fallback chain; the setting is honoured ---------------
const avatarProbe = domain => ({ attrs: { id: ID, n: '5', c: '2' }, domain })
let siteCounter = 0
async function iconSrcs(mode) {
  const site = `site${++siteCounter}.example`
  const c = makeCtx({ iconMode: mode })
  plugin.register(c)
  c.emit('plugin.search-sources.turn.recorded', { record: { ...record, id: 'ss-2222222222bb', sources: [{ ...record.sources[0], domain: site, url: `https://${site}/p/deep?x=1` }] } })
  await mount(null)
  await mount(c.find('directive').data.render({ attrs: { id: 'ss-2222222222bb', n: '1', c: '0' }, source: '', streaming: false }))
  await settle(20)
  const img = () => document.querySelector('.ss-avatar-img')
  const seen = []
  for (let i = 0; i < 6 && img(); i++) {
    seen.push(img().getAttribute('src'))
    await act(async () => { img().dispatchEvent(new window.Event('error')) })
  }
  const letter = document.querySelector('.ss-avatar:not(.has-img)') ? document.querySelector('.ss-avatar').textContent : null
  c.disposers.forEach(fn => fn())
  return { seen, letter, site }
}
let out = await iconSrcs('all')
assert.equal(out.seen.length, 3, 'all 3 icon tiers tried')
assert.ok(out.seen[0].includes('google.com/s2/favicons') && out.seen[1].includes('duckduckgo.com') && out.seen[2] === `https://${out.site}/favicon.ico`, 'fallback chain: ' + out.seen)
assert.equal(out.letter, 'S', 'every tier failed: letter badge')
out = await iconSrcs('off')
assert.deepEqual(out.seen, [], 'off: no icon requests')
assert.equal(out.letter, 'S', 'off: letters')
out = await iconSrcs('app')
assert.ok(out.seen[0].includes('google.com/s2/favicons'), 'legacy stored app mode falls back cleanly to all')
// a host that is not a plain DNS name / IPv4 address never reaches an icon URL
{
  const c = makeCtx({ iconMode: 'all' })
  plugin.register(c)
  const mk = (url, domain) => ({ ...record.sources[0], url, domain, title: domain || 'untitled' })
  c.emit('plugin.search-sources.turn.recorded', { record: { ...record, id: 'ss-3333333333cc', total: 3, sources: [
    mk('http://[::1]/p', 'bad host'), mk('https://good.example/p', 'a.example"onerror="x'), mk('https://x.example/p', 'evil.example/../x?y=1') ] } })
  await mount(c.find('directive').data.render({ attrs: { id: 'ss-3333333333cc', n: '3', c: '0' }, source: '', streaming: false }))
  await act(async () => { document.querySelector('.ss-capsule').click() })
  await mount(c.find('sources').render())
  await settle(20)
  const imgs = [...document.querySelectorAll('.ss-avatar-img')].map(i => i.getAttribute('src'))
  assert.ok(!/onerror|\.\.\/x|\[::1\]/.test(imgs.join(' ')), 'no unsafe host in any icon URL: ' + imgs)
  assert.ok(imgs.some(u => u.includes('good.example')) && imgs.some(u => u.includes('x.example')), 'hosts come from the page URL: ' + imgs)
  c.disposers.forEach(fn => fn())
}
await mount(null)

// 10. settings page changes the mode and persists it ---------------------------------------------------------
if (!OLD) {
  const c = makeCtx()
  plugin.register(c)
  const page = c.settingsPages[0]
  assert.equal(page.title, 'Search Sources')
  await mount(page.render())
  assert.match(text(), /Site icons/)
  assert.equal(document.querySelector('[data-id="all"]').getAttribute('aria-pressed'), 'true')
  await act(async () => { document.querySelector('[data-id="off"]').click() })
  assert.equal(c.store.iconMode, 'off')
  assert.equal(document.querySelector('[data-id="off"]').getAttribute('aria-pressed'), 'true')
  await act(async () => { document.querySelector('[data-id="all"]').click() })
  assert.equal(c.store.iconMode, 'all')
  c.disposers.forEach(fn => fn())
} else {
  const c = makeCtx({ iconMode: 'nonsense' })
  plugin.register(c)
  assert.equal(c.settingsPages.length, 0)
  c.disposers.forEach(fn => fn())
}

// 11. persistence survives a reload; dispose retires style and heartbeat ------------------------------------
const ctx2 = makeCtx({ records: ctx.store.records })
ctx.disposers.forEach(fn => fn())
await settle(0)
assert.ok(!document.head.querySelector('style[data-plugin="search-sources"]') || document.head.querySelectorAll('style[data-plugin="search-sources"]').length === 0)
assert.ok(ctx.rests.some(r => r[0] === '/bye' && r[1].body.instance === ping[1].body.instance), 'bye carries the instance')
plugin.register(ctx2)
await mount(ctx2.find('sources').render())
assert.ok(document.querySelectorAll('.ss-card').length >= 3, 'restored from storage')
console.log(`JS SMOKE OK${OLD ? ' (older desktop)' : ''}`)
process.exit(0)
