<script setup lang="ts">
import { NAlert, NButton, NCheckbox, NCollapse, NCollapseItem, NDatePicker, NDivider, NDrawer, NDrawerContent, NDynamicTags, NForm, NFormItem, NInput, NInputNumber, NRadioButton, NRadioGroup, NSelect, NSpace, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFormat } from '@/composables/format'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { FORMATS, MEDIA_TYPES, SPLIT_MODES, type ExportRequest, type MediaType } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { usePresetsStore } from '@/stores/presets'
import { debounce, formatBytes } from '@/utils'

const props = defineProps<{ show: boolean; chatIds: number[] }>()
const emit = defineEmits<{ 'update:show': [boolean]; started: [number] }>()
const fmt = useFormat()
const { t } = useI18n()
const app = useAppStore()
const chats = useChatsStore()
const presets = usePresetsStore()
const dialog = useDialog()
const message = useMessage()
const router = useRouter()

const defaults = (): ExportRequest => ({
  chat_ids: [],
  formats: ['md'],
  split: { mode: 'month', size_mb: 5, tokens: 100000, overlap: 20 },
  media_types: ['photo', 'voice', 'round', 'document'],
  no_media: false,
  filters: { date_from: null, date_to: null, max_size_mb: null, from: null, extensions: [] },
  also_full: false,
  include_transcripts: true,
  takeout: null,
})
const form = ref<ExportRequest>(defaults())
const dateRange = ref<[number, number] | null>(null)
type PeriodPreset = 'all' | 'today' | '7d' | '30d' | '3m' | '1y' | 'thisYear' | 'lastYear' | 'custom'
const PERIODS: PeriodPreset[] = ['all', 'today', '7d', '30d', '3m', '1y', 'thisYear', 'lastYear', 'custom']
const period = ref<PeriodPreset>('all')
const dayStart = (y: number, m: number, d: number) => new Date(y, m, d).getTime()
function periodRange(p: PeriodPreset): [number, number] | null {
  const n = new Date()
  const [y, m, d] = [n.getFullYear(), n.getMonth(), n.getDate()]
  const today = dayStart(y, m, d)
  switch (p) {
    case 'today': return [today, today]
    case '7d': return [dayStart(y, m, d - 6), today]
    case '30d': return [dayStart(y, m, d - 29), today]
    case '3m': return [dayStart(y, m - 3, d), today]
    case '1y': return [dayStart(y - 1, m, d), today]
    case 'thisYear': return [dayStart(y, 0, 1), today]
    case 'lastYear': return [dayStart(y - 1, 0, 1), dayStart(y - 1, 11, 31)]
    default: return null
  }
}
function pickPeriod(p: PeriodPreset) {
  period.value = p
  if (p !== 'custom') dateRange.value = periodRange(p)
}
// the same presets inside the calendar popup
const pickerShortcuts = computed(() => Object.fromEntries(PERIODS.filter((p) => p !== 'all' && p !== 'custom').map((p) => [t(`export.period.${p}`), () => periodRange(p) as [number, number]])))
function onRangePicked(v: [number, number] | null) {
  dateRange.value = v
  period.value = v ? 'custom' : 'all'
}
const localDay = (ms: number) => {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function toggleMedia(m: MediaType) {
  const set = new Set(form.value.media_types)
  if (set.has(m)) set.delete(m)
  else set.add(m)
  form.value.media_types = MEDIA_TYPES.filter((x) => set.has(x))
}
function toggleFormat(f: (typeof FORMATS)[number]) {
  const set = new Set(form.value.formats)
  if (set.has(f)) {
    if (set.size > 1) set.delete(f)
  } else set.add(f)
  form.value.formats = FORMATS.filter((x) => set.has(x))
}
const MEDIA_ICONS: Record<string, string> = { photo: '🖼️', video: '🎬', round: '⭕', voice: '🎙️', audio: '🎵', document: '📄', sticker: '🏷️', gif: '🎞️' }
type TypeCount = Record<string, { count: number; bytes: number; done: number }>
const estimate = ref<{ media: { count: number; bytes: number; by_type: TypeCount }; all_types: TypeCount; unsynced_chats: number; messages: number; messages_total: number; protected_chats: number; protected_enabled: boolean } | null>(null)
const mediaDone = computed(() => Object.values(estimate.value?.media.by_type ?? {}).reduce((a, x) => a + (x.done || 0), 0))
const toFetch = computed(() => Math.max(0, (estimate.value?.messages_total ?? 0) - (estimate.value?.messages ?? 0)))
const catalogPartial = computed(() => toFetch.value > 0 || !!estimate.value?.unsynced_chats)
const busy = ref(false)
const presetName = ref('')

const selected = computed(() => props.chatIds.map((id) => chats.byId.get(id)).filter(Boolean))
const presetOptions = computed(() => presets.byKind('export').map((p) => ({ label: p.name, value: p.id! })))

watch(
  () => props.show,
  (v) => {
    if (v) {
      form.value.chat_ids = [...props.chatIds]
      refreshEstimate()
    }
  },
)
watch(dateRange, (r) => {
  // local calendar days (toISOString would shift the date by the UTC offset)
  form.value.filters.date_from = r ? localDay(r[0]) : null
  form.value.filters.date_to = r ? localDay(r[1]) : null
})
const refreshEstimate = debounce(async () => {
  if (!props.chatIds.length) return
  estimate.value = await api.post('/export/estimate', { ...form.value, chat_ids: props.chatIds })
}, 250)
watch(() => [form.value.media_types, form.value.no_media, form.value.filters], refreshEstimate, { deep: true })

function applyPreset(id: number) {
  const p = presets.items.find((x) => x.id === id)
  if (!p) return
  const d = defaults()
  form.value = { ...d, ...(p.data as Partial<ExportRequest>), filters: { ...d.filters, ...(p.data.filters ?? {}) }, split: { ...d.split, ...(p.data.split ?? {}) }, chat_ids: [...props.chatIds] }
  if (p.data.chat_ids?.length && !props.chatIds.length) form.value.chat_ids = p.data.chat_ids
}

async function savePreset() {
  if (!presetName.value.trim()) return
  const { chat_ids: _ignored, ...data } = form.value
  void _ignored
  await presets.save({ kind: 'export', name: presetName.value.trim(), data: { ...data, chat_ids: props.chatIds } })
  message.success(t('export.presetSaved'))
  presetName.value = ''
}

async function confirmProtected(): Promise<boolean> {
  if (!estimate.value?.protected_chats || app.settings?.protected_content) return true
  return new Promise((resolve) => {
    dialog.warning({
      title: t('protected.title'),
      content: t('protected.text'),
      positiveText: t('protected.enable'),
      negativeText: t('protected.skip'),
      onPositiveClick: async () => {
        await app.saveSettings({ protected_content: true })
        resolve(true)
      },
      onNegativeClick: () => resolve(true),
      onClose: () => resolve(false),
    })
  })
}

async function start() {
  if (!(await confirmProtected())) return
  busy.value = true
  try {
    const r = await api.post<{ job_id: number }>('/export', { ...form.value, chat_ids: props.chatIds })
    emit('started', r.job_id)
    emit('update:show', false)
    message.success(t('export.started'))
    router.push('/downloads')
  } finally {
    busy.value = false
  }
}

const mediaLabel = (m: MediaType) => t(`media.${m}`)
const fromSel = computed({
  get: () => form.value.filters.from ?? 'all',
  set: (v: string) => (form.value.filters.from = v === 'all' ? null : (v as 'me' | 'others')),
})
</script>

<template>
  <NDrawer :show="show" :width="560" placement="right" @update:show="(v: boolean) => emit('update:show', v)">
    <NDrawerContent :title="t('export.title', { n: fmt.n(chatIds.length) })" closable :native-scrollbar="false">
      <div class="xw-chips">
        <NTag v-for="c in selected.slice(0, 12)" :key="c!.id" size="small" round :bordered="false">{{ c!.noforwards ? '🔒 ' : '' }}{{ c!.title }}</NTag>
        <span v-if="selected.length > 12" class="small muted">+{{ fmt.n(selected.length - 12) }}</span>
      </div>
      <NSelect v-if="presetOptions.length" :options="presetOptions" :placeholder="t('export.loadPreset')" clearable size="small" style="margin-bottom: 12px" @update:value="applyPreset" />

      <section class="xw-card">
        <header>
          <span class="xw-ico">📅</span>
          <div class="grow"><strong>{{ t('export.periodTitle') }}</strong><div class="small muted">{{ t('export.periodHint') }}</div></div>
        </header>
        <div class="xw-pills">
          <button v-for="p in PERIODS" :key="p" type="button" class="xw-pill" :class="{ on: period === p }" @click="pickPeriod(p)">{{ t(`export.period.${p}`) }}</button>
        </div>
        <Transition name="xw-fade">
          <NDatePicker v-if="period !== 'all'" :value="dateRange" type="daterange" clearable :shortcuts="pickerShortcuts" :start-placeholder="t('export.dateFrom')" :end-placeholder="t('export.dateTo')" style="margin-top: 10px; width: 100%" @update:value="onRangePicked" />
        </Transition>
      </section>

      <section class="xw-card" data-tour="export-media">
        <header><span class="xw-ico">🗂️</span><strong>{{ t('export.whatToSave') }}</strong></header>
        <div class="xw-modes">
          <button type="button" class="xw-mode" :class="{ on: form.no_media }" @click="form.no_media = true">
            <span class="xw-mode-ico">💬</span>
            <span><strong>{{ t('export.modeText') }}</strong><span class="small muted">{{ t('export.modeTextHint') }}</span></span>
          </button>
          <button type="button" class="xw-mode" :class="{ on: !form.no_media }" @click="form.no_media = false">
            <span class="xw-mode-ico">🖼️</span>
            <span><strong>{{ t('export.modeMedia') }}</strong><span class="small muted">{{ t('export.modeMediaHint') }}</span></span>
          </button>
        </div>
        <template v-if="!form.no_media">
          <div class="small muted" style="margin: 12px 0 8px">{{ t('export.pickTypes') }}</div>
          <div class="xw-grid">
            <button v-for="m in MEDIA_TYPES" :key="m" type="button" class="xw-tile" :class="{ on: form.media_types.includes(m), empty: estimate && !estimate.all_types[m] }" @click="toggleMedia(m)">
              <span class="xw-tile-ico">{{ MEDIA_ICONS[m] }}</span>
              <span class="xw-tile-name">{{ mediaLabel(m) }}</span>
              <span class="xw-tile-sub">
                <template v-if="estimate?.all_types[m]">{{ fmt.compact(estimate.all_types[m].count) }} · {{ formatBytes(estimate.all_types[m].bytes) }}</template>
                <template v-else-if="estimate">{{ t('export.none') }}</template>
              </span>
              <span v-if="estimate?.all_types[m]?.done" class="xw-tile-done">✓ {{ fmt.compact(estimate.all_types[m].done) }}</span>
            </button>
          </div>
        </template>
        <div v-if="estimate && catalogPartial" class="xw-note small">ℹ️ {{ t('export.partialNote', { n: fmt.n(estimate.messages), total: fmt.n(estimate.messages_total) }) }}</div>
      </section>

      <section class="xw-card">
        <header><span class="xw-ico">📝</span><strong>{{ t('export.format') }}</strong></header>
        <div class="xw-pills">
          <button v-for="f in FORMATS" :key="f" type="button" class="xw-pill" :class="{ on: form.formats.includes(f) }" @click="toggleFormat(f)">{{ t(`formats.${f}`) }}</button>
        </div>
      </section>

      <section class="xw-card" data-tour="export-split">
        <header>
          <span class="xw-ico">✂️</span>
          <div class="grow"><strong>{{ t('export.split') }}</strong><div class="small muted">{{ t(`splitHint.${form.split.mode}`) }}</div></div>
        </header>
        <div class="xw-pills">
          <button v-for="sm in SPLIT_MODES" :key="sm" type="button" class="xw-pill" :class="{ on: form.split.mode === sm }" @click="form.split.mode = sm">{{ t(`split.${sm}`) }}</button>
        </div>
        <NFormItem v-if="form.split.mode === 'size'" :label="t('export.partSize')" label-placement="left" :show-feedback="false" style="margin-top: 10px">
          <NInputNumber v-model:value="form.split.size_mb" :min="0.1" :step="1" style="width: 140px"><template #suffix>MB</template></NInputNumber>
        </NFormItem>
        <NSpace v-if="form.split.mode === 'llm'" style="margin-top: 10px">
          <NInputNumber v-model:value="form.split.tokens" :min="1000" :step="10000" style="width: 170px"><template #suffix>{{ t('export.tokens') }}</template></NInputNumber>
          <NInputNumber v-model:value="form.split.overlap" :min="0" :max="200" style="width: 150px"><template #suffix>{{ t('export.overlap') }}</template></NInputNumber>
        </NSpace>
        <NCheckbox v-if="form.split.mode !== 'single'" v-model:checked="form.also_full" style="margin-top: 10px">{{ t('export.alsoFull') }}</NCheckbox>
      </section>

      <NCollapse>
        <NCollapseItem :title="t('common.advanced')" name="adv">
          <NForm label-placement="left" label-width="auto" size="small">
            <NFormItem :label="t('export.maxSize')"><NInputNumber v-model:value="form.filters.max_size_mb" :min="0" clearable style="width: 160px"><template #suffix>MB</template></NInputNumber></NFormItem>
            <NFormItem :label="t('export.from')">
              <NRadioGroup v-model:value="fromSel" size="small">
                <NRadioButton value="all" :label="t('export.fromAll')" />
                <NRadioButton value="me" :label="t('export.fromMe')" />
                <NRadioButton value="others" :label="t('export.fromOthers')" />
              </NRadioGroup>
            </NFormItem>
            <NFormItem :label="t('export.extensions')"><NDynamicTags v-model:value="form.filters.extensions" /></NFormItem>
            <NFormItem :label="t('export.transcripts')"><NSwitch v-model:value="form.include_transcripts" /></NFormItem>
            <NFormItem :label="t('export.takeout')"><NSwitch :value="form.takeout ?? app.settings?.use_takeout ?? false" @update:value="(v: boolean) => (form.takeout = v)" /></NFormItem>
            <div class="small muted">{{ t('export.takeoutHint') }}</div>
            <NDivider style="margin: 12px 0" />
            <div class="row">
              <NInput v-model:value="presetName" size="small" :placeholder="t('export.presetName')" />
              <NButton size="small" :disabled="!presetName.trim()" @click="savePreset">{{ t('export.savePreset') }}</NButton>
            </div>
          </NForm>
        </NCollapseItem>
      </NCollapse>

      <section v-if="estimate" class="xw-plan">
        <strong>{{ t('export.planTitle') }}</strong>
        <ol>
          <li v-if="form.filters.date_from">{{ t('export.planFetchPeriod') }}</li>
          <li v-else-if="toFetch">{{ t('export.planFetch', { n: fmt.n(toFetch) }) }}</li>
          <li v-else>{{ t('export.planHave', { n: fmt.n(estimate.messages) }) }}</li>
          <li>{{ t('export.planWrite', { formats: form.formats.map((f) => t(`formats.${f}`)).join(', '), split: t(`split.${form.split.mode}`).toLowerCase() }) }}</li>
          <li v-if="form.no_media">{{ t('export.planNoMedia') }}</li>
          <li v-else-if="estimate.media.count || catalogPartial">{{ t('export.planMediaParallel') }}</li>
          <li v-if="!form.no_media && estimate.media.count">{{ t('export.planMedia', { n: fmt.n(estimate.media.count - mediaDone), size: formatBytes(estimate.media.bytes), done: fmt.n(mediaDone) }) }}</li>
          <li v-else-if="!form.no_media && !catalogPartial">{{ t('export.planMediaNone') }}</li>
          <li>{{ t('export.planWhere') }}</li>
        </ol>
      </section>

      <NAlert v-if="estimate?.protected_chats && !app.settings?.protected_content" type="warning" :show-icon="true" style="margin-top: 12px">{{ t('export.protectedWarn', { n: fmt.n(estimate.protected_chats) }) }}</NAlert>

      <template #footer>
        <div class="xw-foot">
          <div class="xw-sum small">
            <span>💬 {{ fmt.n(chatIds.length) }}</span>
            <span>📅 {{ period === 'custom' && form.filters.date_from ? `${form.filters.date_from} - ${form.filters.date_to}` : t(`export.period.${period}`) }}</span>
            <span v-if="!form.no_media && estimate">📦 {{ fmt.compact(estimate.media.count) }} · {{ formatBytes(estimate.media.bytes) }}</span>
          </div>
          <NButton type="primary" size="large" block :loading="busy" :disabled="!chatIds.length || !form.formats.length" @click="start">{{ t('export.start') }}</NButton>
        </div>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.xw-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.xw-card { border: 1px solid var(--border); background: var(--bg-elev); border-radius: var(--radius-lg); padding: 14px; margin-bottom: 12px; transition: border-color var(--dur) var(--ease); }
.xw-card:hover { border-color: var(--border-strong); }
.xw-card > header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.xw-ico { width: 30px; height: 30px; flex: none; display: grid; place-items: center; border-radius: 8px; background: var(--accent-soft); font-size: 15px; }
.grow { flex: 1; min-width: 0; }
.xw-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.xw-pill { font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-sunken); color: var(--text-2); cursor: pointer; transition: all var(--dur) var(--ease); }
.xw-pill:hover { border-color: var(--border-strong); color: var(--text); }
.xw-pill.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.xw-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.xw-tile { font: inherit; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 10px 6px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg-sunken); color: var(--text-2); cursor: pointer; transition: all var(--dur) var(--ease); }
.xw-tile:hover { border-color: var(--border-strong); transform: translateY(-1px); }
.xw-tile.on { background: var(--accent-soft); border-color: var(--accent); color: var(--text); }
.xw-tile-ico { font-size: 20px; line-height: 1.2; filter: grayscale(0.7); opacity: 0.6; transition: all var(--dur) var(--ease); }
.xw-tile.on .xw-tile-ico { filter: none; opacity: 1; }
.xw-tile-name { font-size: 12.5px; font-weight: 500; }
.xw-tile-sub { font-size: 11px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.xw-foot { display: flex; flex-direction: column; gap: 10px; width: 100%; }
.xw-sum { display: flex; flex-wrap: wrap; gap: 14px; color: var(--text-2); font-variant-numeric: tabular-nums; }
.xw-fade-enter-active, .xw-fade-leave-active { transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease); }
.xw-fade-enter-from, .xw-fade-leave-to { opacity: 0; transform: translateY(-4px); }
@media (max-width: 600px) { .xw-grid { grid-template-columns: repeat(2, 1fr); } }
@media (prefers-reduced-motion: reduce) { .xw-tile:hover { transform: none; } }
.xw-modes { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.xw-mode { font: inherit; text-align: left; display: flex; gap: 10px; align-items: center; padding: 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg-sunken); color: var(--text); cursor: pointer; transition: all var(--dur) var(--ease); }
.xw-mode > span:last-child { display: flex; flex-direction: column; gap: 2px; }
.xw-mode:hover { border-color: var(--border-strong); }
.xw-mode.on { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 1px var(--accent) inset; }
.xw-mode-ico { font-size: 20px; }
.xw-tile { position: relative; }
.xw-tile.empty { opacity: 0.45; }
.xw-tile-done { position: absolute; top: 4px; right: 6px; font-size: 10px; color: var(--success); font-variant-numeric: tabular-nums; }
.xw-note { margin-top: 10px; padding: 8px 10px; border-radius: var(--radius-sm); background: var(--bg-sunken); color: var(--text-2); }
.xw-plan { border: 1px dashed var(--border-strong); border-radius: var(--radius-lg); padding: 12px 14px; margin: 12px 0; font-size: 13px; }
.xw-plan ol { margin: 6px 0 0; padding-left: 18px; color: var(--text-2); line-height: 1.7; }
@media (max-width: 600px) { .xw-modes { grid-template-columns: 1fr; } }
</style>

