<script setup lang="ts">
import { NButton, NDatePicker, NDropdown, NEmpty, NSelect, NSpin, NTag, useMessage } from 'naive-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFormat } from '@/composables/format'
import { useRouter } from 'vue-router'
import { api, events } from '@/api/client'
import type { Chat, Message } from '@/api/types'
import ChatAvatar from '@/components/ChatAvatar.vue'
import ExportWizard from '@/components/ExportWizard.vue'
import MessageBubble from '@/components/MessageBubble.vue'
import { useAppStore } from '@/stores/app'

interface ChatDetail extends Chat {
  topics: { id: number; title: string }[]
  dir: string
  media_by_type: { type: string; n: number; done: number; bytes: number }[]
  exports: { format: string; path: string; parts: number; messages: number }[]
  phone: string | null
}

const props = defineProps<{ id: string }>()
const fmt = useFormat()
const { t } = useI18n()
const app = useAppStore()
const router = useRouter()
const message = useMessage()
const chatId = computed(() => Number(props.id))
const chat = ref<ChatDetail | null>(null)
const items = ref<Message[]>([])
const loading = ref(false)
const reachedTop = ref(false)
const reachedBottom = ref(true)
const typeFilter = ref<string | null>(null)
const topic = ref<number | null>(null)
const jumpDate = ref<number | null>(null)
const scroller = ref<HTMLElement | null>(null)
const wizard = ref(false)
const fetching = ref(false) // pulling the latest messages from Telegram on open
let fetchTimer: ReturnType<typeof setTimeout> | undefined
const olderCount = computed(() => (chat.value ? Math.max(0, (chat.value.total_messages ?? 0) - chat.value.stored_messages) : 0))
const hasOlderOnServer = computed(() => olderCount.value > 0)
const PAGE = 60
const MAX_IN_DOM = 600 // windowing: keep the DOM bounded for 100k+ message chats (docs/17)

const query = computed(() => ({ type: typeFilter.value ?? undefined, topic: topic.value ?? undefined }))

async function loadChat() {
  chat.value = await api.get<ChatDetail>(`/chats/${chatId.value}`)
}

async function loadLatest() {
  loading.value = true
  try {
    const r = await api.get<{ items: Message[] }>(`/chats/${chatId.value}/messages`, { limit: PAGE, ...query.value })
    items.value = r.items
    reachedTop.value = r.items.length < PAGE
    reachedBottom.value = true
  } finally {
    loading.value = false
  }
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function loadOlder() {
  if (loading.value || reachedTop.value || !items.value.length) return
  loading.value = true
  const el = scroller.value!
  const prevHeight = el.scrollHeight
  let r: { items: Message[] }
  try {
    r = await api.get<{ items: Message[] }>(`/chats/${chatId.value}/messages`, { before: items.value[0].id, limit: PAGE, ...query.value })
  } finally {
    loading.value = false
  }
  loading.value = true
  reachedTop.value = r.items.length < PAGE
  let next = [...r.items, ...items.value]
  if (next.length > MAX_IN_DOM) {
    next = next.slice(0, MAX_IN_DOM)
    reachedBottom.value = false
  }
  items.value = next
  loading.value = false
  await nextTick()
  el.scrollTop = el.scrollHeight - prevHeight + el.scrollTop
}

async function loadNewer() {
  if (loading.value || reachedBottom.value || !items.value.length) return
  loading.value = true
  let r: { items: Message[] }
  try {
    r = await api.get<{ items: Message[] }>(`/chats/${chatId.value}/messages`, { after: items.value[items.value.length - 1].id, limit: PAGE, ...query.value })
  } finally {
    loading.value = false
  }
  loading.value = true
  reachedBottom.value = r.items.length < PAGE
  let next = [...items.value, ...r.items]
  if (next.length > MAX_IN_DOM) {
    const el = scroller.value!
    const before = el.scrollHeight
    next = next.slice(next.length - MAX_IN_DOM)
    reachedTop.value = false
    items.value = next
    await nextTick()
    el.scrollTop -= before - el.scrollHeight
  } else items.value = next
  loading.value = false
}

async function jumpTo(params: Record<string, unknown>, highlight?: number) {
  loading.value = true
  let r: { items: Message[] }
  try {
    r = await api.get<{ items: Message[] }>(`/chats/${chatId.value}/messages`, { limit: PAGE * 2, ...query.value, ...params })
  } finally {
    loading.value = false
  }
  items.value = r.items
  reachedTop.value = false
  reachedBottom.value = false
  await nextTick()
  const target = highlight ?? (params.around as number | undefined) ?? r.items[Math.floor(r.items.length / 2)]?.id
  const el = document.getElementById(`m${target}`)
  el?.scrollIntoView({ block: 'center' })
  el?.classList.add('flash')
  setTimeout(() => el?.classList.remove('flash'), 1600)
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  if (el.scrollTop < 300) loadOlder()
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 300) loadNewer()
}

