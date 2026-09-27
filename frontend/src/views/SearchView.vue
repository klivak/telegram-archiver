<script setup lang="ts">
import { NButton, NCheckbox, NDatePicker, NEmpty, NInput, NModal, NSelect, NSpace, NTag, useMessage } from 'naive-ui'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useChatsStore } from '@/stores/chats'
import { usePresetsStore } from '@/stores/presets'
import { debounce } from '@/utils'

interface Hit {
  chat_id: number
  id: number
  date: string
  snippet: string
  chat_title: string
  sender_name: string | null
  media_type: string | null
  out: number
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const chats = useChatsStore()
const presets = usePresetsStore()
const message = useMessage()
const q = ref((route.query.q as string) ?? '')
const filters = ref<{ chat_ids: number[]; folder_id: number | null; media_type: string | null; has_link: boolean; has_transcript: boolean; only_mine: boolean; sort: 'date' | 'rank' }>({ chat_ids: [], folder_id: null, media_type: null, has_link: false, has_transcript: false, only_mine: false, sort: 'date' })
const range = ref<[number, number] | null>(null)
const items = ref<Hit[]>([])
const total = ref<number | null>(null)
const took = ref(0)
const loading = ref(false)
const input = ref<InstanceType<typeof NInput> | null>(null)
const saveModal = ref(false)
const presetName = ref('')

const payload = computed(() => ({
  ...filters.value,
  date_from: range.value ? new Date(range.value[0]).toISOString().slice(0, 10) : undefined,
  date_to: range.value ? new Date(range.value[1]).toISOString().slice(0, 10) : undefined,
}))

async function run(offset = 0) {
  const hasFilter = filters.value.chat_ids.length || filters.value.folder_id || filters.value.media_type || filters.value.has_link || filters.value.has_transcript || filters.value.only_mine || range.value
  if (!q.value.trim() && !hasFilter) {
    items.value = []
    total.value = null
    return
  }
  loading.value = true
  const r = await api.get<{ items: Hit[]; total: number | null; took_ms: number }>('/search', { q: q.value, filters: JSON.stringify(payload.value), limit: 50, offset })
  items.value = offset ? [...items.value, ...r.items] : r.items
  if (r.total !== null) total.value = r.total
  took.value = r.took_ms
  loading.value = false
  router.replace({ query: { q: q.value || undefined } })
}
const debounced = debounce(() => run(), 250)
watch(q, debounced)
watch([filters, range], () => run(), { deep: true })

onMounted(async () => {
  presets.load().catch(() => undefined)
  if (q.value) run()
  await nextTick()
  if (route.query.focus) input.value?.focus()
  else input.value?.focus()
})

const chatOptions = computed(() => chats.items.map((c) => ({ label: c.title, value: c.id })))
const folderOptions = computed(() => chats.folders.map((f) => ({ label: `${f.emoji ?? '📁'} ${f.name}`, value: f.id })))
const typeOptions = computed(() => ['photo', 'video', 'round', 'voice', 'audio', 'document', 'sticker', 'gif', 'poll', 'geo', 'webpage'].map((v) => ({ label: t(`media.${v}`, v), value: v })))
const searchPresets = computed(() => presets.byKind('search'))

function applyPreset(id: number) {
  const p = presets.items.find((x) => x.id === id)
  if (!p) return
  q.value = p.data.q ?? ''
  filters.value = { ...filters.value, ...p.data.filters }
  range.value = p.data.range ?? null
}
async function savePreset() {
  await presets.save({ kind: 'search', name: presetName.value, data: { q: q.value, filters: filters.value, range: range.value } })
  saveModal.value = false
  presetName.value = ''
  message.success(t('search.presetSaved'))
}
function exportPresets() {
  const blob = new Blob([presets.exportJson()], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'telegram-archiver-presets.json'
  a.click()
  URL.revokeObjectURL(a.href)
}
function importPresets() {
  const inp = document.createElement('input')
  inp.type = 'file'
  inp.accept = '.json,application/json'
  inp.onchange = async () => {
    const f = inp.files?.[0]
    if (!f) return
    await presets.importJson(await f.text())
    message.success(t('search.presetsImported'))
  }
  inp.click()
}
const open = (h: Hit) => router.push({ path: `/chats/${h.chat_id}`, query: { msg: h.id } })
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>{{ t('nav.search') }}</h1>
      <NSelect v-if="searchPresets.length" :options="searchPresets.map((p) => ({ label: p.name, value: p.id! }))" :placeholder="t('search.presets')" size="small" style="width: 200px" clearable @update:value="applyPreset" />
      <NButton size="small" @click="saveModal = true">★ {{ t('search.savePreset') }}</NButton>
      <NButton size="small" quaternary @click="exportPresets">⇩ JSON</NButton>
      <NButton size="small" quaternary @click="importPresets">⇧ JSON</NButton>
    </div>
    <NInput ref="input" v-model:value="q" size="large" clearable :placeholder="t('search.placeholder')" data-tour="search-input" :loading="loading" />
    <NSpace style="margin: 10px 0" align="center">
      <NSelect v-model:value="filters.chat_ids" multiple filterable :options="chatOptions" :placeholder="t('search.chats')" size="small" style="min-width: 220px; max-width: 360px" max-tag-count="responsive" />
      <NSelect v-model:value="filters.folder_id" :options="folderOptions" clearable :placeholder="t('search.folder')" size="small" style="width: 160px" />
      <NSelect v-model:value="filters.media_type" :options="typeOptions" clearable :placeholder="t('search.mediaType')" size="small" style="width: 150px" />
      <NDatePicker v-model:value="range" type="daterange" clearable size="small" />
      <NCheckbox v-model:checked="filters.has_link">{{ t('search.hasLink') }}</NCheckbox>
      <NCheckbox v-model:checked="filters.has_transcript">{{ t('search.hasTranscript') }}</NCheckbox>
      <NCheckbox v-model:checked="filters.only_mine">{{ t('search.onlyMine') }}</NCheckbox>
      <NSelect v-model:value="filters.sort" :options="[{ label: t('search.sortDate'), value: 'date' }, { label: t('search.sortRank'), value: 'rank' }]" size="small" style="width: 150px" />
    </NSpace>
    <div v-if="total !== null" class="small muted" style="margin-bottom: 8px">{{ t('search.found', { n: total, ms: took }) }}</div>
    <NEmpty v-if="total === 0" :description="t('search.nothing')" />
    <NEmpty v-else-if="total === null" :description="t('search.hint')" />
    <div v-for="h in items" :key="`${h.chat_id}:${h.id}`" class="hit" @click="open(h)">
      <div class="row small">
        <strong class="ellipsis">{{ h.chat_title }}</strong>
        <span class="muted">{{ h.out ? t('search.me') : h.sender_name }}</span>
        <NTag v-if="h.media_type" size="tiny">{{ t(`media.${h.media_type}`, h.media_type) }}</NTag>
        <span class="grow"></span>
        <span class="muted">{{ h.date?.slice(0, 16).replace('T', ' ') }}</span>
      </div>
      <!-- snippet is produced by SQLite FTS from escaped-by-design plain text + <mark> tags -->
      <div class="snip" v-html="h.snippet.replace(/</g, '&lt;').replace(/&lt;(\/?)mark>/g, '<$1mark>')"></div>
    </div>
    <div v-if="total && items.length < total" style="text-align: center; margin: 12px">
      <NButton :loading="loading" @click="run(items.length)">{{ t('search.more') }}</NButton>
    </div>

    <NModal v-model:show="saveModal" preset="card" :title="t('search.savePreset')" style="width: 380px">
      <NInput v-model:value="presetName" :placeholder="t('export.presetName')" @keydown.enter="presetName && savePreset()" />
      <NButton type="primary" block style="margin-top: 10px" :disabled="!presetName" @click="savePreset">{{ t('common.save') }}</NButton>
    </NModal>
  </div>
</template>

<style scoped>
.hit {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  border-bottom: 1px solid rgba(128, 128, 128, 0.12);
}
.hit:hover {
  background: rgba(42, 171, 238, 0.08);
}
.snip {
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
