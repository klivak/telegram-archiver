/** REST + WebSocket client for the local backend (token-protected, 127.0.0.1 only). */

const TOKEN_KEY = 'tga.token'

function readToken(): string {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''))
  const fromHash = hash.get('token')
  if (fromHash) {
    sessionStorage.setItem(TOKEN_KEY, fromHash)
    history.replaceState(null, '', location.pathname + location.search)
    return fromHash
  }
  return window.__TGA_TOKEN__ || sessionStorage.getItem(TOKEN_KEY) || ''
}

export const token = readToken()

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  /** Stable error code from the backend (e.g. auth errors) if present. */
  get code(): string | undefined {
    const d = this.detail as { code?: string } | undefined
    return d && typeof d === 'object' ? d.code : undefined
  }
}

async function request<T>(method: string, path: string, body?: unknown, query?: Record<string, unknown>): Promise<T> {
  const url = new URL('/api' + path, location.origin)
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  const res = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string, query?: Record<string, unknown>) => request<T>('GET', path, undefined, query),
  post: <T>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  /** URL usable in <img>/<audio>/<a> (token in query, since tags cannot send headers). */
  fileUrl: (path: string, query: Record<string, string | number> = {}) => {
    const u = new URL('/api' + path, location.origin)
    for (const [k, v] of Object.entries(query)) u.searchParams.set(k, String(v))
    u.searchParams.set('token', token)
    return u.toString()
  },
}

export interface WsEvent {
  type: string
  data: any
  ts: number
}

type Listener = (ev: WsEvent) => void

/** Auto-reconnecting event stream. Backend batches events every ~200 ms. */
export class EventStream {
  private ws: WebSocket | null = null
  private listeners = new Map<string, Set<Listener>>()
  private retry = 0
  private closed = false
  connected = false
  onStatus: ((connected: boolean) => void) | null = null

  connect(): void {
    this.closed = false
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws/events?token=${encodeURIComponent(token)}`)
    this.ws.onopen = () => {
      this.retry = 0
      this.setConnected(true)
      this.dispatch({ type: 'reconnected', data: null, ts: Date.now() / 1000 })
    }
    this.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data as string) as WsEvent
      if (msg.type === 'batch') for (const ev of msg.data as WsEvent[]) this.dispatch(ev)
      else this.dispatch(msg)
    }
    this.ws.onclose = () => {
      this.setConnected(false)
      if (this.closed) return
      const delay = Math.min(1000 * 2 ** this.retry++, 10000)
      setTimeout(() => this.connect(), delay)
    }
  }

  close(): void {
    this.closed = true
    this.ws?.close()
  }

  on(type: string, fn: Listener): () => void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(fn)
    return () => this.listeners.get(type)?.delete(fn)
  }

  private setConnected(v: boolean) {
    this.connected = v
    this.onStatus?.(v)
  }

  private dispatch(ev: WsEvent) {
    this.listeners.get(ev.type)?.forEach((fn) => fn(ev))
    this.listeners.get('*')?.forEach((fn) => fn(ev))
  }
}

export const events = new EventStream()
