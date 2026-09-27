<script setup lang="ts">
import { NAlert, NButton, NCard, NForm, NFormItem, NInput, NInputNumber, NSpin, NSteps, NStep, NTabPane, NTabs } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, ApiError, events } from '@/api/client'
import type { AuthStatus } from '@/api/types'
import { setLocale } from '@/i18n'
import { useAppStore } from '@/stores/app'

const { t, te, locale } = useI18n()
const app = useAppStore()
const apiId = ref<number | null>(app.auth?.api_id ?? null)
const apiHash = ref('')
const busy = ref(false)
const error = ref('')
const qr = ref<{ svg: string; expires: string } | null>(null)
const phone = ref('')
const code = ref('')
const password = ref('')
const stage = ref<'config' | 'login' | 'password'>(app.auth?.configured ? 'login' : 'config')
const method = ref<'qr' | 'phone'>('qr')
const codeSent = ref(false)
const qrFailed = ref(false)
const hint = ref<string | null>(null)
const waitLeft = ref(0)
let waitTimer: ReturnType<typeof setInterval> | undefined

const step = computed(() => (stage.value === 'config' ? 1 : stage.value === 'login' ? 2 : 3))
const hashValid = computed(() => /^[0-9a-fA-F]{32}$/.test(apiHash.value.trim()))

function showError(e: unknown) {
  if (e instanceof ApiError) {
    const d = e.detail as { code?: string; seconds?: number } | undefined
    if (d?.code === 'flood_wait' && d.seconds) {
      startWait(d.seconds)
      error.value = ''
      return
    }
    error.value = errorText(d?.code)
  } else error.value = t('auth.errors.unknown')
}

function errorText(code?: string) {
  return code && te(`auth.errors.${code}`) ? t(`auth.errors.${code}`) : t('auth.errors.unknown')
}

function startWait(s: number) {
  waitLeft.value = s
  clearInterval(waitTimer)
  waitTimer = setInterval(() => {
    waitLeft.value = Math.max(0, waitLeft.value - 1)
    if (!waitLeft.value) clearInterval(waitTimer)
  }, 1000)
}

