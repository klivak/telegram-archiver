<script setup lang="ts">
import { NButton, NSpin } from 'naive-ui'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, events } from '@/api/client'
import { useFormat } from '@/composables/format'

const props = defineProps<{ enabled: boolean; model: string }>()
const emit = defineEmits<{ enable: [] }>()
const { t } = useI18n()
const fmt = useFormat()

interface Status { installed: boolean; cuda_devices: number; models: string[]; voice: number; voice_done: number; transcribed: number }
const st = ref<Status | null>(null)
const testing = ref(false)
const testMedia = ref<number | null>(null)
const testJob = ref<number | null>(null)
const testResult = ref<{ text: string; lang: string; device?: string; chat_title?: string; duration?: number } | null>(null)
const testError = ref('')
const copied = ref(false)

const INSTALL = 'cd backend; uv sync --extra whisper'
const SIZES: Record<string, string> = { tiny: '75 MB', base: '145 MB', small: '480 MB', medium: '1.5 GB', 'large-v3': '3 GB', 'large-v3-turbo': '1.6 GB' }

async function load() {
  st.value = await api.get<Status>('/whisper/status')
}
async function runTest() {
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    const r = await api.post<{ job_id: number; media_id: number }>('/whisper/test')
    testMedia.value = r.media_id
    testJob.value = r.job_id
  } catch (e) {
    testing.value = false
    const code = (e as { detail?: { code?: string } })?.detail?.code
    testError.value = code && ['no_voice_downloaded', 'whisper_not_installed'].includes(code) ? t(`whisper.err.${code}`) : t('common.failed')
  }
}
function copyInstall() {
  navigator.clipboard?.writeText(INSTALL)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

let offs: (() => void)[] = []
onMounted(() => {
  load()
  offs = [
    events.on('transcript.done', async (ev) => {
      if (ev.data.media_id !== testMedia.value) return
      const r = await api.get<{ text: string; lang: string; chat_title?: string; duration?: number }>(`/transcripts/${ev.data.media_id}`)
      testResult.value = { ...r, device: ev.data.device }
      testing.value = false
      load()
    }),
    events.on('job.update', (ev) => {
      if (!testing.value || ev.data?.id !== testJob.value) return
      if (ev.data.status === 'failed' || ev.data.status === 'cancelled') {
        testing.value = false
        testError.value = ev.data.error || t('common.failed')
      } else if (ev.data.status === 'done') {
        // finished without a transcript event (e.g. the file is missing on disk)
        setTimeout(() => {
          if (testing.value) {
            testing.value = false
            testError.value = t('whisper.err.no_text')
          }
        }, 1500)
      }
    }),
  ]
})
onBeforeUnmount(() => offs.forEach((f) => f()))
</script>

<template>
  <div class="wg">
    <div class="status">
      <div class="st" :class="st?.installed ? 'ok' : 'no'">
        <span class="st-ico">{{ st?.installed ? '✓' : '!' }}</span>
        <div><div class="small muted">{{ t('whisper.module') }}</div><strong>{{ st ? (st.installed ? t('whisper.installed') : t('whisper.notInstalled')) : '…' }}</strong></div>
      </div>
      <div class="st" :class="st?.cuda_devices ? 'ok' : 'mid'">
        <span class="st-ico">{{ st?.cuda_devices ? '⚡' : '🖥️' }}</span>
        <div><div class="small muted">{{ t('whisper.device') }}</div><strong>{{ st ? (st.cuda_devices ? t('whisper.gpu') : t('whisper.cpu')) : '…' }}</strong></div>
      </div>
      <div class="st" :class="st?.models.includes(props.model) ? 'ok' : 'mid'">
        <span class="st-ico">📦</span>
        <div><div class="small muted">{{ t('whisper.modelLabel', { m: props.model }) }}</div><strong>{{ st?.models.includes(props.model) ? t('whisper.downloaded') : t('whisper.willDownload', { size: SIZES[props.model] ?? '?' }) }}</strong></div>
      </div>
      <div class="st">
        <span class="st-ico">🎙️</span>
        <div><div class="small muted">{{ t('whisper.voices') }}</div><strong class="num">{{ st ? t('whisper.voicesValue', { done: fmt.n(st.transcribed), total: fmt.n(st.voice_done) }) : '…' }}</strong></div>
      </div>
    </div>

    <div class="how">
      <strong>{{ t('whisper.howTitle') }}</strong>
      <ol>
        <li>
          {{ t('whisper.step1') }}
          <span v-if="st && !st.installed" class="cmd"><code>{{ INSTALL }}</code><NButton size="tiny" quaternary @click="copyInstall">{{ copied ? '✓' : '⧉' }}</NButton></span>
          <span v-else class="okmark">✓</span>
        </li>
        <li>{{ t('whisper.step2') }} <NButton v-if="!props.enabled" size="tiny" type="primary" secondary @click="emit('enable')">{{ t('whisper.enableNow') }}</NButton><span v-else class="okmark">✓</span></li>
        <li>{{ t('whisper.step3') }}</li>
        <li>{{ t('whisper.step4') }}</li>
      </ol>
      <p class="small muted">{{ t('whisper.local') }}</p>
    </div>

    <div class="test">
      <div class="grow">
        <strong>{{ t('whisper.testTitle') }}</strong>
        <div class="small muted">{{ t('whisper.testHint') }}</div>
      </div>
      <NButton type="primary" :loading="testing" :disabled="!st?.installed" @click="runTest">▶ {{ t('whisper.testBtn') }}</NButton>
    </div>
    <div v-if="testing" class="result small"><NSpin :size="14" /> {{ t('whisper.testRunning') }}</div>
    <div v-if="testResult" class="result ok">
      <div class="small muted">{{ testResult.chat_title }} · {{ testResult.lang }} · {{ testResult.device === 'cuda' ? t('whisper.gpu') : t('whisper.cpu') }}</div>
      <p>«{{ testResult.text || '…' }}»</p>
    </div>
    <div v-if="testError" class="result err small">{{ testError }}</div>
  </div>
</template>

<style scoped>
.wg { max-width: 760px; margin-bottom: 18px; display: flex; flex-direction: column; gap: 12px; }
.status { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.st { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-elev); min-width: 0; }
.st strong { font-size: 13px; }
.st-ico { width: 28px; height: 28px; flex: none; display: grid; place-items: center; border-radius: 8px; background: var(--bg-sunken); font-size: 14px; }
.st.ok .st-ico { background: var(--success-soft); color: var(--success); }
.st.no .st-ico { background: var(--warning-soft); color: var(--warning); }
.how { padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-elev); }
.how ol { margin: 8px 0 6px; padding-left: 20px; line-height: 1.9; color: var(--text-2); }
.how p { margin: 0; }
.cmd { display: inline-flex; align-items: center; gap: 4px; margin-left: 6px; padding: 0 4px 0 8px; border-radius: 6px; background: var(--bg-sunken); }
.cmd code { font-family: var(--mono); font-size: 12px; }
.okmark { color: var(--success); margin-left: 6px; font-weight: 600; }
.test { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px dashed var(--border-strong); border-radius: var(--radius-lg); }
.grow { flex: 1; }
.result { display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; border-radius: var(--radius); background: var(--bg-sunken); }
.result:has(.n-spin) { flex-direction: row; align-items: center; gap: 8px; }
.result.ok { border-left: 3px solid var(--success); }
.result.ok p { margin: 0; line-height: 1.5; }
.result.err { color: var(--danger); background: var(--danger-soft); }
.num { font-variant-numeric: tabular-nums; }
@media (max-width: 900px) { .status { grid-template-columns: repeat(2, 1fr); } }
</style>
