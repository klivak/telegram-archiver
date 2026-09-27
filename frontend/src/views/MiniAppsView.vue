<script setup lang="ts">
import { NAlert, NButton, NCard, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NEmpty, NImage, NInputNumber, NModal, NSpace, NSwitch, NTag, useMessage } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, events } from '@/api/client'
import type { MiniApp } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useJobsStore } from '@/stores/jobs'

interface Snapshot {
  id: number
  path: string
  mode: string
  states: number
  created_at: string
}
interface State {
  n: string
  url: string
  label: string
  png: string
  mhtml: string | null
  html: string
}

const { t } = useI18n()
const app = useAppStore()
const jobs = useJobsStore()
const message = useMessage()
const items = ref<MiniApp[]>([])
const drawer = ref<MiniApp | null>(null)
const snapshots = ref<Snapshot[]>([])
const states = ref<{ snap: Snapshot; list: State[] } | null>(null)
const autoModal = ref<MiniApp | null>(null)
const PRESETS = { full: { max_depth: 4, max_clicks: 400 }, quick: { max_depth: 2, max_clicks: 30 } } as const
const preset = ref<'full' | 'quick' | 'custom'>('full')
const auto = ref({ max_depth: 4, max_clicks: 400, video: false })
function pickPreset(k: 'full' | 'quick') {
  preset.value = k
  auto.value = { ...auto.value, ...PRESETS[k] }
}

const loaded = ref(false)
async function load() {
  items.value = (await api.get<{ items: MiniApp[] }>('/miniapps')).items
  if (!loaded.value && !items.value.length) expanded.value = ['discover', 'install', 'modes']
  loaded.value = true
}

// Guide sections; each has p1..pN paragraphs in i18n (miniapps.guide.<key>.pN).
const guide = [
  { key: 'discover', icon: '🔍', n: 4 },
  { key: 'capture', icon: '📸', n: 5 },
  { key: 'install', icon: '📦', n: 3 },
  { key: 'modes', icon: '🧭', n: 4 },
  { key: 'results', icon: '🗂️', n: 4 },
  { key: 'privacy', icon: '🔒', n: 4 },
]
const expanded = ref<string[]>(['discover'])
const INSTALL = ['cd backend', 'uv sync --extra miniapps', 'uv run playwright install chromium']
async function copyInstall() {
  await navigator.clipboard?.writeText(INSTALL.join('; '))
  message.success(t('miniapps.copied'))
}
async function detect() {
  await api.post('/miniapps/detect')
  message.info(t('miniapps.detecting'))
}
async function open(a: MiniApp, mode: 'manual' | 'auto') {
  const body = mode === 'auto' ? { mode, ...auto.value } : { mode }
  await api.post(`/miniapps/${a.id}/open`, body)
  autoModal.value = null
  message.info(mode === 'manual' ? t('miniapps.manualStarted') : t('miniapps.autoStarted'))
}
async function showSnapshots(a: MiniApp) {
  drawer.value = a
  states.value = null
  snapshots.value = (await api.get<{ items: Snapshot[] }>(`/miniapps/${a.id}/snapshots`)).items
}
async function showStates(s: Snapshot) {
  const r = await api.get<{ states: State[] }>(`/miniapps/snapshots/${s.id}/states`)
  states.value = { snap: s, list: r.states }
}
const fileUrl = (sid: number, name: string) => api.fileUrl(`/miniapps/snapshots/${sid}/file`, { name: `states/${name}` })
const openFolder = (p: string) => api.post('/open-path', { path: p })
const replay = (s: Snapshot) => api.post(`/miniapps/snapshots/${s.id}/replay`).then(() => message.info(t('miniapps.replayStarted')))

const sessions = computed(() => jobs.active.filter((j) => j.kind === 'miniapp_session' && j.status === 'running'))
const snapNow = (jobId: number) => api.post(`/miniapps/sessions/${jobId}/snapshot`).then(() => message.success(t('miniapps.snapQueued')))

let off: (() => void) | undefined
onMounted(() => {
  load()
  off = events.on('miniapps.changed', load)
})
onBeforeUnmount(() => off?.())
</script>

