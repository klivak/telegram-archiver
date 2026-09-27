export function formatBytes(n: number | null | undefined): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatDuration(totalSeconds: number): { h: number; m: number; s: number } {
  const t = Math.max(0, Math.round(totalSeconds))
  return { h: Math.floor(t / 3600), m: Math.floor((t % 3600) / 60), s: t % 60 }
}

export function secondsUntil(iso: string | null | undefined): number {
  if (!iso) return 0
  const ts = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return Math.max(0, Math.round((ts - Date.now()) / 1000))
}

export function shortDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
  if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
  return d.toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function senderName(m: { first_name?: string | null; last_name?: string | null; username?: string | null; sender_id?: number | null }): string {
  const n = [m.first_name, m.last_name].filter(Boolean).join(' ')
  return n || m.username || (m.sender_id ? String(m.sender_id) : '')
}

export function initials(title: string): string {
  return (title || '?')
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => [...w][0] ?? '')
    .join('')
    .toUpperCase()
}

/** Stable pleasant avatar color from an id. */
export function colorFor(id: number): string {
  const colors = ['#e17076', '#7bc862', '#65aadd', '#a695e7', '#ee7aae', '#6ec9cb', '#faa774']
  return colors[Math.abs(id) % colors.length]
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number): (...a: A) => void {
  let t: ReturnType<typeof setTimeout> | undefined
  return (...a: A) => {
    clearTimeout(t)
    t = setTimeout(() => fn(...a), ms)
  }
}

const nfCache = new Map<string, Intl.NumberFormat>()
function nf(locale: string, compact: boolean): Intl.NumberFormat {
  const key = `${locale}|${compact ? 'c' : 'f'}`
  let f = nfCache.get(key)
  if (!f) {
    f = new Intl.NumberFormat(locale === 'uk' ? 'uk-UA' : locale === 'en' ? 'en-US' : locale, compact ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: 0 })
    nfCache.set(key, f)
  }
  return f
}

/** Locale-aware integer formatting: "379 637" (uk) / "379,637" (en). */
export function formatNumber(n: number | null | undefined, locale: string): string {
  return nf(locale, false).format(n ?? 0)
}

/** Compact form for tight places: "380 тис." / "380K". Small numbers stay exact. */
export function formatCompact(n: number | null | undefined, locale: string): string {
  const v = n ?? 0
  return Math.abs(v) < 10000 ? formatNumber(v, locale) : nf(locale, true).format(v)
}
