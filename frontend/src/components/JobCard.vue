<script setup lang="ts">
import { NButton, NCard, NProgress, NTag } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFormat } from '@/composables/format'
import { api } from '@/api/client'
import type { Job } from '@/api/types'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'
import { formatBytes, formatDuration, secondsUntil } from '@/utils'

const props = defineProps<{ job: Job; compact?: boolean }>()
const fmt = useFormat()
const { t, te, locale } = useI18n()
const jobs = useJobsStore()
const chats = useChatsStore()
const showDetails = ref(false)

const p = computed(() => props.job.progress ?? {})
const clamp = (v: number) => Math.max(0, Math.min(100, Math.round(v)))
const isExport = computed(() => props.job.kind === 'export')
const stage = computed<string>(() => p.value.stage ?? '')

// messages: whole job over all chats (a finished chat counts as 100%, never above)
const msgPercent = computed(() => {
  const x = p.value
  const inner = x.msg_total ? Math.min(1, (x.msg_done ?? 0) / x.msg_total) : 0
  if (x.chats_total) {
    const done = Math.min(x.chats_done ?? 0, x.chats_total)
    return clamp(((done + (done < x.chats_total ? inner : 0)) / x.chats_total) * 100)
  }
  return clamp(inner * 100)
})
const bytesPercent = computed(() => (p.value.bytes_total ? clamp(((p.value.bytes_done ?? 0) / p.value.bytes_total) * 100) : 0))
const percent = computed(() => {
  const x = p.value
  if (props.job.status === 'done') return 100
  if (x.bytes_total && (stage.value === 'media' || !x.msg_total)) return bytesPercent.value
  if (x.msg_total || x.chats_total) return msgPercent.value
  if (x.total) return clamp(((x.done ?? 0) / x.total) * 100)
  return 0
})
const eta = computed(() => {
  const x = p.value
  if (!x.speed || !x.bytes_total) return ''
  const d = formatDuration((x.bytes_total - (x.bytes_done ?? 0)) / x.speed)
  return d.h ? `${d.h} ${t('jobs.h')} ${d.m} ${t('jobs.m')}` : d.m ? `${d.m} ${t('jobs.m')} ${d.s} ${t('jobs.s')}` : `${d.s} ${t('jobs.s')}`
})

const statusType = computed(() => ({ running: 'info', done: 'success', failed: 'error', flood_wait: 'warning', paused: 'warning', queued: 'default', cancelled: 'default' })[props.job.status] as 'info')
const kindTitle = computed(() => (te(`jobs.kind.${props.job.kind}`) ? t(`jobs.kind.${props.job.kind}`) : props.job.title))
const chatIds = computed<number[]>(() => props.job.params?.chat_ids ?? (props.job.params?.chat_id ? [props.job.params.chat_id] : []))
const subject = computed(() => {
  if (p.value.chat_title && chatIds.value.length <= 1) return p.value.chat_title as string
  if (chatIds.value.length === 1) return chats.byId.get(chatIds.value[0])?.title ?? ''
  if (chatIds.value.length > 1) return t('jobs.nChats', { n: fmt.n(chatIds.value.length) })
  return ''
})
const ICONS: Record<string, string> = { demo: '🧪', sync_dialogs: '🔄', sync_history: '🔄', export: '📦', render: '🛠️', download_media: '🖼️', transcribe: '🎙️', detect_miniapps: '🧩', miniapp_session: '🧩', miniapp_replay: '⏪', monitor: '🔔', ai: '🤖', chat_preview: '💬' }
const icon = computed(() => ICONS[props.job.kind] ?? '⚙️')
const chip = computed(() => ({ done: 'green', failed: 'red', flood_wait: 'amber', paused: 'amber' })[props.job.status as string] ?? '')
const waitLeft = computed(() => (props.job.status === 'flood_wait' ? secondsUntil(props.job.wait_until) : 0))
const result = computed(() => p.value.result as Record<string, any> | undefined)
const active = computed(() => ['running', 'queued', 'flood_wait', 'paused'].includes(props.job.status))

// export steps: history -> files -> media
const mediaTypes = computed<string[]>(() => (props.job.params?.no_media ? [] : props.job.params?.media_types ?? []))
type StepState = 'done' | 'active' | 'todo' | 'skip'
const steps = computed(() => {
  if (!isExport.value) return []
  const order = ['messages', 'render', 'media']
  const cur = props.job.status === 'done' ? 99 : Math.max(0, order.indexOf(stage.value === 'done' ? 'media' : stage.value))
  return order.map((k, n) => {
    let s: StepState = n < cur ? 'done' : n === cur ? 'active' : 'todo'
    if (k === 'media' && !mediaTypes.value.length) s = 'skip'
    if (props.job.status === 'done' && s !== 'skip') s = 'done'
    return { key: k, state: s }
  })
})