watch(jumpDate, (d) => d && jumpTo({ date: new Date(d).toISOString().slice(0, 10) }))
watch(query, loadLatest)
watch(chatId, init)

async function init() {
  await loadChat()
  const around = Number(router.currentRoute.value.query.msg)
  if (around) await jumpTo({ around }, around)
  else await loadLatest()
  fetchLatest()
}

// Opening a chat shows the newest messages straight from Telegram (up to 100 new ones); the full history stays a separate sync.
async function fetchLatest() {
  if (!app.auth?.authorized) return
  fetching.value = true
  clearTimeout(fetchTimer)
  fetchTimer = setTimeout(() => (fetching.value = false), 30000) // FloodWait or offline: don't spin forever
  try {
    await api.post(`/chats/${chatId.value}/preview`)
  } catch {
    fetching.value = false
  }
}
async function onPreview(ev: { data: { chat_id: number; fetched: number } }) {
  if (ev.data.chat_id !== chatId.value) return
  clearTimeout(fetchTimer)
  fetching.value = false
  if (!ev.data.fetched) return
  loadChat()
  if (!items.value.length) await loadLatest()
  else if (reachedBottom.value) await loadNewer().then(() => (reachedBottom.value = true))
}

async function downloadNow(mediaId: number) {
  await api.post(`/media/${mediaId}/download-now`)
  message.info(t('chat.downloadQueued'))
}
async function transcribe(mediaId: number) {
  await api.post('/transcribe', { media_ids: [mediaId] })
  message.info(t('chat.transcribeStarted'))
}
async function transcribeAll() {
  await api.post('/transcribe', { chat_id: chatId.value })
  message.info(t('chat.transcribeStarted'))
}
async function syncNow() {
  await api.post('/export', { chat_ids: [chatId.value], formats: ['md'], split: { mode: 'month' }, media_types: [], no_media: true, filters: {}, also_full: false, include_transcripts: true })
  message.info(t('export.started'))
}
const openFolder = () => chat.value && api.post('/open-path', { path: chat.value.dir }).catch(() => message.warning(t('chat.noFolder')))

const menu = computed(() => [
  { label: t('chat.syncNow'), key: 'sync' },
  ...(app.modules.whisper ? [{ label: t('chat.transcribeAll'), key: 'transcribe' }] : []),
  { label: t('chat.openFolder'), key: 'folder' },
])
function onMenu(k: string) {
  if (k === 'sync') syncNow()
  else if (k === 'transcribe') transcribeAll()
  else openFolder()
}

const showSender = computed(() => chat.value && !['user', 'saved', 'bot', 'channel'].includes(chat.value.type))
const typeOptions = computed(() => ['photo', 'video', 'round', 'voice', 'audio', 'document', 'sticker', 'gif', 'poll', 'geo'].map((v) => ({ label: t(`media.${v}`, v), value: v })))
const topicOptions = computed(() => (chat.value?.topics ?? []).map((tp) => ({ label: tp.title, value: tp.id })))
const dayOf = (m: Message) => (m.date ?? '').slice(0, 10)

let offs: (() => void)[] = []
onMounted(() => {
  init()
  offs = [
    events.on('chat.preview', (ev) => onPreview(ev as { data: { chat_id: number; fetched: number } })),
    events.on('new_message', (ev) => {
      if (ev.data.chat_id === chatId.value && reachedBottom.value) loadNewer().then(() => (reachedBottom.value = true))
    }),
    events.on('media.done', (ev) => {
      if (ev.data.chat_id !== chatId.value) return
      const m = items.value.find((x) => x.media_id === ev.data.id)
      if (m) m.media_status = 'done'
    }),
    events.on('transcript.done', (ev) => {
      if (ev.data.chat_id === chatId.value) {
        const m = items.value.find((x) => x.media_id === ev.data.media_id)
        if (m) api.get<{ items: Message[] }>(`/chats/${chatId.value}/messages`, { around: m.id, limit: 2 }).then((r) => {
          const fresh = r.items.find((x) => x.id === m.id)
          if (fresh) m.transcript = fresh.transcript
        })
      }
    }),
  ]
})
onBeforeUnmount(() => {
  offs.forEach((f) => f())
  clearTimeout(fetchTimer)
})
</script>

