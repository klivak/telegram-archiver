import { describe, expect, it } from 'vitest'
import type { Chat } from '../src/api/types'
import { matchFilter } from '../src/stores/chats'
import { formatBytes, formatDuration, initials, secondsUntil } from '../src/utils'

describe('utils', () => {
  it('formats bytes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
  })
  it('splits durations', () => {
    expect(formatDuration(252)).toEqual({ h: 0, m: 4, s: 12 })
  })
  it('counts down to ISO timestamps with offsets', () => {
    const iso = new Date(Date.now() + 10_000).toISOString().replace('Z', '+00:00')
    expect(secondsUntil(iso)).toBeGreaterThanOrEqual(9)
    expect(secondsUntil(null)).toBe(0)
  })
  it('initials handle emoji and cyrillic', () => {
    expect(initials('Робочий чат')).toBe('РЧ')
    expect(initials('😀 Fun')).toBe('😀F')
  })
})

describe('chat filters', () => {
  const base = { id: 1, title: 'x', username: null, is_forum: 0, is_archived: 0, unread_count: 0, unread_mentions: 0, last_message_at: null, noforwards: 0, stored_messages: 0, media_count: 0, media_done: 0, folders: [], synced: null, total_messages: null } as const
  const mk = (p: Partial<Chat>): Chat => ({ ...base, type: 'user', ...p }) as Chat
  it('private includes saved messages', () => {
    expect(matchFilter(mk({ type: 'saved' }), 'private')).toBe(true)
    expect(matchFilter(mk({ type: 'bot' }), 'private')).toBe(false)
  })
  it('protected and unread chips', () => {
    expect(matchFilter(mk({ noforwards: 1 }), 'protected')).toBe(true)
    expect(matchFilter(mk({ unread_count: 3 }), 'unread')).toBe(true)
    expect(matchFilter(mk({ type: 'supergroup' }), 'groups')).toBe(true)
  })
})
