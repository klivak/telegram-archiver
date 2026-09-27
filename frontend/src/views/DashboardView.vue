<script setup lang="ts">
import { NButton, NProgress, useMessage } from 'naive-ui'
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api, events } from '@/api/client'
import type { Job } from '@/api/types'
import ChatAvatar from '@/components/ChatAvatar.vue'
import JobCard from '@/components/JobCard.vue'
import { useFormat } from '@/composables/format'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'
import { formatBytes, initials, shortDate } from '@/utils'

interface Dash {
  me: { first_name?: string; last_name?: string; username?: string } | null
  archive_root: string
  account_dir: string
  stats: Record<string, number>
  jobs: Job[]
  last_report: { id: number; kind: string; created_at: string } | null
}
interface Report {
  id: number
  kind: string
  created_at: string
  result?: { chats?: { chat: string; summary: string }[]; summary?: string; items?: { title?: string; text?: string }[] } & Record<string, unknown>
}

const { t, te, locale } = useI18n()
const fmt = useFormat()
const router = useRouter()
const message = useMessage()
const app = useAppStore()
const jobs = useJobsStore()
const chats = useChatsStore()
const data = ref<Dash | null>(null)
const digest = ref<Report | null>(null)

async function load() {
  data.value = await api.get<Dash>('/dashboard')
  const id = data.value.last_report?.id
  if (id && digest.value?.id !== id) {
    try {
      digest.value = await api.get<Report>(`/ai/reports/${id}`)
    } catch {
      digest.value = null
    }
  }
}

const s = computed(() => data.value?.stats ?? {})
const name = computed(() => data.value?.me?.first_name ?? app.auth?.me?.first_name ?? '')
const fullName = computed(() => [data.value?.me?.first_name, data.value?.me?.last_name].filter(Boolean).join(' ') || name.value || '?')
const greeting = computed(() => {
  const h = new Date().getHours()
  const k = h < 5 ? 'night' : h < 12 ? 'morning' : h < 18 ? 'day' : 'evening'
  return t(`dashboard.greet.${k}`, { name: name.value })
})
const pct = (a: number, b: number) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0)
const chatsPct = computed(() => pct(s.value.archived_chats ?? 0, s.value.chats ?? 0))
const mediaTotal = computed(() => (s.value.media_done ?? 0) + (s.value.media_pending ?? 0))
const mediaPct = computed(() => pct(s.value.media_done ?? 0, mediaTotal.value))

const kpis = computed(() => [
  { key: 'chats', icon: '💬', chip: '', value: fmt.n(s.value.chats), sub: t('dashboard.archivedChats', { n: fmt.n(s.value.archived_chats) }), bar: chatsPct.value, to: '/chats' },
  { key: 'messages', icon: '🗂️', chip: 'violet', value: fmt.compact(s.value.messages), title: fmt.n(s.value.messages), sub: t('dashboard.inChats', { n: fmt.n(s.value.archived_chats) }), bar: null, to: '/search' },
  { key: 'archiveSize', icon: '💾', chip: 'green', value: formatBytes((s.value.media_bytes ?? 0) + (s.value.db_bytes ?? 0)), sub: t('dashboard.mediaFiles', { n: fmt.n(s.value.media_done) }), bar: mediaTotal.value ? mediaPct.value : null, to: '/downloads' },
  { key: 'unread', icon: '🔔', chip: 'amber', value: fmt.compact(s.value.unread), title: fmt.n(s.value.unread), sub: t('dashboard.unreadChats', { n: fmt.n(s.value.unread_chats) }), bar: null, to: '/monitor' },
])

const topUnread = computed(() => [...chats.items].filter((c) => c.unread_count > 0).sort((a, b) => b.unread_count - a.unread_count).slice(0, 5))
const history = computed(() => jobs.list.filter((j) => ['done', 'failed', 'cancelled'].includes(j.status)).slice(0, 5))
const digestLines = computed(() => {
  const r = digest.value?.result
  if (!r) return []
  if (Array.isArray(r.chats)) return r.chats.slice(0, 3).map((c) => ({ title: c.chat, text: c.summary }))
  if (typeof r.summary === 'string') return [{ title: '', text: r.summary }]
  return []
})
const jobTitle = (j: Job) => (te(`jobs.kind.${j.kind}`) ? t(`jobs.kind.${j.kind}`) : j.title)
const STATUS_ICON: Record<string, string> = { done: '✓', failed: '!', cancelled: '–' }

