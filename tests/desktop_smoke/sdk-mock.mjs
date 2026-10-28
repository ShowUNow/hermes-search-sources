import { atom } from 'nanostores'
import { useStore } from '@nanostores/react'
import { jsx, jsxs } from 'react/jsx-runtime'
export { atom }
export const useValue = useStore
export const PANES_AREA = 'panes'
export const TRANSCRIPT_DIRECTIVE_AREA = 'transcript.directives'
export const profileColor = name => (name ? 'hsl(10 68% 58%)' : null)
export const profileColorSoft = (c, p = 16) => `color-mix(in srgb, ${c} ${p}%, transparent)`
export const calls = []
export const visible = atom(false)
export const host = {
  notify: m => calls.push(['notify', m]),
  undismissPane: id => calls.push(['undismiss', id]),
  revealPane: id => { calls.push(['reveal', id]); visible.set(true) },
  paneVisibility: () => visible
}

// --- plugin i18n, resolved like the app does: active locale -> the plugin's `en` bundle -> the key
export const locale = atom('en')
let bundles = {}
function translate(loc, key, args) {
  for (const code of [loc, 'en']) {
    const found = key.split('.').reduce((node, part) => node?.[part], bundles[code])
    if (typeof found === 'function') return found(...args)
    if (typeof found === 'string') return found
  }
  return key
}
export const bundlesOf = () => bundles
export const i18n = { register: b => { bundles = b; return () => {} }, t: (key, ...args) => translate(locale.get(), key, args) }
export function usePluginI18n() {
  const loc = useStore(locale)
  return (key, ...args) => translate(loc, key, args)
}

// --- settings primitives
export const SegmentedControl = ({ options, value, onChange }) =>
  jsx('div', { className: 'seg', children: options.map(o => jsx('button', { type: 'button', 'data-id': o.id, 'aria-pressed': o.id === value, onClick: () => onChange(o.id), children: o.label }, o.id)) })
export const ListRow = ({ title, description, action }) => jsxs('div', { className: 'row', children: [jsx('b', { children: title }), jsx('p', { children: description }), action] })
