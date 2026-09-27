<script setup lang="ts">
import { NAlert, NButton, NCard, NCheckbox, NDatePicker, NEmpty, NInput, NList, NListItem, NSelect, NSpace, NTabPane, NTabs, NTag, useMessage } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { api, events } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { formatDuration, senderName } from '@/utils'

interface Unread {
  chat_id: number
  title: string
  type: string
  unread: number
  mentions: number
  stored: number
  last_message_at: string
  preview: { id: number; date: string; text: string; media_type: string | null; first_name: string | null; last_name: string | null }[]
}
interface Report {
  id: number
  kind: string
  provider: string
  model: string
  tokens_in: number
  tokens_out: number
  created_at: string
  result?: Record<string, any>
}

const { t } = useI18n()
const app = useAppStore()
const chats = useChatsStore()
const route = useRoute()
const router = useRouter()
const message = useMessage()
const unread = ref<Unread[]>([])
const lastRun = ref<string | null>(null)
const reports = ref<Report[]>([])
const current = ref<Report | null>(null)
const task = ref<'digest' | 'priorities' | 'action_items' | 'topics' | 'qa'>('digest')
const scopeKind = ref<'unread' | 'folder' | 'chats' | 'days'>('unread')
const scopeFolder = ref<number | null>(null)
const scopeChats = ref<number[]>([])
const period = ref<'1' | '3' | '7' | '30' | 'custom'>('7')
const range = ref<[number, number] | null>(null)
const transcribeVoice = ref(true)
const question = ref('')
const estimate = ref<{ messages: number; tokens: number; chats: number; today_used: number; daily_limit: number; untranscribed_voice: number } | null>(null)
const canTranscribe = computed(() => app.modules.whisper && !!app.settings?.whisper.enabled)
const tab = ref(route.query.report ? 'ai' : 'unread')

