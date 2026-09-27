<script setup lang="ts">
import { NButton, NButtonGroup, NEmpty, NProgress, NSpace, NTabPane, NTabs, NTag, NVirtualList } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, events } from '@/api/client'
import type { MediaItem } from '@/api/types'
import JobCard from '@/components/JobCard.vue'
import { useJobsStore } from '@/stores/jobs'
import { formatBytes } from '@/utils'

const { t } = useI18n()
const jobs = useJobsStore()
const tab = ref<'jobs' | 'media'>('jobs')
const media = ref<MediaItem[]>([])
const counts = ref<Record<string, { n: number; bytes: number; done: number }>>({})
const status = ref<string | null>(null)
const live = ref<Record<number, { bytes_done: number; speed: number }>>({})

const history = computed(() => jobs.list.filter((j) => ['done', 'failed', 'cancelled'].includes(j.status)).slice(0, 50))

async function loadMedia() {
  const r = await api.get<{ items: MediaItem[]; counts: Record<string, { n: number; bytes: number; done: number }> }>('/media/queue', { status: status.value ?? undefined, limit: 500 })
  media.value = r.items
  counts.value = r.counts
}

let timer: ReturnType<typeof setTimeout> | undefined
const scheduleMedia = () => {
  clearTimeout(timer)
  timer = setTimeout(loadMedia, 700)
}

const mediaAction = (id: number, a: string) => api.post(`/media/${id}/${a}`).then(scheduleMedia)
const mediaAll = (a: string) => api.post(`/media-all/${a}`, { chat_id: null }).then(scheduleMedia)

let offs: (() => void)[] = []
onMounted(() => {
  loadMedia()
  offs = [
    events.on('media.progress', (ev) => {
      live.value = { ...live.value, [ev.data.id]: { bytes_done: ev.data.bytes_done, speed: ev.data.speed } }
    }),
    events.on('media.done', scheduleMedia),
    events.on('media.changed', scheduleMedia),
  ]
})
onBeforeUnmount(() => offs.forEach((f) => f()))

const statuses = ['downloading', 'pending', 'paused', 'failed', 'done', 'skipped']
type TagType = 'info' | 'success' | 'error' | 'warning' | 'default'
const TAGS: Record<string, TagType> = { downloading: 'info', done: 'success', failed: 'error', paused: 'warning' }
const tagType = (s: string): TagType => TAGS[s] ?? 'default'
const doneBytes = (m: MediaItem) => live.value[m.id]?.bytes_done ?? (m.status === 'done' ? m.size : m.bytes_done)
</script>

<template>
  <div class="page" data-tour="downloads">
    <div class="page-header">
      <h1>{{ t('nav.downloads') }}</h1>
      <NButtonGroup size="small">
        <NButton @click="jobs.all('pause')">⏸ {{ t('downloads.pauseAll') }}</NButton>
        <NButton @click="jobs.all('resume')">▶ {{ t('downloads.resumeAll') }}</NButton>
        <NButton @click="jobs.all('clear')">🧹 {{ t('downloads.clear') }}</NButton>
      </NButtonGroup>
    </div>
    <div class="small muted" style="margin: -8px 0 8px">{{ t('downloads.spaceHint') }}</div>
    <NTabs v-model:value="tab" type="line">
      <NTabPane name="jobs" :tab="t('downloads.jobs', { n: jobs.active.length })">
        <div v-if="jobs.active.length" class="grid-cards" style="grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))">
          <JobCard v-for="j in jobs.active" :key="j.id" :job="j" />
        </div>
        <NEmpty v-else :description="t('downloads.noActive')" style="margin: 30px 0" />
        <h3 v-if="history.length">{{ t('downloads.history') }}</h3>
        <div class="grid-cards" style="grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))">
          <JobCard v-for="j in history" :key="j.id" :job="j" />
        </div>
      </NTabPane>
      <NTabPane name="media" :tab="t('downloads.files')">
        <NSpace align="center" style="margin-bottom: 8px">
          <NTag :checked="status === null" checkable @update:checked="status = null; loadMedia()">{{ t('downloads.allStatuses') }}</NTag>
          <NTag v-for="s in statuses" :key="s" :checked="status === s" checkable @update:checked="status = s; loadMedia()">
            {{ t(`mediaStatus.${s}`) }} <span class="small muted">{{ counts[s]?.n ?? 0 }}</span>
          </NTag>
        </NSpace>
        <NSpace size="small" style="margin-bottom: 8px">
          <NButton size="small" @click="mediaAll('pause')">⏸ {{ t('downloads.pauseQueue') }}</NButton>
          <NButton size="small" @click="mediaAll('resume')">▶ {{ t('downloads.resumeQueue') }}</NButton>
          <NButton size="small" @click="mediaAll('retry')">↻ {{ t('downloads.retryFailed') }}</NButton>
          <NButton size="small" quaternary @click="mediaAll('cancel')">✕ {{ t('downloads.cancelQueue') }}</NButton>
        </NSpace>
        <NEmpty v-if="!media.length" :description="t('downloads.noFiles')" />
        <NVirtualList v-else :items="media" :item-size="54" key-field="id" style="height: calc(100vh - 290px)">
          <template #default="{ item }">
            <div class="file">
              <div class="grow">
                <div class="row"><span class="ellipsis">{{ item.file_name }}</span><NTag size="tiny" :type="tagType(item.status)">{{ t(`mediaStatus.${item.status}`) }}</NTag></div>
                <div class="small muted row">
                  <span class="ellipsis">{{ item.chat_title }} · {{ t(`media.${item.type}`) }}</span>
                  <span>{{ formatBytes(doneBytes(item)) }} / {{ formatBytes(item.size) }}</span>
                  <span v-if="live[item.id]?.speed && item.status === 'downloading'">· {{ formatBytes(live[item.id].speed) }}/s</span>
                  <span v-if="item.error" style="color: #d03050" class="ellipsis">· {{ item.error }}</span>
                </div>
                <NProgress v-if="item.status === 'downloading' || item.status === 'paused'" type="line" :percentage="item.size ? Math.round((doneBytes(item) / item.size) * 100) : 0" :show-indicator="false" :height="3" />
              </div>
              <NSpace size="small">
                <NButton v-if="['pending', 'paused', 'failed'].includes(item.status)" size="tiny" :title="t('chat.downloadNow')" @click="mediaAction(item.id, 'download-now')">⚡</NButton>
                <NButton v-if="['pending', 'downloading'].includes(item.status)" size="tiny" @click="mediaAction(item.id, 'pause')">⏸</NButton>
                <NButton v-if="item.status === 'paused'" size="tiny" @click="mediaAction(item.id, 'resume')">▶</NButton>
                <NButton v-if="item.status === 'failed'" size="tiny" @click="mediaAction(item.id, 'retry')">↻</NButton>
                <NButton v-if="item.status !== 'done' && item.status !== 'skipped'" size="tiny" quaternary @click="mediaAction(item.id, 'cancel')">✕</NButton>
              </NSpace>
            </div>
          </template>
        </NVirtualList>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.file {
  height: 54px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.12);
}
</style>