async function exportSaved() {
  const saved = chats.items.find((c) => c.type === 'saved')
  if (!saved) return
  await api.post('/export', { chat_ids: [saved.id], formats: ['md', 'html'], split: { mode: 'month' }, media_types: [], no_media: true, filters: {}, also_full: false, include_transcripts: true })
  router.push('/downloads')
}
async function runDigest() {
  if (!app.settings?.ai.enabled) {
    router.push({ path: '/settings', query: { tab: 'ai' } })
    return
  }
  await api.post('/ai/run', { task: 'digest', scope: { unread: true }, refresh_unread: true })
  message.info(t('monitor.aiStarted'))
}

const openFolder = (path: string) => api.post('/open-path', { path })
const quick = computed(() => [
  { icon: '👤', chip: '', title: t('dashboard.quickPrivate'), desc: t('dashboard.quickPrivateDesc'), run: () => router.push({ path: '/chats', query: { filter: 'private' } }) },
  { icon: '⭐', chip: 'amber', title: t('dashboard.quickSaved'), desc: t('dashboard.quickSavedDesc'), run: exportSaved },
  { icon: '🔎', chip: 'violet', title: t('dashboard.quickSearch'), desc: t('dashboard.quickSearchDesc'), run: () => router.push('/search') },
  { icon: '📂', chip: 'green', title: t('dashboard.openArchive'), desc: data.value?.account_dir ?? '', run: () => data.value && openFolder(data.value.account_dir) },
])

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
  <div class="page dash">
    <header class="hero rise">
      <div class="hero-avatar">{{ initials(fullName) }}</div>
      <div class="grow">
        <h1>{{ greeting }}</h1>
        <p class="muted">{{ t('dashboard.subtitle') }}</p>
      </div>
      <div class="row hero-actions">
        <NButton :loading="chats.loading" @click="chats.refresh()">↻ {{ t('dashboard.refreshChats') }}</NButton>
        <NButton type="primary" @click="router.push('/chats')">⬇ {{ t('dashboard.exportChats') }}</NButton>
      </div>
    </header>

    <!-- KPI cards -->
    <section class="kpis">
      <template v-if="!data">
        <div v-for="n in 4" :key="n" class="skeleton" style="height: 132px"></div>
      </template>
      <template v-else>
        <button v-for="(k, i) in kpis" :key="k.key" class="kpi surface lift rise" :style="{ animationDelay: `${i * 40}ms` }" @click="router.push(k.to)">
          <div class="row">
            <span class="kpi-label grow">{{ t(`dashboard.${k.key}`) }}</span>
            <span class="icon-chip sm" :class="k.chip">{{ k.icon }}</span>
          </div>
          <div class="kpi-value num" :title="k.title">{{ k.value }}</div>
          <div class="small muted ellipsis">{{ k.sub }}</div>
          <div v-if="k.bar !== null" class="bar"><span :style="{ width: `${k.bar}%` }"></span></div>
        </button>
      </template>
    </section>

    <div class="grid">
      <!-- left column -->
      <div class="col">
        <section class="card surface">
          <div class="card-head">
            <div class="grow">
              <h2>{{ t('dashboard.archiveProgress') }}</h2>
              <p>{{ t('dashboard.archiveProgressDesc') }}</p>
            </div>
          </div>
          <div v-if="data" class="progress-rows">
            <div>
              <div class="row small"><span class="grow">{{ t('dashboard.chatsArchived') }}</span><span class="num">{{ fmt.n(s.archived_chats) }} / {{ fmt.n(s.chats) }}</span></div>
              <NProgress type="line" :percentage="chatsPct" :show-indicator="false" :height="8" />
            </div>
            <div>
              <div class="row small"><span class="grow">{{ t('dashboard.mediaDownloaded') }}</span><span class="num">{{ fmt.n(s.media_done) }} / {{ fmt.n(mediaTotal) }}</span></div>
              <NProgress type="line" :percentage="mediaPct" :show-indicator="false" :height="8" status="success" />
            </div>
            <div class="mini-stats">
              <div><div class="num strong">{{ fmt.n(s.messages) }}</div><div class="small muted">{{ t('dashboard.messages') }}</div></div>
              <div><div class="num strong">{{ fmt.n(s.transcripts) }}</div><div class="small muted">{{ t('dashboard.transcripts') }}</div></div>
              <div><div class="num strong">{{ fmt.n(s.mini_apps) }}</div><div class="small muted">{{ t('nav.miniapps') }}</div></div>
            </div>
            <div v-if="!s.archived_chats" class="empty-cta">
              <span class="muted small grow">{{ t('dashboard.emptyArchive') }}</span>
              <NButton size="small" type="primary" @click="router.push('/chats')">{{ t('dashboard.exportChats') }}</NButton>
            </div>
          </div>
          <div v-else class="skeleton" style="height: 120px"></div>
        </section>

        <section class="card surface">
          <div class="card-head">
            <div class="grow">
              <h2>{{ t('dashboard.activeJobs') }}</h2>
              <p>{{ t('dashboard.activeJobsDesc') }}</p>
            </div>
            <NButton size="small" quaternary @click="router.push('/downloads')">{{ t('dashboard.viewAll') }} →</NButton>
          </div>
          <TransitionGroup v-if="jobs.active.length" name="fade" tag="div" class="jobs">
            <JobCard v-for="j in jobs.active.slice(0, 4)" :key="j.id" :job="j" />
          </TransitionGroup>
          <div v-else class="empty">
            <span class="icon-chip">✨</span>
            <div class="grow">
              <div>{{ t('dashboard.noJobs') }}</div>
              <div class="small muted">{{ t('dashboard.noJobsHint') }}</div>
            </div>
            <NButton size="small" @click="router.push('/chats')">{{ t('dashboard.exportChats') }}</NButton>
          </div>
          <template v-if="history.length">
            <div class="sub-head small muted">{{ t('dashboard.recent') }}</div>
            <div v-for="j in history" :key="j.id" class="hist hover-row" @click="router.push('/downloads')">
              <span class="st" :class="j.status">{{ STATUS_ICON[j.status] }}</span>
              <span class="grow ellipsis">{{ jobTitle(j) }}<span v-if="j.progress?.chat_title" class="muted"> · {{ j.progress.chat_title }}</span></span>
              <span class="small muted">{{ shortDate(j.updated_at, locale) }}</span>
            </div>
          </template>
        </section>
      </div>

      <!-- right column -->
      <div class="col">
        <section class="card surface digest">
          <div class="card-head">
            <span class="icon-chip violet">🤖</span>
            <div class="grow">
              <h2>{{ t('dashboard.lastDigest') }}</h2>
              <p>{{ digest ? shortDate(digest.created_at, locale) : t('dashboard.digestDesc') }}</p>
            </div>
          </div>
          <template v-if="digestLines.length">
            <div v-for="(l, i) in digestLines" :key="i" class="digest-item">
              <div v-if="l.title" class="strong ellipsis">{{ l.title }}</div>
              <div class="small clamp">{{ l.text }}</div>
            </div>
          </template>
          <div v-else class="small muted" style="margin: 4px 0 12px">{{ app.settings?.ai.enabled ? t('dashboard.noDigest') : t('dashboard.aiOff') }}</div>
          <div class="row">
            <NButton size="small" type="primary" @click="runDigest">{{ app.settings?.ai.enabled ? t('dashboard.runDigest') : t('dashboard.enableAi') }}</NButton>
            <NButton v-if="data?.last_report" size="small" quaternary @click="router.push({ path: '/monitor', query: { report: data.last_report.id } })">{{ t('common.open') }} →</NButton>
          </div>
        </section>

        <section class="card surface">
          <div class="card-head">
            <div class="grow">
              <h2>{{ t('dashboard.topUnread') }}</h2>
              <p>{{ t('dashboard.topUnreadDesc') }}</p>
            </div>
            <NButton size="small" quaternary @click="router.push('/monitor')">{{ t('dashboard.viewAll') }} →</NButton>
          </div>
          <div v-if="!chats.loaded" class="skeleton" style="height: 160px"></div>
          <div v-else-if="!topUnread.length" class="empty">
            <span class="icon-chip green">🎉</span>
            <span class="grow small muted">{{ t('monitor.allRead') }}</span>
          </div>
          <div v-for="c in topUnread" v-else :key="c.id" class="unread hover-row" @click="router.push(`/chats/${c.id}`)">
            <ChatAvatar :id="c.id" :title="c.title" :size="32" />
            <div class="grow">
              <div class="ellipsis strong">{{ c.title }}</div>
              <div class="small muted">{{ t(`chatType.${c.type}`) }}</div>
            </div>
            <span class="pill num" :title="fmt.n(c.unread_count)">{{ fmt.compact(c.unread_count) }}</span>
          </div>
        </section>
      </div>
    </div>

    <div class="section-head">
      <div class="grow">
        <h2>{{ t('dashboard.quick') }}</h2>
        <p>{{ t('dashboard.quickDesc') }}</p>
      </div>
    </div>
    <section class="quick">
      <button v-for="q in quick" :key="q.title" class="qa surface lift" @click="q.run()">
        <span class="icon-chip" :class="q.chip">{{ q.icon }}</span>
        <span class="grow">
          <span class="qa-title">{{ q.title }}</span>
          <span class="small muted ellipsis qa-desc">{{ q.desc }}</span>
        </span>
        <span class="muted arrow">→</span>
      </button>
    </section>
  </div>