async function saveConfig() {
  busy.value = true
  error.value = ''
  try {
    app.auth = await api.post<AuthStatus>('/auth/config', { api_id: apiId.value, api_hash: apiHash.value.trim() })
    stage.value = 'login'
    await startQr()
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}

async function startQr() {
  method.value = 'qr'
  error.value = ''
  qr.value = null
  qrFailed.value = false
  try {
    qr.value = await api.post('/auth/qr/start')
  } catch (e) {
    qrFailed.value = true
    showError(e)
  }
}

async function sendCode() {
  busy.value = true
  error.value = ''
  try {
    await api.post('/auth/qr/cancel')
    await api.post('/auth/phone/send', { phone: phone.value })
    codeSent.value = true
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}

async function verifyCode() {
  busy.value = true
  error.value = ''
  try {
    const r = await api.post<{ state: string; hint?: string }>('/auth/phone/verify', { code: code.value })
    if (r.state === 'password') {
      stage.value = 'password'
      hint.value = r.hint ?? null
    } else await app.loadAuth() // do not depend on the WS auth.ready event alone
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}

async function sendPassword() {
  busy.value = true
  error.value = ''
  try {
    await api.post('/auth/password', { password: password.value })
    password.value = ''
    await app.loadAuth()
  } catch (e) {
    showError(e)
  } finally {
    busy.value = false
  }
}

let offs: (() => void)[] = []
onMounted(() => {
  offs = [
    events.on('auth.qr', (ev) => (qr.value = ev.data)),
    events.on('auth.password_needed', (ev) => {
      stage.value = 'password'
      hint.value = ev.data?.hint ?? null
    }),
    events.on('auth.error', (ev) => {
      qr.value = null // the QR loop on the server has stopped; offer a retry
      qrFailed.value = true
      if (ev.data?.code === 'flood_wait') startWait(ev.data.seconds)
      else error.value = errorText(ev.data?.code)
    }),
    events.on('auth.ready', () => app.loadAuth()),
  ]
  if (stage.value === 'login') startQr()
})
onBeforeUnmount(() => {
  offs.forEach((f) => f())
  clearInterval(waitTimer)
})

function toggleLang() {
  setLocale(locale.value === 'uk' ? 'en' : 'uk')
}
</script>

<template>
  <div class="wrap">
    <div class="head">
      <img src="/logo.png" width="48" height="48" alt="" />
      <div class="grow">
        <h1>Telegram Archiver</h1>
        <div class="muted">{{ t('onboarding.tagline') }}</div>
      </div>
      <NButton quaternary size="small" @click="toggleLang">{{ locale === 'uk' ? 'English' : 'Українська' }}</NButton>
    </div>
    <NSteps :current="step" size="small" style="margin: 16px 0 20px">
      <NStep :title="t('onboarding.stepKeys')" />
      <NStep :title="t('onboarding.stepLogin')" />
      <NStep :title="t('onboarding.step2fa')" />
    </NSteps>

    <NAlert v-if="error" type="error" style="margin-bottom: 12px" closable @close="error = ''">{{ error }}</NAlert>
    <NAlert v-if="waitLeft" type="warning" style="margin-bottom: 12px">{{ t('flood.title', { time: t('time.sec', { s: waitLeft }) }) }}</NAlert>

    <NCard v-if="stage === 'config'" :title="t('onboarding.keysTitle')">
      <ol class="howto">
        <li>{{ t('onboarding.keysStep1') }} <a href="https://my.telegram.org" target="_blank" rel="noopener">my.telegram.org</a></li>
        <li>{{ t('onboarding.keysStep2') }}</li>
        <li>{{ t('onboarding.keysStep3') }}</li>
      </ol>
      <NForm @submit.prevent="saveConfig">
        <NFormItem label="api_id">
          <NInputNumber v-model:value="apiId" :show-button="false" placeholder="1234567" style="width: 100%" />
        </NFormItem>
        <NFormItem label="api_hash" :feedback="apiHash && !hashValid ? t('onboarding.hashInvalid') : ''" :validation-status="apiHash && !hashValid ? 'error' : undefined">
          <NInput v-model:value="apiHash" type="password" show-password-on="click" placeholder="0123456789abcdef0123456789abcdef" />
        </NFormItem>
        <NButton type="primary" block :loading="busy" :disabled="!apiId || !hashValid" attr-type="submit">{{ t('common.continue') }}</NButton>
      </NForm>
      <p class="small muted">{{ t('onboarding.keysPrivacy') }}</p>
    </NCard>

    <NCard v-else-if="stage === 'login'">
      <NTabs v-model:value="method" type="segment" @update:value="(v: string) => v === 'qr' && startQr()">
        <NTabPane name="qr" :tab="t('onboarding.qrTab')">
          <div class="qr">
            <div v-if="qr" class="qr-img" v-html="qr.svg"></div>
            <NButton v-else-if="qrFailed" :disabled="waitLeft > 0" @click="startQr">{{ t('common.retry') }}</NButton>
            <NSpin v-else />
            <ol class="howto">
              <li>{{ t('onboarding.qrStep1') }}</li>
              <li>{{ t('onboarding.qrStep2') }}</li>
              <li>{{ t('onboarding.qrStep3') }}</li>
            </ol>
            <p class="small muted">{{ t('onboarding.qrRefresh') }}</p>
          </div>
        </NTabPane>
        <NTabPane name="phone" :tab="t('onboarding.phoneTab')">
          <NForm v-if="!codeSent" @submit.prevent="sendCode">
            <NFormItem :label="t('onboarding.phone')"><NInput v-model:value="phone" placeholder="+380..." /></NFormItem>
            <NButton type="primary" block :loading="busy" :disabled="phone.length < 7 || waitLeft > 0" attr-type="submit">{{ t('onboarding.sendCode') }}</NButton>
          </NForm>
          <NForm v-else @submit.prevent="verifyCode">
            <NFormItem :label="t('onboarding.code')" :feedback="t('onboarding.codeHint')"><NInput v-model:value="code" placeholder="12345" /></NFormItem>
            <NButton type="primary" block :loading="busy" :disabled="code.length < 4 || waitLeft > 0" attr-type="submit">{{ t('onboarding.signIn') }}</NButton>
            <NButton quaternary block style="margin-top: 6px" @click="codeSent = false">{{ t('onboarding.resend') }}</NButton>
          </NForm>
        </NTabPane>
      </NTabs>
      <NButton quaternary size="small" style="margin-top: 8px" @click="stage = 'config'">{{ t('onboarding.changeKeys') }}</NButton>
    </NCard>

    <NCard v-else :title="t('onboarding.passwordTitle')">
      <NForm @submit.prevent="sendPassword">
        <NFormItem :label="t('onboarding.password')" :feedback="hint ? t('onboarding.hint', { hint }) : ''">
          <NInput v-model:value="password" type="password" show-password-on="click" />
        </NFormItem>
        <NButton type="primary" block :loading="busy" :disabled="!password || waitLeft > 0" attr-type="submit">{{ t('onboarding.signIn') }}</NButton>
      </NForm>
    </NCard>

    <p class="small muted disclaimer">{{ t('onboarding.disclaimer') }}</p>
  </div>
</template>

<style scoped>
.wrap {
  max-width: 520px;
  margin: 0 auto;
  padding: 32px 16px;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.head h1 {
  margin: 0;
  font-size: 22px;
}
.howto {
  padding-left: 18px;
  line-height: 1.6;
}
.qr {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 8px;
}
.qr-img {
  width: 240px;
  height: 240px;
  background: #fff;
  border-radius: 12px;
  padding: 8px;
}
.qr-img :deep(svg) {
  width: 100%;
  height: 100%;
}
.disclaimer {
  margin-top: 16px;
  text-align: center;
}
</style>
