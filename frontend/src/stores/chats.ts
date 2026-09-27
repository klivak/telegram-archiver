import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { api, events } from '@/api/client'
import type { Chat, Folder } from '@/api/types'

const CACHE_KEY = 'tga.chats.v1'

export type ChatFilter = 'all' | 'private' | 'bots' | 'groups' | 'channels' | 'forums' | 'archived' | 'unread' | 'protected'

export const FILTERS: ChatFilter[] = ['all', 'private', 'bots', 'groups', 'channels', 'forums', 'archived', 'unread', 'protected']

export function matchFilter(c: Chat, f: ChatFilter): boolean {
  switch (f) {
    case 'private':
      return c.type === 'user' || c.type === 'saved'
    case 'bots':
      return c.type === 'bot'
    case 'groups':
      return c.type === 'group' || c.type === 'supergroup'
    case 'channels':
      return c.type === 'channel'
    case 'forums':
      return c.type === 'forum'
    case 'archived':
      return c.is_archived === 1
    case 'unread':
      return c.unread_count > 0
    case 'protected':
      return c.noforwards === 1
    default:
      return true
  }
}

export const useChatsStore = defineStore('chats', () => {
  // shallowRef: 2000+ chats, we replace the array instead of deep-tracking every row (docs/17 budget).
  const items = shallowRef<Chat[]>(loadCache())
  const folders = ref<Folder[]>([])
  const loading = ref(false)
  const loaded = ref(items.value.length > 0)
  let started = false

  const byId = computed(() => new Map(items.value.map((c) => [c.id, c])))

  function loadCache(): Chat[] {
    try {
      return JSON.parse(localStorage.getItem(CACHE_KEY) || '[]') as Chat[]
    } catch {
      return []
    }
  }

  async function load() {
    loading.value = true
    try {
      const res = await api.get<{ items: Chat[] }>('/chats')
      items.value = res.items
      loaded.value = true
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(res.items))
      } catch {
        /* quota */
      }
    } finally {
      loading.value = false
    }
  }

  async function loadFolders() {
    folders.value = (await api.get<{ items: Folder[] }>('/folders')).items
  }

  const refresh = () => api.post<{ job_id: number }>('/chats/refresh')

  let timer: ReturnType<typeof setTimeout> | undefined
  const scheduleLoad = () => {
    clearTimeout(timer)
    timer = setTimeout(load, 400)
  }

  function start() {
    if (started) return
    started = true
    events.on('chats.changed', scheduleLoad)
    events.on('folders.changed', () => loadFolders().then(scheduleLoad))
    events.on('media.changed', scheduleLoad)
    events.on('reconnected', scheduleLoad)
    load()
    loadFolders()
  }

  return { items, folders, loading, loaded, byId, load, loadFolders, refresh, start }
})