<template>
  <div class="page">
    <div class="page-header" data-tour="page">
      <div class="grow">
        <h1>{{ t('nav.miniapps') }}</h1>
        <p class="page-desc">{{ t('pageDesc.miniapps') }}</p>
      </div>
      <NButton type="primary" @click="detect">🔍 {{ t('miniapps.detect') }}</NButton>
    </div>

    <div class="layout">
      <div class="main-col">
        <!-- module status -->
        <div class="module surface" :class="{ ok: app.modules.playwright }">
          <span class="icon-chip" :class="app.modules.playwright ? 'green' : 'amber'">{{ app.modules.playwright ? '✓' : '📦' }}</span>
          <div class="grow">
            <div class="strong">{{ app.modules.playwright ? t('miniapps.moduleReady') : t('miniapps.installTitle') }}</div>
            <div class="small muted">{{ app.modules.playwright ? t('miniapps.moduleReadyText') : t('miniapps.installText') }}</div>
            <div v-if="!app.modules.playwright" class="cmds">
              <code v-for="c in INSTALL" :key="c">{{ c }}</code>
            </div>
          </div>
          <NButton v-if="!app.modules.playwright" size="small" @click="copyInstall">⧉ {{ t('miniapps.copy') }}</NButton>
        </div>

        <NCard v-for="j in sessions" :key="j.id" size="small" class="rec">
          <div class="row">
            <span class="pulse"></span>
            <span class="grow">{{ t('miniapps.recording', { n: j.progress.states ?? 0 }) }}</span>
            <NButton size="small" @click="snapNow(j.id)">📸 {{ t('miniapps.snapNow') }}</NButton>
            <NButton size="small" @click="jobs.action(j.id, 'cancel')">⏹ {{ t('miniapps.stop') }}</NButton>
          </div>
        </NCard>

        <div class="section-head">
          <div class="grow">
            <h2>{{ t('miniapps.found', { n: items.length }) }}</h2>
            <p>{{ t('miniapps.foundDesc') }}</p>
          </div>
        </div>

        <div v-if="!loaded" class="apps">
          <div v-for="n in 4" :key="n" class="skeleton" style="height: 128px"></div>
        </div>
        <div v-else-if="!items.length" class="empty surface">
          <span class="icon-chip violet">🧩</span>
          <h3>{{ t('miniapps.emptyTitle') }}</h3>
          <p class="muted">{{ t('miniapps.empty') }}</p>
          <div class="row" style="justify-content: center">
            <NButton type="primary" @click="detect">🔍 {{ t('miniapps.detect') }}</NButton>
            <NButton @click="$router.push('/chats')">{{ t('miniapps.syncBots') }}</NButton>
          </div>
        </div>
        <TransitionGroup v-else name="fade" tag="div" class="apps">
          <div v-for="a in items" :key="a.id" class="app surface lift">
            <div class="row">
              <span class="icon-chip">🧩</span>
              <div class="grow">
                <div class="strong ellipsis">{{ a.title || a.short_name || a.bot_username }}</div>
                <div class="small muted ellipsis">@{{ a.bot_username ?? a.bot_id }}<template v-if="a.chat_title"> · {{ t('miniapps.foundIn', { chat: a.chat_title }) }}</template></div>
              </div>
              <NTag size="small" round>{{ t(`miniapps.kind.${a.kind}`, a.kind) }}</NTag>
            </div>
            <div v-if="a.url" class="small muted ellipsis url">{{ a.url }}</div>
            <div class="row actions">
              <NButton size="small" type="primary" :disabled="!app.modules.playwright" @click="open(a, 'manual')">▶ {{ t('miniapps.openRecord') }}</NButton>
              <NButton size="small" :disabled="!app.modules.playwright" @click="autoModal = a">🤖 {{ t('miniapps.autoCrawl') }}</NButton>
              <NButton size="small" quaternary @click="showSnapshots(a)">🗂 {{ t('miniapps.snapshots', { n: a.snapshots }) }}</NButton>
            </div>
          </div>
        </TransitionGroup>
      </div>

      <!-- how it works -->
      <aside class="guide surface">
        <div class="guide-head">
          <span class="icon-chip sm">📘</span>
          <div class="grow">
            <div class="strong">{{ t('miniapps.guide.title') }}</div>
            <div class="small muted">{{ t('miniapps.guide.subtitle') }}</div>
          </div>
        </div>
        <NCollapse v-model:expanded-names="expanded" arrow-placement="right">
          <NCollapseItem v-for="(g, i) in guide" :key="g.key" :name="g.key">
            <template #header>
              <span class="g-title"><span class="g-num num">{{ i + 1 }}</span>{{ g.icon }} {{ t(`miniapps.guide.${g.key}.title`) }}</span>
            </template>
            <ul class="g-list">
              <li v-for="n in g.n" :key="n">{{ t(`miniapps.guide.${g.key}.p${n}`) }}</li>
            </ul>
            <div v-if="g.key === 'install'" class="cmds">
              <code v-for="c in INSTALL" :key="c">{{ c }}</code>
            </div>
            <div v-if="g.key === 'results'" class="cmds">
              <code>mini_apps/&lt;bot&gt;/&lt;app&gt;/&lt;YYYY-MM-DD_HHMMSS&gt;/</code>
              <code>uv run playwright show-trace trace.zip</code>
            </div>
          </NCollapseItem>
        </NCollapse>
        <NAlert type="warning" :show-icon="false" class="small" style="margin-top: 14px">{{ t('miniapps.warning') }}</NAlert>
      </aside>
    </div>

    <NModal :show="!!autoModal" preset="card" :title="t('miniapps.autoCrawl')" style="width: 460px" @update:show="(v: boolean) => !v && (autoModal = null)">
      <div class="presets">
        <button type="button" class="preset" :class="{ on: preset === 'full' }" @click="pickPreset('full')">
          <strong>🌐 {{ t('miniapps.presetFull') }}</strong><span class="small muted">{{ t('miniapps.presetFullHint') }}</span>
        </button>
        <button type="button" class="preset" :class="{ on: preset === 'quick' }" @click="pickPreset('quick')">
          <strong>⚡ {{ t('miniapps.presetQuick') }}</strong><span class="small muted">{{ t('miniapps.presetQuickHint') }}</span>
        </button>
      </div>
      <p class="small">{{ t('miniapps.autoText') }}</p>
      <p class="small muted">{{ t('miniapps.autoResult') }}</p>
      <NSpace vertical>
        <div class="row"><span class="grow">{{ t('miniapps.maxDepth') }}</span><NInputNumber v-model:value="auto.max_depth" :min="1" :max="8" style="width: 110px" @update:value="preset = 'custom'" /></div>
        <div class="row"><span class="grow">{{ t('miniapps.maxClicks') }}</span><NInputNumber v-model:value="auto.max_clicks" :min="1" :max="2000" style="width: 110px" @update:value="preset = 'custom'" /></div>
        <div class="row"><span class="grow">{{ t('miniapps.video') }}</span><NSwitch v-model:value="auto.video" /></div>
      </NSpace>
      <NButton type="primary" block style="margin-top: 14px" @click="autoModal && open(autoModal, 'auto')">{{ t('miniapps.startCrawl') }}</NButton>
    </NModal>

    <NDrawer :show="!!drawer" :width="720" @update:show="(v: boolean) => !v && (drawer = null)">
      <NDrawerContent :title="drawer?.title ?? ''" closable>
        <template v-if="!states">
          <NEmpty v-if="!snapshots.length" :description="t('miniapps.noSnapshots')" />
          <div v-for="s in snapshots" :key="s.id" class="snap hover-row">
            <span class="icon-chip sm">{{ s.mode === 'auto' ? '🤖' : '▶' }}</span>
            <span class="grow">{{ s.created_at.slice(0, 16).replace('T', ' ') }} · {{ t(`miniapps.mode.${s.mode}`) }} · {{ t('miniapps.states', { n: s.states }) }}</span>
            <NButton size="small" @click="showStates(s)">{{ t('common.open') }}</NButton>
            <NButton size="small" quaternary :title="t('miniapps.siteText')" @click="openFolder(s.path + '/site.md')">📄</NButton>
            <NButton size="small" quaternary :title="t('miniapps.siteIndex')" @click="openFolder(s.path + '/index.html')">🌐</NButton>
            <NButton size="small" quaternary :title="s.path" @click="openFolder(s.path)">📂</NButton>
            <NButton size="small" quaternary :disabled="!app.modules.playwright" @click="replay(s)">⟲ {{ t('miniapps.replay') }}</NButton>
          </div>
        </template>
        <template v-else>
          <NButton size="small" @click="states = null">← {{ t('common.back') }}</NButton>
          <div class="gallery">
            <NCard v-for="st in states.list" :key="st.n" size="small">
              <NImage :src="fileUrl(states.snap.id, st.png)" width="100%" object-fit="cover" style="max-height: 300px" />
              <div class="small ellipsis">{{ st.n }} · {{ st.label }}</div>
              <div class="small muted ellipsis">{{ st.url }}</div>
              <NSpace size="small">
                <a v-if="st.mhtml" :href="fileUrl(states.snap.id, st.mhtml)" target="_blank" class="small">MHTML</a>
                <a :href="fileUrl(states.snap.id, st.html)" target="_blank" class="small">HTML</a>
              </NSpace>
            </NCard>
          </div>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 420px);
  gap: 20px;
  align-items: start;
}
.main-col {
  min-width: 0;
}
.strong {
  font-weight: 600;
}
.module {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 16px 18px;
  border-color: var(--warning-soft);
}
.module.ok {
  border-color: var(--border);
  align-items: center;
}
.cmds {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
}
.cmds code {
  user-select: all;
  font-size: 12.5px;
  padding: 4px 8px;
}
.rec {
  margin-top: 12px;
}
.pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--danger);
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
  50% {
    box-shadow: 0 0 0 6px var(--danger-soft);
  }
}
.apps {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}
.app {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.url {
  font-family: var(--mono);
  font-size: 11.5px;
}
.actions {
  flex-wrap: wrap;
  margin-top: auto;
}
.empty {
  text-align: center;
  padding: 36px 24px;
  border-style: dashed;
}
.empty h3 {
  margin: 12px 0 4px;
}
.empty p {
  max-width: 460px;
  margin: 0 auto 16px;
  font-size: 13.5px;
}
.guide {
  padding: 18px 18px 16px;
  position: sticky;
  top: 16px;
}
.guide-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.g-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13.5px;
}
.g-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-grid;
  place-items: center;
  font-size: 11px;
  background: var(--accent-soft);
  color: var(--accent);
}
.g-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-2);
}
.g-list li + li {
  margin-top: 4px;
}
.snap {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--border);
}
.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
@media (max-width: 1180px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .guide {
    position: static;
  }
}
.presets { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
.preset { font: inherit; text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg-sunken); color: var(--text); cursor: pointer; transition: all var(--dur) var(--ease); }
.preset:hover { border-color: var(--border-strong); }
.preset.on { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 1px var(--accent) inset; }
</style>
