<script setup lang="ts">
import { NButton, NCheckbox, NColorPicker, NDropdown, NEmpty, NForm, NFormItem, NInput, NModal, NSelect, NTag, NTooltip, NVirtualList, useDialog, useMessage } from 'naive-ui'
import { computed, onActivated, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { Chat, Folder } from '@/api/types'
import ChatAvatar from '@/components/ChatAvatar.vue'
import ExportWizard from '@/components/ExportWizard.vue'
import { FILTERS, matchFilter, useChatsStore, type ChatFilter } from '@/stores/chats'
import { useAppStore } from '@/stores/app'
import { shortDate } from '@/utils'

defineOptions({ name: 'ChatsView' })
const { t, locale } = useI18n()
const store = useChatsStore()
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const filter = ref<ChatFilter>((route.query.filter as ChatFilter) || 'all')
const folderId = ref<number | null>(null)
const q = ref('')
const sort = ref<'recent' | 'title' | 'unread' | 'size'>('recent')
const selected = ref<Set<number>>(new Set())
const lastClicked = ref<number | null>(null)
const wizard = ref(false)
const folderModal = ref(false)
const folderForm = ref<{ id?: number; name: string; emoji: string; color: string | null; parent_id: number | null }>({ name: '', emoji: '', color: null, parent_id: null })

watch(() => route.query.filter, (f) => f && (filter.value = f as ChatFilter))
onActivated(() => store.start())

const counts = computed(() => {
  const m: Record<string, number> = {}
  for (const f of FILTERS) m[f] = 0
  for (const c of store.items) for (const f of FILTERS) if (matchFilter(c, f)) m[f]++
  return m
})

const visible = computed<Chat[]>(() => {
  const s = q.value.trim().toLowerCase()
  let list = store.items.filter((c) => matchFilter(c, filter.value))
  if (folderId.value !== null) {
    const sub = new Set([folderId.value, ...store.folders.filter((f) => f.parent_id === folderId.value).map((f) => f.id)])
    list = list.filter((c) => c.folders.some((f) => sub.has(f)))
  }
  if (s) list = list.filter((c) => c.title.toLowerCase().includes(s) || (c.username ?? '').toLowerCase().includes(s))
  const sorted = [...list]
  if (sort.value === 'title') sorted.sort((a, b) => a.title.localeCompare(b.title))
  else if (sort.value === 'unread') sorted.sort((a, b) => b.unread_count - a.unread_count)
  else if (sort.value === 'size') sorted.sort((a, b) => b.stored_messages - a.stored_messages)
  return sorted
})

const userFolders = computed(() => store.folders.filter((f) => f.source === 'user' && !f.parent_id))
const tgFolders = computed(() => store.folders.filter((f) => f.source === 'telegram'))
const childrenOf = (id: number) => store.folders.filter((f) => f.parent_id === id)

function toggle(c: Chat, e?: MouseEvent) {
  const s = new Set(selected.value)
  if (e?.shiftKey && lastClicked.value !== null) {
    const ids = visible.value.map((x) => x.id)
    const a = ids.indexOf(lastClicked.value)
    const b = ids.indexOf(c.id)
    if (a >= 0 && b >= 0) for (const id of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) s.add(id)
  } else if (s.has(c.id)) s.delete(c.id)
  else s.add(c.id)
  lastClicked.value = c.id
  selected.value = s
}
const allSelected = computed(() => visible.value.length > 0 && visible.value.every((c) => selected.value.has(c.id)))
function toggleAll() {
  const s = new Set(selected.value)
  if (allSelected.value) visible.value.forEach((c) => s.delete(c.id))
  else visible.value.forEach((c) => s.add(c.id))
  selected.value = s
}

function syncPercent(c: Chat) {
  if (!c.total_messages) return c.synced ? 100 : 0
  return Math.min(100, Math.round((c.stored_messages / c.total_messages) * 100))
}

async function refresh() {
  await store.refresh()
  message.info(t('chats.refreshing'))
}

function openFolderModal(f?: Folder, parent?: number) {
  folderForm.value = f ? { id: f.id, name: f.name, emoji: f.emoji ?? '', color: f.color, parent_id: f.parent_id } : { name: '', emoji: '', color: null, parent_id: parent ?? null }
  folderModal.value = true
}
async function saveFolder() {
  const body = { name: folderForm.value.name, emoji: folderForm.value.emoji || null, color: folderForm.value.color, parent_id: folderForm.value.parent_id }
  if (folderForm.value.id) await api.put(`/folders/${folderForm.value.id}`, body)
  else await api.post('/folders', body)
  folderModal.value = false
  await store.loadFolders()
}
function deleteFolder(f: Folder) {
  dialog.warning({
    title: t('folders.deleteTitle'),
    content: t('folders.deleteText', { name: f.name }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.del(`/folders/${f.id}`)
      if (folderId.value === f.id) folderId.value = null
      await store.loadFolders()
    },
  })
}
async function addToFolder(fid: number) {
  await api.post(`/folders/${fid}/chats`, { add: [...selected.value], remove: [] })
  message.success(t('folders.added', { n: selected.value.size }))
  await store.load()
}
async function removeFromFolder() {
  if (folderId.value === null) return
  await api.post(`/folders/${folderId.value}/chats`, { add: [], remove: [...selected.value] })
  await store.load()
}
async function importTg() {
  await api.post('/folders/import-telegram')
  await store.loadFolders()
  message.success(t('folders.imported'))
}
async function syncSelected() {
  await api.post('/export', { chat_ids: [...selected.value], formats: ['md'], split: { mode: 'month' }, media_types: [], no_media: true, filters: {}, also_full: false, include_transcripts: true })
  message.success(t('export.started'))
}
function transcribeSelected() {
  for (const id of selected.value) api.post('/transcribe', { chat_id: id })
  message.success(t('chat.transcribeStarted'))
}

const folderMenu = computed(() => store.folders.filter((f) => f.source === 'user').map((f) => ({ label: `${f.emoji ?? '📁'} ${f.name}`, key: f.id })))
const parentOptions = computed(() => [{ label: '—', value: null as unknown as number }, ...userFolders.value.filter((f) => f.id !== folderForm.value.id).map((f) => ({ label: f.name, value: f.id }))])
const moreMenu = computed(() => [
  { label: t('chats.syncOnly'), key: 'sync' },
  ...(app.modules.whisper ? [{ label: t('chat.transcribeAll'), key: 'transcribe' }] : []),
  ...(folderId.value !== null ? [{ label: t('folders.removeFrom'), key: 'remove' }] : []),
])
function onMore(k: string) {
  if (k === 'sync') syncSelected()
  else if (k === 'transcribe') transcribeSelected()
  else if (k === 'remove') removeFromFolder()
}
</script>

<template>
  <div class="layout">
    <aside class="folders" data-tour="folders">
      <div class="folder" :class="{ on: folderId === null }" @click="folderId = null">📚 {{ t('folders.all') }} <span class="small muted">{{ store.items.length }}</span></div>
      <div class="sect row"><span class="grow">{{ t('folders.mine') }}</span><NButton size="tiny" quaternary @click="openFolderModal()">＋</NButton></div>
      <template v-for="f in userFolders" :key="f.id">
        <div class="folder" :class="{ on: folderId === f.id }" :style="f.color ? { borderLeft: `3px solid ${f.color}` } : {}" @click="folderId = f.id" @dblclick="openFolderModal(f)">
          <span class="grow ellipsis">{{ f.emoji || '📁' }} {{ f.name }}</span>
          <span class="small muted">{{ f.chats }}</span>
          <NDropdown trigger="click" :options="[{ label: t('common.edit'), key: 'e' }, { label: t('folders.addSub'), key: 's' }, { label: t('common.delete'), key: 'd' }]" @select="(k: string) => (k === 'e' ? openFolderModal(f) : k === 's' ? openFolderModal(undefined, f.id) : deleteFolder(f))">
            <NButton size="tiny" quaternary @click.stop>⋯</NButton>
          </NDropdown>
        </div>
        <div v-for="c in childrenOf(f.id)" :key="c.id" class="folder sub" :class="{ on: folderId === c.id }" @click="folderId = c.id" @dblclick="openFolderModal(c)">
          <span class="grow ellipsis">{{ c.emoji || '📁' }} {{ c.name }}</span><span class="small muted">{{ c.chats }}</span>
          <NButton size="tiny" quaternary @click.stop="deleteFolder(c)">✕</NButton>
        </div>
      </template>
      <div v-if="!userFolders.length" class="small muted" style="padding: 4px 10px">{{ t('folders.emptyHint') }}</div>
      <div class="sect row"><span class="grow">{{ t('folders.telegram') }}</span><NButton size="tiny" quaternary :title="t('folders.import')" @click="importTg">↻</NButton></div>
      <div v-for="f in tgFolders" :key="f.id" class="folder" :class="{ on: folderId === f.id }" @click="folderId = f.id">
        <span class="grow ellipsis">{{ f.emoji || '🗂' }} {{ f.name }}</span><span class="small muted">{{ f.chats }}</span>
      </div>
    </aside>

    <section class="main">
      <div class="page-header" style="margin-bottom: 8px">
        <h1>{{ t('nav.chats') }}</h1>
        <NInput v-model:value="q" :placeholder="t('chats.filterPlaceholder')" clearable style="max-width: 260px" size="small" />
        <NSelect v-model:value="sort" size="small" style="width: 150px" :options="['recent', 'title', 'unread', 'size'].map((v) => ({ label: t(`chats.sort.${v}`), value: v }))" />
        <NButton size="small" :loading="store.loading" @click="refresh">↻</NButton>
      </div>
      <div class="chips">
        <NTag v-for="f in FILTERS" :key="f" :checked="filter === f" checkable round size="medium" @update:checked="filter = f">
          {{ t(`chats.filters.${f}`) }} <span class="small muted">{{ counts[f] }}</span>
        </NTag>
      </div>
      <div class="toolbar row">
        <NCheckbox :checked="allSelected" :indeterminate="selected.size > 0 && !allSelected" @update:checked="toggleAll">{{ t('chats.selectAll') }}</NCheckbox>
        <span class="small muted grow">{{ t('chats.selected', { n: selected.size }) }}</span>
        <NDropdown v-if="selected.size && folderMenu.length" :options="folderMenu" @select="addToFolder"><NButton size="small">📁 {{ t('folders.addTo') }}</NButton></NDropdown>
        <NDropdown v-if="selected.size" :options="moreMenu" @select="onMore"><NButton size="small">⋯</NButton></NDropdown>
        <NButton type="primary" data-tour="export-btn" :disabled="!selected.size" @click="wizard = true">⬇ {{ t('chats.exportSelected', { n: selected.size }) }}</NButton>
      </div>

      <div data-tour="chat-list" class="list">
        <template v-if="!store.loaded">
          <div v-for="n in 8" :key="n" class="skeleton-row"></div>
        </template>
        <NEmpty v-else-if="!visible.length" :description="store.items.length ? t('chats.noMatch') : t('chats.empty')" style="margin-top: 40px">
          <template #extra><NButton v-if="!store.items.length" @click="refresh">{{ t('chats.loadChats') }}</NButton></template>
        </NEmpty>
        <NVirtualList v-else :items="visible" :item-size="60" key-field="id" style="height: calc(100vh - 210px)">
          <template #default="{ item }">
            <div class="chat" :class="{ sel: selected.has(item.id) }" @click="router.push(`/chats/${item.id}`)">
              <NCheckbox :checked="selected.has(item.id)" @click.stop="toggle(item, $event)" />
              <ChatAvatar :id="item.id" :title="item.title" />
              <div class="grow">
                <div class="row">
                  <span class="title ellipsis">{{ item.title }}</span>
                  <NTooltip v-if="item.noforwards"><template #trigger><span>🔒</span></template>{{ t('chats.protectedTip') }}</NTooltip>
                  <span v-if="item.is_archived" class="small muted">🗄</span>
                </div>
                <div class="small muted row">
                  <span>{{ t(`chatType.${item.type}`) }}</span>
                  <span v-if="item.stored_messages">· {{ t('chats.stored', { n: item.stored_messages.toLocaleString(), p: syncPercent(item) }) }}</span>
                  <span v-if="item.media_count">· 📎 {{ item.media_done }}/{{ item.media_count }}</span>
                </div>
              </div>
              <div class="right">
                <div class="small muted">{{ shortDate(item.last_message_at, locale) }}</div>
                <NTag v-if="item.unread_count" size="small" round type="info">{{ item.unread_count }}</NTag>
              </div>
            </div>
          </template>
        </NVirtualList>
      </div>
    </section>

    <ExportWizard v-model:show="wizard" :chat-ids="[...selected]" />

    <NModal v-model:show="folderModal" preset="card" :title="folderForm.id ? t('folders.edit') : t('folders.new')" style="width: 420px">
      <NForm @submit.prevent="saveFolder">
        <NFormItem :label="t('folders.name')"><NInput v-model:value="folderForm.name" /></NFormItem>
        <NFormItem label="Emoji"><NInput v-model:value="folderForm.emoji" maxlength="4" style="width: 90px" /></NFormItem>
        <NFormItem :label="t('folders.color')"><NColorPicker v-model:value="folderForm.color" :modes="['hex']" :show-alpha="false" /></NFormItem>
        <NFormItem :label="t('folders.parent')"><NSelect v-model:value="folderForm.parent_id" :options="parentOptions" /></NFormItem>
        <NButton type="primary" block attr-type="submit" :disabled="!folderForm.name.trim()">{{ t('common.save') }}</NButton>
      </NForm>
    </NModal>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
}
.folders {
  width: 220px;
  flex: none;
  border-right: 1px solid rgba(128, 128, 128, 0.2);
  padding: 12px 6px;
  overflow: auto;
}
.folder {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}
.folder.sub {
  padding-left: 26px;
}
.folder.on,
.folder:hover {
  background: rgba(42, 171, 238, 0.12);
}
.sect {
  margin: 14px 10px 4px;
  font-size: 12px;
  text-transform: uppercase;
  opacity: 0.6;
}
.main {
  flex: 1;
  min-width: 0;
  padding: 16px 20px 0;
}
.chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.toolbar {
  padding: 6px 0;
}
.chat {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border-radius: 8px;
  cursor: pointer;
}
.chat:hover,
.chat.sel {
  background: rgba(42, 171, 238, 0.08);
}
.title {
  font-weight: 500;
}
.right {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
@media (max-width: 800px) {
  .folders {
    display: none;
  }
}
</style>