const when = (iso: string) => {
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  const today = new Date().toDateString() === d.toDateString()
  return d.toLocaleString(locale.value, today ? { hour: '2-digit', minute: '2-digit' } : { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
const period = computed(() => {
  const f = props.job.params?.filters ?? {}
  if (f.date_from || f.date_to) return `${f.date_from ?? '…'} - ${f.date_to ?? '…'}`
  return isExport.value ? t('export.period.all') : ''
})
const paramChips = computed(() => {
  if (!isExport.value) return []
  const out: string[] = []
  const f: string[] = props.job.params?.formats ?? []
  if (f.length) out.push(`📝 ${f.map((x) => t(`formats.${x}`)).join(', ')}`)
  out.push(mediaTypes.value.length ? `🗂️ ${mediaTypes.value.map((m) => t(`media.${m}`)).join(', ')}` : `💬 ${t('export.modeText')}`)
  if (period.value) out.push(`📅 ${period.value}`)
  return out
})

const FOLDER_KINDS = ['export', 'download_media', 'sync_history', 'transcribe', 'miniapp_session']
const showFolder = computed(() => FOLDER_KINDS.includes(props.job.kind))
function openFolder() {
  api.post(chatIds.value.length === 1 ? `/chats/${chatIds.value[0]}/reveal` : '/archive/reveal')
}
</script>

<template>
  <NCard size="small" :bordered="true" class="job" :class="[job.status, { compact }]">
    <div class="head">
      <span class="icon-chip sm" :class="chip">{{ icon }}</span>
      <div class="grow" style="min-width: 0">
        <div class="title ellipsis"><strong>{{ kindTitle }}</strong><span v-if="subject" class="subject"> · {{ subject }}</span></div>
        <div class="meta small">{{ when(job.created_at) }}<template v-if="!active && job.updated_at"> → {{ when(job.updated_at) }}</template></div>
      </div>
      <NTag size="small" :type="statusType" round :bordered="false">{{ t(`jobs.status.${job.status}`) }}</NTag>
    </div>

    <div v-if="steps.length" class="steps">
      <template v-for="(s, n) in steps" :key="s.key">
        <span class="step" :class="s.state">
          <span class="dot">{{ s.state === 'done' ? '✓' : s.state === 'skip' ? '–' : n + 1 }}</span>{{ t(`jobs.step.${s.key}`) }}
        </span>
        <span v-if="n < steps.length - 1" class="line" :class="{ on: s.state === 'done' }"></span>
      </template>
    </div>

    <template v-if="active || job.status === 'failed'">
      <div v-if="p.msg_total || p.chats_total" class="metric">
        <div class="metric-top small">
          <span>💬 {{ t('jobs.messages') }}</span>
          <span class="num">{{ fmt.n(p.msg_done ?? 0) }} / {{ fmt.n(p.msg_total ?? 0) }}<template v-if="p.chats_total > 1"> · {{ t('jobs.chatsProgress', { done: fmt.n(Math.min(p.chats_done ?? 0, p.chats_total)), total: fmt.n(p.chats_total) }) }}</template></span>
        </div>
        <NProgress type="line" :percentage="msgPercent" :show-indicator="false" :height="6" :processing="job.status === 'running' && stage === 'messages'" />
      </div>
      <div v-if="p.bytes_total" class="metric">
        <div class="metric-top small">
          <span>🗂️ {{ t('jobs.files') }}<template v-if="p.total"> · {{ fmt.n(p.done ?? 0) }} / {{ fmt.n(p.total) }}</template></span>
          <span class="num">{{ formatBytes(p.bytes_done ?? 0) }} / {{ formatBytes(p.bytes_total) }}</span>
        </div>
        <NProgress type="line" :percentage="bytesPercent" :show-indicator="false" :height="6" :processing="job.status === 'running' && stage === 'media'" />
        <div v-if="job.status === 'running' && p.speed" class="small muted num" style="margin-top: 4px">⚡ {{ formatBytes(p.speed) }}/s<template v-if="eta"> · {{ t('jobs.left', { time: eta }) }}</template></div>
      </div>
      <div v-if="!p.msg_total && !p.chats_total && !p.bytes_total" class="metric">
        <NProgress type="line" :percentage="percent" :show-indicator="false" :height="6" :processing="job.status === 'running'" />
      </div>
    </template>
    <div v-else-if="job.status === 'done' && isExport" class="summary small">
      ✅ {{ t('jobs.doneSummary', { n: fmt.n(p.msg_total ?? p.msg_done ?? 0) }) }}<template v-if="p.bytes_total"> · {{ t('jobs.doneFiles', { size: formatBytes(p.bytes_done ?? p.bytes_total) }) }}</template>
    </div>

    <div v-if="waitLeft" class="notice warn small">⏳ {{ t('flood.short', { s: waitLeft }) }}</div>
    <div v-if="p.deferred" class="notice small">⏸ {{ t(`jobs.deferred.${p.deferred}`) }}</div>
    <div v-if="job.error && job.status === 'failed'" class="notice err small">{{ job.error }}</div>
    <div v-if="job.status === 'done' && result?.skipped_protected?.length" class="notice warn small">{{ t('export.skippedProtected', { n: result.skipped_protected.length }) }}</div>

    <div v-if="paramChips.length && !compact" class="chips">
      <span v-for="c in paramChips" :key="c" class="pchip small">{{ c }}</span>
    </div>

    <div v-if="!compact" class="actions">
      <NButton v-if="['running', 'queued', 'flood_wait'].includes(job.status)" size="small" secondary @click="jobs.action(job.id, 'pause')">⏸ {{ t('common.pause') }}</NButton>
      <NButton v-if="job.status === 'paused'" size="small" type="primary" @click="jobs.action(job.id, 'resume')">▶ {{ t('common.resume') }}</NButton>
      <NButton v-if="['failed', 'cancelled'].includes(job.status)" size="small" secondary @click="jobs.action(job.id, 'retry')">↻ {{ t('common.retry') }}</NButton>
      <NButton v-if="showFolder" size="small" secondary @click="openFolder">📂 {{ t('common.openFolder') }}</NButton>
      <span class="grow"></span>
      <NButton size="small" quaternary @click="showDetails = !showDetails">{{ showDetails ? t('jobs.hideDetails') : t('jobs.details') }}</NButton>
      <NButton v-if="active" size="small" quaternary type="error" @click="jobs.action(job.id, 'cancel')">✕</NButton>
    </div>
    <Transition name="det">
      <dl v-if="showDetails" class="details small">
        <dt>ID</dt><dd class="num">#{{ job.id }}</dd>
        <dt>{{ t('jobs.created') }}</dt><dd>{{ when(job.created_at) }}</dd>
        <dt>{{ t('jobs.updated') }}</dt><dd>{{ when(job.updated_at) }}</dd>
        <template v-if="stage"><dt>{{ t('jobs.stageLabel') }}</dt><dd>{{ te(`jobs.stage.${stage}`) ? t(`jobs.stage.${stage}`) : stage }}</dd></template>
        <template v-if="job.attempts"><dt>{{ t('jobs.attempts') }}</dt><dd class="num">{{ job.attempts }}</dd></template>
        <template v-if="p.failed"><dt>{{ t('jobs.failedFiles') }}</dt><dd class="num">{{ fmt.n(p.failed) }}</dd></template>
        <template v-if="isExport && job.params?.split"><dt>{{ t('export.split') }}</dt><dd>{{ t(`split.${job.params.split.mode}`) }}</dd></template>
        <template v-if="chatIds.length > 1"><dt>{{ t('jobs.chats') }}</dt><dd>{{ chatIds.map((id) => chats.byId.get(id)?.title ?? id).join(', ') }}</dd></template>
        <template v-if="job.error"><dt>{{ t('jobs.error') }}</dt><dd class="err">{{ job.error }}</dd></template>
      </dl>
    </Transition>
  </NCard>
</template>

<style scoped>
.job { transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease); }
.job.running { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
.job.failed { border-color: color-mix(in srgb, var(--danger) 45%, var(--border)); }
.job.done, .job.cancelled { opacity: 0.92; }
.head { display: flex; align-items: center; gap: 10px; }
.title { font-size: 14px; }
.subject { color: var(--text-2); font-weight: 500; }
.meta { color: var(--text-3); font-variant-numeric: tabular-nums; }
.steps { display: flex; align-items: center; gap: 6px; margin: 12px 0 4px; flex-wrap: wrap; }
.step { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-3); white-space: nowrap; }
.step .dot { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 600; border: 1px solid var(--border-strong); background: var(--bg-sunken); }
.step.done { color: var(--text-2); }
.step.done .dot { background: var(--success-soft); border-color: var(--success); color: var(--success); }
.step.active { color: var(--text); font-weight: 600; }
.step.active .dot { background: var(--accent); border-color: var(--accent); color: #fff; box-shadow: 0 0 0 3px var(--accent-soft); }
.step.skip { text-decoration: line-through; opacity: 0.6; }
.line { flex: 1; min-width: 12px; height: 2px; border-radius: 2px; background: var(--border-strong); }
.line.on { background: var(--success); }
.metric { margin-top: 10px; }
.metric-top { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; color: var(--text-2); }
.num { font-variant-numeric: tabular-nums; }
.summary { margin-top: 10px; color: var(--text-2); }
.notice { margin-top: 10px; padding: 6px 10px; border-radius: var(--radius-sm); background: var(--bg-sunken); color: var(--text-2); word-break: break-word; }
.notice.warn { background: var(--warning-soft); color: var(--warning); }
.notice.err { background: var(--danger-soft); color: var(--danger); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.pchip { padding: 2px 8px; border-radius: 999px; background: var(--bg-sunken); border: 1px solid var(--border); color: var(--text-2); }
.actions { display: flex; align-items: center; gap: 6px; margin-top: 12px; flex-wrap: wrap; }
.grow { flex: 1; }
.details { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 10px 0 0; padding: 10px 12px; border-radius: var(--radius-sm); background: var(--bg-sunken); }
.details dt { color: var(--text-3); }
.details dd { margin: 0; color: var(--text-2); word-break: break-word; }
.details .err { color: var(--danger); }
.det-enter-active, .det-leave-active { transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease); }
.det-enter-from, .det-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
