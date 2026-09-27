<script setup lang="ts">
import { NAlert, NButton, NCode, NDivider, NForm, NFormItem, NInput, NInputNumber, NRadioButton, NRadioGroup, NSelect, NSwitch, NTabPane, NTabs, NTimePicker, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import type { Settings } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'

const { t } = useI18n()
const app = useAppStore()
const chats = useChatsStore()
const route = useRoute()
const message = useMessage()
const dialog = useDialog()
const tab = ref((route.query.tab as string) || 'general')
const form = ref<Settings | null>(null)
const keys = ref<Record<string, string>>({ ai_key_anthropic: '', ai_key_openai: '', ai_key_openrouter: '', ai_key_groq: '', ai_key_gemini: '' })
const prompts = ref<{ defaults: Record<string, string>; overrides: Record<string, string> } | null>(null)
const saving = ref(false)
const version = ref('')

onMounted(async () => {
  await app.loadSettings()
  form.value = JSON.parse(JSON.stringify(app.settings))
  prompts.value = await api.get('/ai/prompts')
  version.value = (await fetch('/api/health').then((r) => r.json())).version
})
watch(() => app.settings, (s) => s && !form.value && (form.value = JSON.parse(JSON.stringify(s))))

const originalRoot = computed(() => app.settings?.archive_root)

async function save() {
  if (!form.value) return
  saving.value = true
  try {
    const { secrets: _s, api_id: _a, ...patch } = form.value
    void _s
    void _a
    await app.saveSettings(patch)
    for (const [k, v] of Object.entries(keys.value)) if (v) await api.post('/settings/secret', { key: k, value: v })
    keys.value = { ai_key_anthropic: '', ai_key_openai: '', ai_key_openrouter: '', ai_key_groq: '', ai_key_gemini: '' }
    await app.loadSettings()
    form.value = JSON.parse(JSON.stringify(app.settings))
    message.success(t('settings.saved'))
  } finally {
    saving.value = false
  }
}

function toggleProtected(v: boolean) {
  if (!form.value) return
  if (!v) {
    form.value.protected_content = false
    return
  }
  dialog.warning({
    title: t('protected.title'),
    content: t('protected.text'),
    positiveText: t('protected.enable'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      form.value!.protected_content = true
    },
  })
}

async function clearKey(k: string) {
  await api.post('/settings/secret', { key: k, value: '' })
  await app.loadSettings()
  form.value!.secrets = app.settings!.secrets
}

function logout() {
  dialog.warning({
    title: t('settings.logoutTitle'),
    content: t('settings.logoutText'),
    positiveText: t('settings.logout'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      await api.post('/auth/logout')
      await app.loadAuth()
    },
  })
}

async function rebuildSearch() {
  await api.post('/search/rebuild')
  message.success(t('settings.rebuilt'))
}
async function demoJob() {
  await api.post('/jobs-demo', { steps: 20 })
  message.info(t('settings.demoStarted'))
}

const scheduleTs = computed({
  get: () => {
    const s = form.value?.ai.schedule_time
    if (!s) return null
    const [h, m] = s.split(':').map(Number)
    return new Date(2000, 0, 1, h, m).getTime()
  },
  set: (v: number | null) => {
    if (!form.value) return
    if (!v) form.value.ai.schedule_time = ''
    else {
      const d = new Date(v)
      form.value.ai.schedule_time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    }
  },
})
const chatOptions = computed(() => chats.items.map((c) => ({ label: c.title, value: c.id })))
const folderOptions = computed(() => chats.folders.map((f) => ({ label: f.name, value: f.id })))
const whisperModels = ['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo'].map((v) => ({ label: v, value: v }))
const defaultModels = ref<Record<string, string>>({})
onMounted(async () => {
  const r = await api.get<{ items: { id: string; default_model: string }[] }>('/ai/providers')
  defaultModels.value = Object.fromEntries(r.items.map((p) => [p.id, p.default_model]))
})
// a model name belongs to one provider: reset it so the new provider's default is used
watch(() => form.value?.ai.provider, (p, old) => { if (form.value && old && p !== old) form.value.ai.model = '' })
const keyName = computed(() => (form.value ? `ai_key_${form.value.ai.provider}` : ''))
</script>

<template>
  <div class="page" v-if="form">
    <div class="page-header">
      <h1>{{ t('nav.settings') }}</h1>
      <NButton type="primary" :loading="saving" @click="save">{{ t('common.save') }}</NButton>
    </div>
    <NTabs v-model:value="tab" type="line" placement="top">
      <NTabPane name="general" :tab="t('settings.tabs.general')">
        <NForm label-placement="left" label-width="240" style="max-width: 720px">
          <NFormItem :label="t('settings.language')">
            <NRadioGroup v-model:value="form.language"><NRadioButton value="uk">Українська</NRadioButton><NRadioButton value="en">English</NRadioButton></NRadioGroup>
          </NFormItem>
          <NFormItem :label="t('settings.theme')">
            <NRadioGroup v-model:value="form.theme"><NRadioButton value="auto">{{ t('settings.themeAuto') }}</NRadioButton><NRadioButton value="light">{{ t('settings.themeLight') }}</NRadioButton><NRadioButton value="dark">{{ t('settings.themeDark') }}</NRadioButton></NRadioGroup>
          </NFormItem>
          <NFormItem :label="t('settings.archiveRoot')" :feedback="form.archive_root !== originalRoot ? t('settings.restartNeeded') : ''">
            <NInput v-model:value="form.archive_root" />
          </NFormItem>
          <NFormItem :label="t('settings.advancedMode')"><NSwitch v-model:value="form.advanced_mode" /></NFormItem>
          <NFormItem :label="t('settings.checkUpdates')"><NSwitch v-model:value="form.check_updates" /></NFormItem>
          <NFormItem :label="t('tutorial.show')"><NButton @click="app.tutorialOpen = true">{{ t('tutorial.show') }}</NButton></NFormItem>
        </NForm>
      </NTabPane>

      <NTabPane name="telegram" :tab="t('settings.tabs.telegram')">
        <NForm label-placement="left" label-width="240" style="max-width: 720px">
          <NFormItem :label="t('settings.protected')" :feedback="t('settings.protectedHint')">
            <NSwitch :value="form.protected_content" @update:value="toggleProtected" />
          </NFormItem>
          <NFormItem :label="t('settings.importFolders')"><NSwitch v-model:value="form.import_tg_folders" /></NFormItem>
          <NFormItem :label="t('settings.takeout')" :feedback="t('export.takeoutHint')"><NSwitch v-model:value="form.use_takeout" /></NFormItem>
          <template v-if="form.advanced_mode">
            <NDivider>{{ t('common.advanced') }}</NDivider>
            <NFormItem :label="t('settings.rps')" :feedback="t('settings.rpsHint')"><NInputNumber v-model:value="form.history_rps" :min="0.1" :max="5" :step="0.1" /></NFormItem>
            <NFormItem :label="t('settings.concurrency')"><NInputNumber v-model:value="form.media_concurrency" :min="1" :max="6" /></NFormItem>
            <NFormItem :label="t('settings.retries')"><NInputNumber v-model:value="form.max_retries" :min="1" :max="20" /></NFormItem>
            <NFormItem :label="t('settings.window')" :feedback="t('settings.windowHint')"><NInput v-model:value="form.download_window" placeholder="01:00-07:00" /></NFormItem>
            <NFormItem :label="t('settings.dailyLimit')"><NInputNumber v-model:value="form.daily_gb_limit" :min="0" :step="1"><template #suffix>GB</template></NInputNumber></NFormItem>
            <NFormItem :label="t('settings.pathTemplate')" :feedback="'{type} {yyyy} {mm} {msgid} {name}'"><NInput v-model:value="form.path_template" /></NFormItem>
          </template>
          <div v-else class="small muted">{{ t('settings.advancedHint') }}</div>
          <NAlert type="info" :show-icon="false" class="small" style="margin-top: 12px">{{ t('settings.secretChats') }}</NAlert>
        </NForm>
      </NTabPane>

      <NTabPane name="whisper" :tab="t('settings.tabs.whisper')">
        <NAlert v-if="!app.modules.whisper" type="info" :title="t('settings.whisperInstall')" style="margin-bottom: 12px; max-width: 720px">
          <NCode code="cd backend; uv sync --extra whisper" language="powershell" />
        </NAlert>
        <NForm label-placement="left" label-width="240" style="max-width: 720px">
          <NFormItem :label="t('settings.whisperEnabled')"><NSwitch v-model:value="form.whisper.enabled" /></NFormItem>
          <NFormItem :label="t('settings.whisperModel')" :feedback="t('settings.whisperModelHint')"><NSelect v-model:value="form.whisper.model" :options="whisperModels" /></NFormItem>
          <NFormItem :label="t('settings.whisperLang')"><NSelect v-model:value="form.whisper.language" :options="['auto', 'uk', 'en', 'ru'].map((v) => ({ label: v, value: v }))" /></NFormItem>
          <NFormItem :label="t('settings.whisperAuto')"><NSwitch v-model:value="form.whisper.auto" /></NFormItem>
          <NFormItem v-if="form.whisper.auto" :label="t('settings.whisperChats')"><NSelect v-model:value="form.whisper.auto_chat_ids" multiple filterable :options="chatOptions" /></NFormItem>
          <template v-if="form.advanced_mode">
            <NFormItem :label="t('settings.whisperDevice')"><NSelect v-model:value="form.whisper.device" :options="['auto', 'cpu', 'cuda'].map((v) => ({ label: v, value: v }))" /></NFormItem>
            <NFormItem label="compute_type"><NSelect v-model:value="form.whisper.compute_type" :options="['auto', 'int8', 'int8_float16', 'float16', 'float32'].map((v) => ({ label: v, value: v }))" /></NFormItem>
            <NFormItem label="beam_size"><NInputNumber v-model:value="form.whisper.beam_size" :min="1" :max="10" /></NFormItem>
          </template>
        </NForm>
      </NTabPane>

      <NTabPane name="ai" :tab="t('settings.tabs.ai')">
        <NAlert type="warning" :show-icon="false" class="small" style="margin-bottom: 12px; max-width: 720px">{{ t('settings.aiPrivacy') }}</NAlert>
        <NForm label-placement="left" label-width="240" style="max-width: 720px">
          <NFormItem :label="t('settings.aiEnabled')"><NSwitch v-model:value="form.ai.enabled" /></NFormItem>
          <NFormItem :label="t('settings.aiProvider')">
            <NRadioGroup v-model:value="form.ai.provider">
              <NRadioButton value="ollama">Ollama ({{ t('settings.local') }})</NRadioButton>
              <NRadioButton value="anthropic">Anthropic</NRadioButton>
              <NRadioButton value="openai">OpenAI</NRadioButton>
              <NRadioButton value="openrouter">OpenRouter</NRadioButton>
              <NRadioButton value="groq">Groq</NRadioButton>
              <NRadioButton value="gemini">Gemini</NRadioButton>
            </NRadioGroup>
          </NFormItem>
          <NFormItem :label="t('settings.aiModel')" :feedback="t('settings.aiModelHint')"><NInput v-model:value="form.ai.model" :placeholder="defaultModels[form.ai.provider] ?? ''" /></NFormItem>
          <NFormItem v-if="form.ai.provider === 'ollama'" label="Ollama URL"><NInput v-model:value="form.ai.ollama_url" /></NFormItem>
          <NFormItem v-else :label="t('settings.aiKey')" :feedback="form.secrets[keyName] ? t('settings.keyStored') : t('settings.keyHint')">
            <NInput v-model:value="keys[keyName]" type="password" show-password-on="click" :placeholder="form.secrets[keyName] ? '••••••••' : ''" />
            <NButton v-if="form.secrets[keyName]" quaternary size="small" @click="clearKey(keyName)">🗑</NButton>
          </NFormItem>
          <NFormItem :label="t('settings.maskPii')"><NSwitch v-model:value="form.ai.mask_pii" /></NFormItem>
          <NFormItem :label="t('settings.schedule')" :feedback="t('settings.scheduleHint')"><NTimePicker v-model:value="scheduleTs" format="HH:mm" clearable /></NFormItem>
          <NFormItem :label="t('settings.tokenLimit')"><NInputNumber v-model:value="form.ai.daily_token_limit" :min="0" :step="10000" /></NFormItem>
          <template v-if="form.advanced_mode && prompts">
            <NDivider>{{ t('settings.prompts') }}</NDivider>
            <NFormItem v-for="(def, k) in prompts.defaults" :key="k" :label="t(`ai.task.${k}`)">
              <NInput type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" :value="form.ai.prompts[k] ?? def" @update:value="(v: string) => (form!.ai.prompts[k] = v)" />
            </NFormItem>
          </template>
        </NForm>
      </NTabPane>

      <NTabPane name="monitor" :tab="t('settings.tabs.monitor')">
        <NForm label-placement="left" label-width="240" style="max-width: 720px">
          <NFormItem :label="t('settings.monitorEnabled')"><NSwitch v-model:value="form.monitor.enabled" /></NFormItem>
          <NFormItem :label="t('settings.interval')"><NInputNumber v-model:value="form.monitor.interval_min" :min="5" :max="240"><template #suffix>{{ t('settings.min') }}</template></NInputNumber></NFormItem>
          <NFormItem :label="t('settings.monitorFolders')"><NSelect v-model:value="form.monitor.folder_ids" multiple :options="folderOptions" :placeholder="t('settings.allChats')" /></NFormItem>
          <NFormItem :label="t('settings.monitorChats')"><NSelect v-model:value="form.monitor.chat_ids" multiple filterable :options="chatOptions" :placeholder="t('settings.allChats')" /></NFormItem>
          <NFormItem :label="t('settings.ignore')"><NSelect v-model:value="form.monitor.ignore_chat_ids" multiple filterable :options="chatOptions" /></NFormItem>
          <NFormItem :label="t('settings.mentionsOnly')"><NSwitch v-model:value="form.monitor.mentions_only" /></NFormItem>
          <NFormItem :label="t('settings.stayOffline')"><NSwitch v-model:value="form.monitor.stay_offline" /></NFormItem>
        </NForm>
      </NTabPane>

      <NTabPane name="account" :tab="t('settings.tabs.account')">
        <p>{{ app.auth?.me?.first_name }} {{ app.auth?.me?.last_name }} <span class="muted">@{{ app.auth?.me?.username }}</span></p>
        <p class="small muted">{{ t('settings.sessionStored') }}</p>
        <NButton type="error" @click="logout">{{ t('settings.logout') }}</NButton>
        <NDivider>{{ t('settings.maintenance') }}</NDivider>
        <NButton @click="rebuildSearch">{{ t('settings.rebuildSearch') }}</NButton>
        <NButton quaternary style="margin-left: 8px" @click="demoJob">{{ t('settings.demoJob') }}</NButton>
      </NTabPane>

      <NTabPane name="about" :tab="t('settings.tabs.about')">
        <p><strong>Telegram Archiver</strong> {{ version }}</p>
        <p>{{ t('settings.aboutText') }}</p>
        <p class="small muted">{{ t('settings.notAffiliated') }}</p>
        <p class="small muted">{{ t('settings.modules') }}: Whisper {{ app.modules.whisper ? '✓' : '—' }} · Playwright {{ app.modules.playwright ? '✓' : '—' }} · cryptg {{ app.modules.cryptg ? '✓' : '—' }}</p>
      </NTabPane>
    </NTabs>
  </div>
</template>
