<script setup lang="ts">
import { NButton, NCard, NProgress, NSpace, NTag, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Job } from '@/api/types'
import { useJobsStore } from '@/stores/jobs'
import { formatBytes, formatDuration, secondsUntil } from '@/utils'

const props = defineProps<{ job: Job; compact?: boolean }>()
const { t, te } = useI18n()
const jobs = useJobsStore()

const p = computed(() => props.job.progress ?? {})
const percent = computed(() => {
  const x = p.value
  if (x.chats_total) {
    const inner = x.msg_total ? Math.min(1, (x.msg_done ?? 0) / x.msg_total) : 0
    return Math.round((((x.chats_done ?? 0) + inner) / x.chats_total) * 100)
  }
  if (x.bytes_total) return Math.round(((x.bytes_done ?? 0) / x.bytes_total) * 100)
  if (x.total) return Math.round(((x.done ?? 0) / x.total) * 100)
  if (x.msg_total) return Math.round(((x.msg_done ?? 0) / x.msg_total) * 100)
  return props.job.status === 'done' ? 100 : 0
})
const statusType = computed(() => ({ running: 'info', done: 'success', failed: 'error', flood_wait: 'warning', paused: 'default', queued: 'default', cancelled: 'default' })[props.job.status] as 'info')
const title = computed(() => (te(`jobs.kind.${props.job.kind}`) ? t(`jobs.kind.${props.job.kind}`) : props.job.title))
const detail = computed(() => {
  const x = p.value
  const parts: string[] = []
  if (x.chats_total) parts.push(t('jobs.chatsProgress', { done: x.chats_done ?? 0, total: x.chats_total }))
  if (x.chat_title) parts.push(x.chat_title)
  if (x.msg_total) parts.push(t('jobs.messagesProgress', { done: (x.msg_done ?? 0).toLocaleString(), total: x.msg_total.toLocaleString() }))
  if (x.bytes_total) parts.push(`${formatBytes(x.bytes_done)} / ${formatBytes(x.bytes_total)}`)
  if (x.speed) parts.push(`${formatBytes(x.speed)}/s`)
  if (x.speed && x.bytes_total) {
    const eta = formatDuration((x.bytes_total - (x.bytes_done ?? 0)) / x.speed)
    parts.push(t('jobs.eta', { time: eta.h ? `${eta.h}h ${eta.m}m` : `${eta.m}m ${eta.s}s` }))
  }
  if (x.stage && te(`jobs.stage.${x.stage}`)) parts.push(t(`jobs.stage.${x.stage}`))
  if (x.states !== undefined) parts.push(t('miniapps.states', { n: x.states }))
  if (x.deferred) parts.push(t(`jobs.deferred.${x.deferred}`))
  return parts.join(' · ')
})
const waitLeft = computed(() => (props.job.status === 'flood_wait' ? secondsUntil(props.job.wait_until) : 0))
const result = computed(() => p.value.result as Record<string, any> | undefined)
</script>

<template>
  <NCard size="small" :bordered="true">
    <div class="row">
      <strong class="grow ellipsis">{{ title }}</strong>
      <NTag size="small" :type="statusType" round>{{ t(`jobs.status.${job.status}`) }}</NTag>
    </div>
    <NProgress v-if="job.status !== 'done' && job.status !== 'cancelled'" type="line" :percentage="percent" :processing="job.status === 'running'" :status="job.status === 'failed' ? 'error' : job.status === 'flood_wait' ? 'warning' : 'default'" style="margin: 8px 0 4px" />
    <NText depth="3" class="small">{{ detail }}</NText>
    <div v-if="waitLeft" class="small" style="color: #f0a020">{{ t('flood.short', { s: waitLeft }) }}</div>
    <div v-if="job.error && job.status === 'failed'" class="small" style="color: #d03050; word-break: break-word">{{ job.error }}</div>
    <div v-if="job.status === 'done' && result?.skipped_protected?.length" class="small" style="color: #f0a020">
      {{ t('export.skippedProtected', { n: result.skipped_protected.length }) }}
    </div>
    <NSpace v-if="!compact" size="small" style="margin-top: 8px">
      <NButton v-if="['running', 'queued', 'flood_wait'].includes(job.status)" size="tiny" @click="jobs.action(job.id, 'pause')">⏸ {{ t('common.pause') }}</NButton>
      <NButton v-if="job.status === 'paused'" size="tiny" type="primary" @click="jobs.action(job.id, 'resume')">▶ {{ t('common.resume') }}</NButton>
      <NButton v-if="['failed', 'cancelled'].includes(job.status)" size="tiny" @click="jobs.action(job.id, 'retry')">↻ {{ t('common.retry') }}</NButton>
      <NButton v-if="!['done', 'failed', 'cancelled'].includes(job.status)" size="tiny" quaternary @click="jobs.action(job.id, 'cancel')">✕ {{ t('common.cancel') }}</NButton>
    </NSpace>
  </NCard>
</template>
