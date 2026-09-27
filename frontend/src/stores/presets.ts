import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api/client'
import type { Preset } from '@/api/types'

const LS_KEY = 'tga.presets.v1'

/** Presets live in localStorage (instant) with a synchronous duplicate in SQLite (survives WebView cleanup), docs/07. */
export const usePresetsStore = defineStore('presets', () => {
  const items = ref<Preset[]>(readLocal())

  function readLocal(): Preset[] {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || '[]') as Preset[]
    } catch {
      return []
    }
  }

  function writeLocal() {
    localStorage.setItem(LS_KEY, JSON.stringify(items.value))
  }

  async function load() {
    const server = (await api.get<{ items: Preset[] }>('/presets')).items
    if (!server.length && items.value.length) {
      // localStorage has presets the DB lost (e.g. new archive root): push them back.
      await api.post('/presets/import', items.value.map(({ kind, name, data }) => ({ kind, name, data })))
      items.value = (await api.get<{ items: Preset[] }>('/presets')).items
    } else {
      items.value = server
    }
    writeLocal()
  }

  async function save(p: Preset) {
    if (p.id) await api.put(`/presets/${p.id}`, p)
    else p.id = (await api.post<{ id: number }>('/presets', p)).id
    const i = items.value.findIndex((x) => x.id === p.id)
    if (i >= 0) items.value[i] = p
    else items.value.push(p)
    writeLocal()
  }

  async function remove(id: number) {
    await api.del(`/presets/${id}`)
    items.value = items.value.filter((p) => p.id !== id)
    writeLocal()
  }

  function exportJson(): string {
    return JSON.stringify(items.value.map(({ kind, name, data }) => ({ kind, name, data })), null, 2)
  }

  async function importJson(text: string) {
    const list = JSON.parse(text) as Preset[]
    await api.post('/presets/import', list.map(({ kind, name, data }) => ({ kind, name, data })))
    await load()
  }

  const byKind = (kind: Preset['kind']) => items.value.filter((p) => p.kind === kind)

  return { items, load, save, remove, exportJson, importJson, byKind }
})