<template>
  <div class="wrap">
    <div class="head">
      <NButton quaternary circle @click="router.push('/chats')">←</NButton>
      <ChatAvatar v-if="chat" :id="chat.id" :title="chat.title" :size="36" />
      <div class="grow">
        <div class="row">
          <strong class="ellipsis">{{ chat?.title }}</strong>
          <NTag v-if="chat?.noforwards" size="small" type="warning">🔒 {{ t('chats.filters.protected') }}</NTag>
        </div>
        <div class="small muted">
          {{ chat ? t(`chatType.${chat.type}`) : '' }}<template v-if="chat?.username"> · @{{ chat.username }}</template>
          <template v-if="chat"> · {{ t('chats.stored', { n: fmt.n(chat.stored_messages), p: chat.total_messages ? Math.round((chat.stored_messages / chat.total_messages) * 100) : 0 }) }}</template>
        </div>
      </div>
      <NSelect v-if="topicOptions.length" v-model:value="topic" :options="topicOptions" clearable size="small" style="width: 170px" :placeholder="t('chat.topic')" />
      <NSelect v-model:value="typeFilter" :options="typeOptions" clearable size="small" style="width: 150px" :placeholder="t('chat.filterType')" />
      <NDatePicker v-model:value="jumpDate" type="date" size="small" clearable :placeholder="t('chat.jumpDate')" style="width: 150px" />
      <NButton type="primary" size="small" @click="wizard = true">⬇ {{ t('chat.export') }}</NButton>
      <NDropdown :options="menu" @select="onMenu"><NButton size="small">⋯</NButton></NDropdown>
    </div>
    <div ref="scroller" class="feed" @scroll.passive="onScroll">
      <div v-if="(loading || fetching) && !items.length" class="fetching">
        <NSpin size="small" />
        <span>{{ t('chat.fetchingLatest') }}</span>
      </div>
      <NEmpty v-else-if="!items.length" :description="t('chat.empty')" style="margin-top: 60px">
        <template #extra><NButton type="primary" @click="syncNow">{{ t('chat.syncNow') }}</NButton></template>
      </NEmpty>
      <div v-if="!reachedTop && items.length" class="small muted" style="text-align: center; padding: 8px">…</div>
      <div v-else-if="reachedTop && items.length && hasOlderOnServer" class="older">
        <span>{{ t('chat.olderNotLoaded', { n: fmt.n(olderCount) }) }}</span>
        <NButton size="small" type="primary" secondary @click="syncNow">{{ t('chat.loadAllHistory') }}</NButton>
      </div>
      <div v-if="fetching && items.length" class="fetch-pill"><NSpin :size="12" /> {{ t('chat.fetchingNew') }}</div>
      <template v-for="(m, i) in items" :key="m.id">
        <div v-if="i === 0 || dayOf(items[i - 1]) !== dayOf(m)" class="day"><span>{{ dayOf(m) }}</span></div>
        <MessageBubble :m="m" :show-sender="!!showSender" :whisper="app.modules.whisper" @download="downloadNow" @transcribe="transcribe" @jump="(id: number) => jumpTo({ around: id }, id)" />
      </template>
      <div v-if="!reachedBottom" style="text-align: center; padding: 8px"><NButton size="small" @click="loadLatest">↓ {{ t('chat.latest') }}</NButton></div>
    </div>
    <ExportWizard v-model:show="wizard" :chat-ids="[chatId]" />
  </div>
</template>

<style scoped>
.wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elev);
  flex-wrap: wrap;
}
.feed {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px 24px;
  contain: strict;
}
.day {
  text-align: center;
  margin: 12px 0 6px;
}
.day span {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  color: var(--text-2);
  border-radius: 10px;
  padding: 2px 10px;
  font-size: 12px;
}
:deep(.flash) {
  animation: flash 1.6s;
}
@keyframes flash {
  0%,
  60% {
    box-shadow: 0 0 0 3px #2aabee;
  }
}
.fetching { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 60px 0; color: var(--text-2); }
.older { display: flex; align-items: center; justify-content: center; gap: 12px; flex-wrap: wrap; margin: 8px auto 16px; padding: 10px 14px; max-width: 560px; border: 1px dashed var(--border-strong); border-radius: var(--radius); color: var(--text-2); font-size: 13px; }
.fetch-pill { position: sticky; top: 8px; z-index: 2; margin: 0 auto; width: fit-content; display: flex; align-items: center; gap: 8px; padding: 4px 12px; border-radius: 999px; background: var(--bg-elev); border: 1px solid var(--border); box-shadow: var(--shadow-sm); font-size: 12px; color: var(--text-2); }
</style>
