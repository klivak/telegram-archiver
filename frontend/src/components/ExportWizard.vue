<script setup lang="ts">
import { NAlert, NButton, NCheckbox, NCheckboxGroup, NCollapse, NCollapseItem, NDatePicker, NDivider, NDrawer, NDrawerContent, NDynamicTags, NForm, NFormItem, NInput, NInputNumber, NRadioButton, NRadioGroup, NSelect, NSpace, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
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
const estimate = ref<{ media: { count: number; bytes: number; by_type: Record<string, { count: number; bytes: number; done: number }> }; unsynced_chats: number; messages: number; protected_chats: number; protected_enabled: boolean } | null>(null)
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
  form.value.filters.date_from = r ? new Date(r[0]).toISOString().slice(0, 10) : null
  form.value.filters.date_to = r ? new Date(r[1]).toISOString().slice(0, 10) : null
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
  <NDrawer :show="show" :width="520" placement="right" @update:show="(v: boolean) => emit('update:show', v)">
    <NDrawerContent :title="t('export.title', { n: chatIds.length })" closable>
      <NSpace vertical size="large">
        <div>
          <NTag v-for="c in selected.slice(0, 12)" :key="c!.id" size="small" style="margin: 0 4px 4px 0">{{ c!.noforwards ? '🔒 ' : '' }}{{ c!.title }}</NTag>
          <span v-if="selected.length > 12" class="small muted">+{{ selected.length - 12 }}</span>
        </div>
        <NSelect v-if="presetOptions.length" :options="presetOptions" :placeholder="t('export.loadPreset')" clearable @update:value="applyPreset" />

        <div data-tour="export-media">
          <div class="row" style="justify-content: space-between">
            <strong>{{ t('export.whatMedia') }}</strong>
            <NSwitch v-model:value="form.no_media"><template #checked>{{ t('export.noMedia') }}</template><template #unchecked>{{ t('export.withMedia') }}</template></NSwitch>
          </div>
          <NCheckboxGroup v-if="!form.no_media" v-model:value="form.media_types" style="margin-top: 8px">
            <NSpace>
              <NCheckbox v-for="m in MEDIA_TYPES" :key="m" :value="m" :label="mediaLabel(m)" />
            </NSpace>
          </NCheckboxGroup>
          <div v-if="estimate" class="small muted" style="margin-top: 6px">
            <template v-if="!form.no_media">{{ t('export.estimate', { n: fmt.n(estimate.media.count), size: formatBytes(estimate.media.bytes) }) }}</template>
            <template v-if="estimate.unsynced_chats"> · {{ t('export.estimateUnsynced', { n: fmt.n(estimate.unsynced_chats) }) }}</template>
          </div>
        </div>

        <div>
          <strong>{{ t('export.format') }}</strong>
          <NCheckboxGroup v-model:value="form.formats" style="margin-top: 8px">
            <NSpace>
              <NCheckbox v-for="f in FORMATS" :key="f" :value="f" :label="t(`formats.${f}`)" />
            </NSpace>
          </NCheckboxGroup>
        </div>

        <div data-tour="export-split">
          <strong>{{ t('export.split') }}</strong>
          <NRadioGroup v-model:value="form.split.mode" size="small" style="margin-top: 8px; display: flex; flex-wrap: wrap">
            <NRadioButton v-for="s in SPLIT_MODES" :key="s" :value="s" :label="t(`split.${s}`)" />
          </NRadioGroup>
          <div class="small muted" style="margin-top: 4px">{{ t(`splitHint.${form.split.mode}`) }}</div>
          <NFormItem v-if="form.split.mode === 'size'" :label="t('export.partSize')" label-placement="left" style="margin-top: 8px">
            <NInputNumber v-model:value="form.split.size_mb" :min="0.1" :step="1" style="width: 140px"><template #suffix>MB</template></NInputNumber>
          </NFormItem>
          <NSpace v-if="form.split.mode === 'llm'" style="margin-top: 8px">
            <NInputNumber v-model:value="form.split.tokens" :min="1000" :step="10000" style="width: 170px"><template #suffix>{{ t('export.tokens') }}</template></NInputNumber>
            <NInputNumber v-model:value="form.split.overlap" :min="0" :max="200" style="width: 150px"><template #suffix>{{ t('export.overlap') }}</template></NInputNumber>
          </NSpace>
          <NCheckbox v-if="form.split.mode !== 'single'" v-model:checked="form.also_full" style="margin-top: 8px">{{ t('export.alsoFull') }}</NCheckbox>
        </div>

        <NCollapse>
          <NCollapseItem :title="t('common.advanced')" name="adv">
            <NForm label-placement="left" label-width="auto" size="small">
              <NFormItem :label="t('export.dates')"><NDatePicker v-model:value="dateRange" type="daterange" clearable /></NFormItem>
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
            </NForm>
          </NCollapseItem>
        </NCollapse>

        <NAlert v-if="estimate?.protected_chats && !app.settings?.protected_content" type="warning" :show-icon="true">{{ t('export.protectedWarn', { n: estimate.protected_chats }) }}</NAlert>

        <NDivider style="margin: 4px 0" />
        <div class="row">
          <NInput v-model:value="presetName" size="small" :placeholder="t('export.presetName')" />
          <NButton size="small" :disabled="!presetName.trim()" @click="savePreset">{{ t('export.savePreset') }}</NButton>
        </div>
      </NSpace>
      <template #footer>
        <NButton type="primary" size="large" block :loading="busy" :disabled="!chatIds.length || !form.formats.length" @click="start">{{ t('export.start') }}</NButton>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
