<script setup lang="ts">
import { NButton, NCard, NEmpty, NGrid, NGridItem, NSkeleton, NStatistic } from 'naive-ui'
import { onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api, events } from '@/api/client'
import type { Job } from '@/api/types'
import JobCard from '@/components/JobCard.vue'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'
import { formatBytes } from '@/utils'

interface Dash {
  me: { first_name?: string; username?: string } | null
  archive_root: string
  account_dir: string
  stats: Record<string, number>
  jobs: Job[]
  last_report: { id: number; kind: string; created_at: string } | null
}

const { t } = useI18n()
const router = useRouter()
const jobs = useJobsStore()
const chats = useChatsStore()
const data = ref<Dash | null>(null)

async function load() {
  data.value = await api.get<Dash>('/dashboard')
}

async function exportSaved() {
  const saved = chats.items.find((c) => c.type === 'saved')
  if (!saved) return
  await api.post('/export', { chat_ids: [saved.id], formats: ['md', 'html'], split: { mode: 'month' }, media_types: [], no_media: true, filters: {}, also_full: false, include_transcripts: true })
  router.push('/downloads')
}

const openFolder = (path: string) => api.post('/open-path', { path })
let off: (() => void) | undefined
onMounted(() => {
  load()
  off = events.on('job.update', (ev) => {
    if (['done', 'failed'].includes(ev.data.status)) load()
  })
})
onActivated(load)
onBeforeUnmount(() => off?.())
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>{{ t('dashboard.hello', { name: data?.me?.first_name ?? '' }) }}</h1>
      <NButton @click="chats.refresh()">↻ {{ t('dashboard.refreshChats') }}</NButton>
      <NButton type="primary" @click="router.push('/chats')">{{ t('dashboard.exportChats') }}</NButton>
    </div>
    <NSkeleton v-if="!data" height="120px" :sharp="false" />
    <template v-else>
      <NGrid :cols="4" :x-gap="12" :y-gap="12" responsive="screen" item-responsive>
        <NGridItem span="4 m:1"><NCard size="small"><NStatistic :label="t('dashboard.chats')" :value="data.stats.chats" /><div class="small muted">{{ t('dashboard.archivedChats', { n: data.stats.archived_chats }) }}</div></NCard></NGridItem>
        <NGridItem span="4 m:1"><NCard size="small"><NStatistic :label="t('dashboard.messages')" :value="data.stats.messages.toLocaleString()" /></NCard></NGridItem>
        <NGridItem span="4 m:1"><NCard size="small"><NStatistic :label="t('dashboard.archiveSize')" :value="formatBytes(data.stats.media_bytes + data.stats.db_bytes)" /><div class="small muted">{{ t('dashboard.mediaFiles', { n: data.stats.media_done }) }}</div></NCard></NGridItem>
        <NGridItem span="4 m:1"><NCard size="small" hoverable style="cursor: pointer" @click="router.push('/monitor')"><NStatistic :label="t('dashboard.unread')" :value="data.stats.unread" /><div class="small muted">{{ t('dashboard.unreadChats', { n: data.stats.unread_chats }) }}</div></NCard></NGridItem>
      </NGrid>

      <h3>{{ t('dashboard.activeJobs') }}</h3>
      <div v-if="jobs.active.length" class="grid-cards">
        <JobCard v-for="j in jobs.active.slice(0, 6)" :key="j.id" :job="j" />
      </div>
      <NEmpty v-else :description="t('dashboard.noJobs')" />

      <h3>{{ t('dashboard.quick') }}</h3>
      <div class="grid-cards">
        <NCard size="small" hoverable @click="router.push({ path: '/chats', query: { filter: 'private' } })" style="cursor: pointer">💬 {{ t('dashboard.quickPrivate') }}</NCard>
        <NCard size="small" hoverable @click="exportSaved" style="cursor: pointer">⭐ {{ t('dashboard.quickSaved') }}</NCard>
        <NCard size="small" hoverable @click="router.push('/search')" style="cursor: pointer">🔎 {{ t('dashboard.quickSearch') }}</NCard>
        <NCard size="small" hoverable @click="openFolder(data.account_dir)" style="cursor: pointer">📂 {{ t('dashboard.openArchive') }}<div class="small muted ellipsis">{{ data.account_dir }}</div></NCard>
        <NCard v-if="data.last_report" size="small" hoverable @click="router.push({ path: '/monitor', query: { report: data.last_report.id } })" style="cursor: pointer">🤖 {{ t('dashboard.lastDigest') }}<div class="small muted">{{ data.last_report.created_at.slice(0, 16).replace('T', ' ') }}</div></NCard>
      </div>
    </template>
  </div>
</template>
