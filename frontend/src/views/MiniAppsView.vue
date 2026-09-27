<script setup lang="ts">
import { NAlert, NButton, NCard, NCode, NDrawer, NDrawerContent, NEmpty, NImage, NInputNumber, NModal, NSpace, NSwitch, NTag, useMessage } from 'naive-ui'
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
const auto = ref({ max_depth: 2, max_clicks: 30, video: false })

async function load() {
  items.value = (await api.get<{ items: MiniApp[] }>('/miniapps')).items
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
  <div class="page" data-tour="page">
    <div class="page-header">
      <h1>{{ t('nav.miniapps') }}</h1>
      <NButton type="primary" @click="detect">🔍 {{ t('miniapps.detect') }}</NButton>
    </div>
    <NAlert v-if="!app.modules.playwright" type="info" :title="t('miniapps.installTitle')" style="margin-bottom: 12px">
      {{ t('miniapps.installText') }}
      <NCode code="cd backend; uv sync --extra miniapps; uv run playwright install chromium" language="powershell" style="margin-top: 6px" />
    </NAlert>
    <NAlert type="warning" :show-icon="false" style="margin-bottom: 12px" class="small">{{ t('miniapps.warning') }}</NAlert>

    <NCard v-for="j in sessions" :key="j.id" size="small" style="margin-bottom: 10px">
      <div class="row">
        <span class="grow">🔴 {{ t('miniapps.recording', { n: j.progress.states ?? 0 }) }}</span>
        <NButton size="small" @click="snapNow(j.id)">📸 {{ t('miniapps.snapNow') }}</NButton>
        <NButton size="small" @click="jobs.action(j.id, 'cancel')">⏹ {{ t('miniapps.stop') }}</NButton>
      </div>
    </NCard>

    <NEmpty v-if="!items.length" :description="t('miniapps.empty')" style="margin-top: 40px" />
    <div class="grid-cards" style="grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))">
      <NCard v-for="a in items" :key="a.id" size="small">
        <div class="row">
          <strong class="grow ellipsis">🧩 {{ a.title || a.short_name || a.bot_username }}</strong>
          <NTag size="small">{{ t(`miniapps.kind.${a.kind}`, a.kind) }}</NTag>
        </div>
        <div class="small muted ellipsis">@{{ a.bot_username ?? a.bot_id }}<template v-if="a.chat_title"> · {{ t('miniapps.foundIn', { chat: a.chat_title }) }}</template></div>
        <div class="small muted ellipsis" v-if="a.url">{{ a.url }}</div>
        <NSpace size="small" style="margin-top: 8px">
          <NButton size="small" type="primary" :disabled="!app.modules.playwright" @click="open(a, 'manual')">▶ {{ t('miniapps.openRecord') }}</NButton>
          <NButton size="small" :disabled="!app.modules.playwright" @click="autoModal = a">🤖 {{ t('miniapps.autoCrawl') }}</NButton>
          <NButton size="small" quaternary @click="showSnapshots(a)">🗂 {{ t('miniapps.snapshots', { n: a.snapshots }) }}</NButton>
        </NSpace>
      </NCard>
    </div>

    <NModal :show="!!autoModal" preset="card" :title="t('miniapps.autoCrawl')" style="width: 440px" @update:show="(v: boolean) => !v && (autoModal = null)">
      <p class="small">{{ t('miniapps.autoText') }}</p>
      <NSpace vertical>
        <div class="row"><span class="grow">{{ t('miniapps.maxDepth') }}</span><NInputNumber v-model:value="auto.max_depth" :min="1" :max="5" style="width: 110px" /></div>
        <div class="row"><span class="grow">{{ t('miniapps.maxClicks') }}</span><NInputNumber v-model:value="auto.max_clicks" :min="1" :max="200" style="width: 110px" /></div>
        <div class="row"><span class="grow">{{ t('miniapps.video') }}</span><NSwitch v-model:value="auto.video" /></div>
      </NSpace>
      <NButton type="primary" block style="margin-top: 12px" @click="autoModal && open(autoModal, 'auto')">{{ t('miniapps.startCrawl') }}</NButton>
    </NModal>

    <NDrawer :show="!!drawer" :width="720" @update:show="(v: boolean) => !v && (drawer = null)">
      <NDrawerContent :title="drawer?.title ?? ''" closable>
        <template v-if="!states">
          <NEmpty v-if="!snapshots.length" :description="t('miniapps.noSnapshots')" />
          <NCard v-for="s in snapshots" :key="s.id" size="small" style="margin-bottom: 8px">
            <div class="row">
              <span class="grow">{{ s.created_at.slice(0, 16).replace('T', ' ') }} · {{ t(`miniapps.mode.${s.mode}`) }} · {{ t('miniapps.states', { n: s.states }) }}</span>
              <NButton size="small" @click="showStates(s)">{{ t('common.open') }}</NButton>
              <NButton size="small" quaternary @click="openFolder(s.path)">📂</NButton>
              <NButton size="small" quaternary :disabled="!app.modules.playwright" @click="replay(s)">⟲ {{ t('miniapps.replay') }}</NButton>
            </div>
          </NCard>
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
.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
</style>
