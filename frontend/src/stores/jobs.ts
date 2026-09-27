import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, events } from '@/api/client'
import type { Job } from '@/api/types'

export const useJobsStore = defineStore('jobs', () => {
  const items = ref<Map<number, Job>>(new Map())
  let started = false

  const list = computed(() => [...items.value.values()].sort((a, b) => b.id - a.id))
  const active = computed(() => list.value.filter((j) => !['done', 'failed', 'cancelled'].includes(j.status)))
  const floodWaiting = computed(() => active.value.filter((j) => j.status === 'flood_wait'))

  async function load() {
    const res = await api.get<{ items: Job[] }>('/jobs', { limit: 200 })
    const m = new Map<number, Job>()
    for (const j of res.items) m.set(j.id, j)
    items.value = m
  }

  function upsert(job: Job) {
    const m = new Map(items.value)
    m.set(job.id, job)
    items.value = m
  }

  function start() {
    if (started) return
    started = true
    events.on('job.update', (ev) => upsert(ev.data as Job))
    events.on('job.progress', (ev) => {
      const j = items.value.get(ev.data.id)
      if (j) upsert({ ...j, progress: ev.data.progress })
    })
    events.on('jobs.changed', () => load())
    events.on('reconnected', () => load())
    load()
  }

  const action = (id: number, a: 'pause' | 'resume' | 'cancel' | 'retry') => api.post(`/jobs/${id}/${a}`)
  const all = (a: 'pause' | 'resume' | 'clear') => api.post(`/jobs-all/${a}`).then(load)

  /** Resolves when the job reaches a final state. */
  function waitFor(id: number): Promise<Job> {
    return new Promise((resolve) => {
      const check = () => {
        const j = items.value.get(id)
        if (j && ['done', 'failed', 'cancelled'].includes(j.status)) {
          off()
          resolve(j)
        }
      }
      const off = events.on('job.update', check)
      check()
    })
  }

  return { items, list, active, floodWaiting, load, start, action, all, waitFor }
})