</template>

<style scoped>
.hero {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}
.hero h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.02em;
}
.hero p {
  margin: 4px 0 0;
  font-size: 14px;
}
.hero-avatar {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 650;
  font-size: 18px;
  color: #fff;
  background: linear-gradient(135deg, var(--tga-blue), var(--tga-navy));
  box-shadow: 0 0 0 4px var(--accent-soft);
  flex: none;
}
.hero-actions {
  flex-wrap: wrap;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 18px;
  text-align: left;
  font: inherit;
  color: inherit;
  cursor: pointer;
  min-width: 0;
}
.kpi-label {
  font-size: 13px;
  color: var(--text-2);
  font-weight: 500;
}
.kpi-value {
  font-size: 30px;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.15;
  margin-top: 4px;
}
.bar {
  height: 4px;
  border-radius: 4px;
  background: var(--bg-sunken);
  overflow: hidden;
  margin-top: 6px;
}
.bar span {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--tga-blue);
  transition: width 600ms var(--ease);
}
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.card {
  padding: 18px 20px;
}
.card-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}
.card-head h2,
.section-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.card-head p {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--text-3);
}
.progress-rows {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.progress-rows .row {
  margin-bottom: 6px;
}
.mini-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.strong {
  font-weight: 600;
}
.mini-stats .strong {
  font-size: 18px;
}
.empty-cta,
.empty {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px dashed var(--border-strong);
}
.jobs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.sub-head {
  margin: 16px 0 6px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 11px;
}
.hist {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  cursor: pointer;
  font-size: 13.5px;
}
.st {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  flex: none;
  background: var(--bg-sunken);
  color: var(--text-3);
}
.st.done {
  background: var(--success-soft);
  color: var(--success);
}
.st.failed {
  background: var(--danger-soft);
  color: var(--danger);
}
.digest {
  background: linear-gradient(160deg, var(--violet-soft), transparent 55%), var(--bg-elev);
}
.digest-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.digest-item:last-of-type {
  border-bottom: 0;
  margin-bottom: 10px;
}
.clamp {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--text-2);
  margin-top: 2px;
}
.unread {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  cursor: pointer;
}
.pill {
  min-width: 28px;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
  text-align: center;
}
.section-head p {
  margin: 2px 0 0;
}
.quick {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.qa {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  min-width: 0;
}
.qa-title {
  display: block;
  font-weight: 600;
  font-size: 14px;
}
.qa-desc {
  display: block;
  margin-top: 2px;
}
.arrow {
  transition: transform var(--dur) var(--ease);
}
.qa:hover .arrow {
  transform: translateX(3px);
  color: var(--accent);
}
@media (max-width: 1180px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 980px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