const day = (ms: number) => { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
const periodScope = computed(() => {
  if (period.value === 'custom' && range.value) return { since: day(range.value[0]), until: day(range.value[1]) }
  return { days: Number(period.value === 'custom' ? 7 : period.value) }
})
const scope = computed(() => {
  if (scopeKind.value === 'folder') return { folder_id: scopeFolder.value, ...periodScope.value }
  if (scopeKind.value === 'chats') return { chat_ids: scopeChats.value, ...periodScope.value }
  if (scopeKind.value === 'days') return periodScope.value
  return { unread: true }
})

async function loadUnread() {
  const r = await api.get<{ items: Unread[]; last_run: string | null }>('/monitor/unread')
  unread.value = r.items
  lastRun.value = r.last_run
}
async function loadReports() {
  reports.value = (await api.get<{ items: Report[] }>('/ai/reports')).items
  const id = Number(route.query.report) || reports.value[0]?.id
  if (id) openReport(id)
}
async function openReport(id: number) {
  current.value = await api.get<Report>(`/ai/reports/${id}`)
}
async function runMonitor() {
  await api.post('/monitor/run')
  message.info(t('monitor.collecting'))
}
async function doEstimate() {
  estimate.value = await api.post('/ai/estimate', { task: task.value, scope: scope.value })
}
async function runAi() {
  if (!app.settings?.ai.enabled) {
    router.push({ path: '/settings', query: { tab: 'ai' } })
    return
  }
  await api.post('/ai/run', { task: task.value, scope: scope.value, question: question.value, provider: provider.value, refresh_unread: scopeKind.value === 'unread', transcribe_voice: canTranscribe.value && transcribeVoice.value })
  message.info(t('monitor.aiStarted'))
}
async function deleteReport(id: number) {
  await api.del(`/ai/reports/${id}`)
  current.value = null
  loadReports()
}
function download(r: Report) {
  const blob = new Blob([JSON.stringify(r.result, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${r.kind}_${r.id}.json`
  a.click()
}
function askNotify() {
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission()
}

const providers = ref<{ id: string; default_model: string; ready: boolean }[]>([])
const provider = ref<string | null>(null) // null = the one from Settings
const effectiveProvider = computed(() => provider.value ?? app.settings?.ai.provider)
const providerOptions = computed(() => providers.value.map((p) => ({ label: `${providerLabel(p.id)}${p.ready ? '' : ` (${t('ai.noKey')})`}`, value: p.id, disabled: !p.ready })))
function providerLabel(id: string) {
  return ({ ollama: 'Ollama', anthropic: 'Anthropic', openai: 'OpenAI', openrouter: 'OpenRouter', groq: 'Groq', gemini: 'Gemini' } as Record<string, string>)[id] ?? id
}
const cloud = computed(() => effectiveProvider.value !== 'ollama')
const lastRunAgo = computed(() => {
  if (!lastRun.value) return ''
  const d = formatDuration((Date.now() - Date.parse(lastRun.value)) / 1000)
  return d.h ? t('time.hMin', { h: d.h, m: d.m }) : t('time.minSec', { m: d.m, s: d.s })
})
const folderOptions = computed(() => chats.folders.map((f) => ({ label: f.name, value: f.id })))
const chatOptions = computed(() => chats.items.map((c) => ({ label: c.title, value: c.id })))
const tasks = ['digest', 'priorities', 'action_items', 'topics', 'qa'] as const

let offs: (() => void)[] = []
onMounted(() => {
  loadUnread()
  loadReports()
  api.get<{ items: { id: string; default_model: string; ready: boolean }[] }>('/ai/providers').then((r) => (providers.value = r.items))
  offs = [events.on('monitor.updated', loadUnread), events.on('ai.report', (ev) => loadReports().then(() => openReport(ev.data.id)))]
})
onBeforeUnmount(() => offs.forEach((f) => f()))
</script>

<template>
  <div class="page" data-tour="page">
    <div class="page-header">
      <h1>{{ t('nav.monitor') }}</h1>
      <NButton @click="runMonitor">↻ {{ t('monitor.collect') }}</NButton>
    </div>
    <NAlert type="info" :show-icon="false" class="small" style="margin-bottom: 12px">{{ t('monitor.noRead') }}</NAlert>
    <NTabs v-model:value="tab" type="line">
      <NTabPane name="unread" :tab="t('monitor.unreadTab', { n: unread.length })">
        <div class="small muted" v-if="lastRun">{{ t('monitor.lastRun', { ago: lastRunAgo }) }}</div>
        <NEmpty v-if="!unread.length" :description="t('monitor.allRead')" style="margin: 30px 0" />
        <NList v-else hoverable clickable>
          <NListItem v-for="u in unread" :key="u.chat_id" @click="router.push(`/chats/${u.chat_id}`)">
            <div class="row">
              <strong class="grow ellipsis">{{ u.title }}</strong>
              <NTag v-if="u.mentions" size="small" type="error">@ {{ u.mentions }}</NTag>
              <NTag size="small" type="info" round>{{ u.unread }}</NTag>
            </div>
            <div v-for="p in u.preview" :key="p.id" class="small muted ellipsis">{{ senderName(p) }}: {{ p.text || (p.media_type ? `[${t(`media.${p.media_type}`, p.media_type)}]` : '') }}</div>
          </NListItem>
        </NList>
      </NTabPane>
      <NTabPane name="ai" :tab="t('monitor.aiTab')">
        <NAlert v-if="!app.settings?.ai.enabled" type="info" style="margin-bottom: 12px">
          {{ t('monitor.aiDisabled') }} <NButton text type="primary" @click="router.push({ path: '/settings', query: { tab: 'ai' } })">{{ t('nav.settings') }} →</NButton>
        </NAlert>
        <NAlert v-else-if="cloud" type="warning" :show-icon="false" class="small" style="margin-bottom: 12px">{{ t('monitor.cloudWarn') }}</NAlert>
        <NCard size="small">
          <NSpace align="center">
            <NSelect v-model:value="task" :options="tasks.map((x) => ({ label: t(`ai.task.${x}`), value: x }))" style="width: 190px" size="small" />
            <NSelect v-model:value="scopeKind" :options="['unread', 'days', 'folder', 'chats'].map((x) => ({ label: t(`ai.scope.${x}`), value: x }))" style="width: 190px" size="small" />
            <NSelect v-if="scopeKind === 'folder'" v-model:value="scopeFolder" :options="folderOptions" style="width: 180px" size="small" />
            <NSelect v-if="scopeKind === 'chats'" v-model:value="scopeChats" multiple filterable :options="chatOptions" style="min-width: 220px" size="small" max-tag-count="responsive" />
            <NSelect v-if="scopeKind !== 'unread'" v-model:value="period" :options="['1', '3', '7', '30', 'custom'].map((x) => ({ label: t(`ai.period.${x}`), value: x }))" style="width: 150px" size="small" />
            <NDatePicker v-if="scopeKind !== 'unread' && period === 'custom'" v-model:value="range" type="daterange" size="small" clearable />
            <NCheckbox v-if="canTranscribe" v-model:checked="transcribeVoice" size="small">{{ t('ai.transcribeVoice') }}</NCheckbox>
            <NSelect v-model:value="provider" :options="providerOptions" :placeholder="providerLabel(app.settings?.ai.provider ?? '')" clearable style="width: 170px" size="small" />
            <NButton size="small" @click="doEstimate">{{ t('ai.estimate') }}</NButton>
            <NButton size="small" type="primary" @click="runAi">✨ {{ t('ai.run') }}</NButton>
          </NSpace>
          <NInput v-if="task === 'qa'" v-model:value="question" :placeholder="t('ai.question')" style="margin-top: 8px" />
          <div v-if="estimate" class="small muted" style="margin-top: 6px">
            {{ t('ai.estimateText', estimate) }}
            <template v-if="estimate.untranscribed_voice"> {{ t(canTranscribe && transcribeVoice ? 'ai.voiceWillTranscribe' : 'ai.voiceSkipped', { n: estimate.untranscribed_voice }) }}</template>
          </div>
          <div class="small muted" style="margin-top: 6px">
            {{ app.settings?.ai.schedule_time ? t('ai.scheduled', { time: app.settings.ai.schedule_time }) : t('ai.notScheduled') }}
            <NButton text size="tiny" @click="askNotify">🔔 {{ t('ai.enableNotifications') }}</NButton>
          </div>
        </NCard>
        <div class="reports">
          <NList class="rlist" hoverable clickable>
            <NListItem v-for="r in reports" :key="r.id" @click="openReport(r.id)">
              <div class="row small"><strong class="grow">{{ t(`ai.task.${r.kind}`) }}</strong><span class="muted">{{ r.created_at.slice(5, 16).replace('T', ' ') }}</span></div>
              <div class="small muted">{{ r.provider }} · {{ r.model }}</div>
            </NListItem>
          </NList>
          <NCard v-if="current" size="small" class="rbody">
            <div class="row">
              <strong class="grow">{{ t(`ai.task.${current.kind}`) }} · {{ current.created_at.slice(0, 16).replace('T', ' ') }}</strong>
              <span class="small muted">{{ current.tokens_in + current.tokens_out }} tok</span>
              <NButton size="tiny" @click="download(current)">JSON</NButton>
              <NButton size="tiny" quaternary @click="deleteReport(current.id)">🗑</NButton>
            </div>
            <template v-if="current.result?.empty"><NEmpty :description="t('ai.emptyScope')" /></template>
            <template v-else-if="current.kind === 'digest'">
              <p>{{ current.result?.overall }}</p>
              <div v-for="c in current.result?.chats ?? []" :key="c.chat" class="block"><strong>{{ c.chat }}</strong><div>{{ c.summary }}</div></div>
            </template>
            <template v-else-if="current.kind === 'priorities' || current.kind === 'action_items'">
              <div v-for="(it, i) in current.result?.items ?? []" :key="i" class="block row">
                <NTag v-if="it.urgency" size="small" :type="it.urgency === 'high' ? 'error' : it.urgency === 'medium' ? 'warning' : 'default'">{{ t(`ai.urgency.${it.urgency}`, it.urgency) }}</NTag>
                <div class="grow">
                  <div>{{ it.what ?? it.task }}</div>
                  <div class="small muted">{{ [it.chat, it.why, it.from, it.deadline ?? it.due].filter(Boolean).join(' · ') }}</div>
                </div>
              </div>
            </template>
            <template v-else-if="current.kind === 'topics'">
              <div v-for="tp in current.result?.topics ?? []" :key="tp.title" class="block"><strong>{{ tp.title }}</strong><div>{{ tp.summary }}</div><div class="small muted">{{ (tp.chats ?? []).join(', ') }}</div></div>
            </template>
            <template v-else>
              <p>{{ current.result?.answer }}</p>
              <div class="small muted" v-for="(s, i) in current.result?.sources ?? []" :key="i">{{ s.chat }} · {{ s.date }}</div>
            </template>
          </NCard>
        </div>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.reports {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 12px;
  margin-top: 12px;
}
.block {
  margin: 10px 0;
}
@media (max-width: 800px) {
  .reports {
    grid-template-columns: 1fr;
  }
}
</style>
