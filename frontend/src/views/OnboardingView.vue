<script setup lang="ts">
import { NAlert, NButton, NForm, NFormItem, NInput, NInputNumber, NSpin } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const phone = ref(app.auth?.phone ?? '')
const code = ref('')
const password = ref('')
const stage = ref<'config' | 'login' | 'password'>(app.auth?.configured ? 'login' : 'config')
const method = ref<'qr' | 'phone'>('qr')
const codeSent = ref(false)
const qrFailed = ref(false)
const hint = ref<string | null>(null)
const waitLeft = ref(0)
let waitTimer: ReturnType<typeof setInterval> | undefined

const step = computed(() => (success.value ? 4 : stage.value === 'config' ? 1 : stage.value === 'login' ? 2 : 3))
const steps = computed(() => [t('onboarding.stepKeys'), t('onboarding.stepLogin'), t('onboarding.step2fa')])
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

// Success animation before the shell takes over (AppShell waits for `celebrating` to clear).
const success = ref(false)
watch(
  () => app.auth?.authorized,
  (v) => {
    if (!v || success.value) return
    success.value = true
    app.celebrating = true
    setTimeout(() => (app.celebrating = false), 1500)
  },
)

/** Paste-friendly: keep only digits ("Login code: 12 345" -> "12345"). */
function onCode(v: string) {
  code.value = v.replace(/\D/g, '').slice(0, 8)
}
function setMethod(m: 'qr' | 'phone') {
  if (method.value === m) return
  method.value = m
  error.value = ''
  if (m === 'qr') startQr()
}

function toggleLang() {
  setLocale(locale.value === 'uk' ? 'en' : 'uk')
}
</script>

<template>
  <div class="onb">
    <div class="glow" aria-hidden="true"></div>
    <div class="topbar">
      <NButton quaternary size="small" @click="toggleLang">🌐 {{ locale === 'uk' ? 'English' : 'Українська' }}</NButton>
    </div>

    <div class="card surface" :class="{ wide: stage === 'login' && !success }">
      <header class="head">
        <img src="/logo.png" width="52" height="52" alt="" class="logo" />
        <div>
          <h1>Telegram Archiver</h1>
          <div class="muted">{{ t('onboarding.tagline') }}</div>
        </div>
      </header>

      <ol class="stepper" :aria-label="t('onboarding.progress')">
        <li v-for="(label, n) in steps" :key="n" :class="{ done: step > n + 1, on: step === n + 1 }">
          <span class="bullet num">{{ step > n + 1 ? '✓' : n + 1 }}</span>
          <span class="label">{{ label }}</span>
        </li>
      </ol>

      <Transition name="fade">
        <NAlert v-if="error && !success" type="error" class="msg" closable @close="error = ''">{{ error }}</NAlert>
      </Transition>
      <Transition name="fade">
        <NAlert v-if="waitLeft && !success" type="warning" class="msg">{{ t('flood.title', { time: t('time.sec', { s: waitLeft }) }) }}</NAlert>
      </Transition>

      <Transition name="slide" mode="out-in">
        <!-- success -->
        <section v-if="success" key="ok" class="success">
          <svg viewBox="0 0 52 52" class="check" aria-hidden="true">
            <circle cx="26" cy="26" r="24" />
            <path d="M15 27 l7 7 l15 -15" />
          </svg>
          <h2>{{ t('onboarding.successTitle') }}</h2>
          <p class="muted">{{ t('onboarding.successText') }}</p>
        </section>

        <!-- step 1: api keys -->
        <section v-else-if="stage === 'config'" key="config">
          <h2>{{ t('onboarding.keysTitle') }}</h2>
          <ol class="howto">
            <li>{{ t('onboarding.keysStep1') }} <a href="https://my.telegram.org" target="_blank" rel="noopener">my.telegram.org</a></li>
            <li>{{ t('onboarding.keysStep2') }}</li>
            <li>{{ t('onboarding.keysStep3') }}</li>
          </ol>
          <NForm @submit.prevent="saveConfig">
            <NFormItem label="api_id">
              <NInputNumber v-model:value="apiId" :show-button="false" placeholder="1234567" style="width: 100%" size="large" autofocus />
            </NFormItem>
            <NFormItem label="api_hash" :feedback="apiHash && !hashValid ? t('onboarding.hashInvalid') : ''" :validation-status="apiHash && !hashValid ? 'error' : undefined">
              <NInput v-model:value="apiHash" type="password" show-password-on="click" placeholder="0123456789abcdef0123456789abcdef" size="large" />
            </NFormItem>
            <NButton type="primary" block size="large" :loading="busy" :disabled="!apiId || !hashValid" attr-type="submit">{{ t('common.continue') }}</NButton>
          </NForm>
          <p class="small muted privacy">🔒 {{ t('onboarding.keysPrivacy') }}</p>
        </section>

        <!-- step 2: login -->
        <section v-else-if="stage === 'login'" key="login">
          <div class="seg" role="tablist">
            <button role="tab" :class="{ on: method === 'qr' }" :aria-selected="method === 'qr'" @click="setMethod('qr')">▦ {{ t('onboarding.qrTab') }}</button>
            <button role="tab" :class="{ on: method === 'phone' }" :aria-selected="method === 'phone'" @click="setMethod('phone')">📱 {{ t('onboarding.phoneTab') }}</button>
          </div>

          <Transition name="fade" mode="out-in">
            <div v-if="method === 'qr'" key="qr" class="qr-layout">
              <div class="qr-box">
                <div v-if="qr" class="qr-img" v-html="qr.svg"></div>
                <div v-else class="qr-img placeholder">
                  <NButton v-if="qrFailed" :disabled="waitLeft > 0" @click="startQr">{{ t('common.retry') }}</NButton>
                  <NSpin v-else />
                </div>
                <p class="small muted center">{{ t('onboarding.qrRefresh') }}</p>
              </div>
              <div class="qr-help">
                <ol class="howto">
                  <li>{{ t('onboarding.qrStep1') }}</li>
                  <li>{{ t('onboarding.qrStep2') }}</li>
                  <li>{{ t('onboarding.qrStep3') }}</li>
                </ol>
                <!-- animated hint: where "Link Desktop Device" lives on the phone -->
                <div class="phone" aria-hidden="true">
                  <div class="notch"></div>
                  <div class="screen">
                    <div class="ph-row a"><span>⚙️</span>{{ t('onboarding.hintSettings') }}</div>
                    <div class="ph-row b"><span>💻</span>{{ t('onboarding.hintDevices') }}</div>
                    <div class="ph-row c"><span>＋</span>{{ t('onboarding.hintLink') }}</div>
                  </div>
                </div>
              </div>
            </div>

            <div v-else key="phone" class="phone-form">
              <NForm v-if="!codeSent" @submit.prevent="sendCode">
                <NFormItem :label="t('onboarding.phone')">
                  <NInput v-model:value="phone" placeholder="+380..." size="large" autofocus :input-props="{ autocomplete: 'tel', inputmode: 'tel' }" />
                </NFormItem>
                <NButton type="primary" block size="large" :loading="busy" :disabled="phone.length < 7 || waitLeft > 0" attr-type="submit">{{ t('onboarding.sendCode') }}</NButton>
              </NForm>
              <NForm v-else @submit.prevent="verifyCode">
                <NFormItem :label="t('onboarding.code')" :feedback="t('onboarding.codeHint')">
                  <NInput :value="code" class="code-input" placeholder="12345" size="large" autofocus :input-props="{ autocomplete: 'one-time-code', inputmode: 'numeric' }" @update:value="onCode" />
                </NFormItem>
                <NButton type="primary" block size="large" :loading="busy" :disabled="code.length < 4 || waitLeft > 0" attr-type="submit">{{ t('onboarding.signIn') }}</NButton>
                <NButton quaternary block style="margin-top: 6px" @click="codeSent = false">{{ t('onboarding.resend') }}</NButton>
              </NForm>
            </div>
          </Transition>
          <div class="foot-actions">
            <NButton quaternary size="small" @click="stage = 'config'">← {{ t('onboarding.changeKeys') }}</NButton>
          </div>
        </section>

        <!-- step 3: 2FA -->
        <section v-else key="password">
          <h2>{{ t('onboarding.passwordTitle') }}</h2>
          <p class="muted small">{{ t('onboarding.passwordText') }}</p>
          <NForm @submit.prevent="sendPassword">
            <NFormItem :label="t('onboarding.password')" :feedback="hint ? t('onboarding.hint', { hint }) : ''">
              <NInput v-model:value="password" type="password" show-password-on="click" size="large" autofocus :input-props="{ autocomplete: 'current-password' }" />
            </NFormItem>
            <NButton type="primary" block size="large" :loading="busy" :disabled="!password || waitLeft > 0" attr-type="submit">{{ t('onboarding.signIn') }}</NButton>
          </NForm>
        </section>
      </Transition>
    </div>

    <p class="small muted disclaimer">{{ t('onboarding.disclaimer') }}</p>
  </div>
</template>

<style scoped>
.onb {
  position: relative;
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 56px 16px 24px;
  background: var(--bg);
}
.onb > .card {
  margin-top: auto;
}
.onb > .disclaimer {
  margin-bottom: auto;
}
.glow {
  position: fixed;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(600px 400px at 50% 0%, rgba(42, 171, 238, 0.16), transparent 70%), radial-gradient(500px 300px at 90% 100%, rgba(11, 58, 91, 0.25), transparent 70%);
}
.topbar {
  position: fixed;
  top: 12px;
  right: 16px;
  z-index: 2;
}
.card {
  position: relative;
  width: 100%;
  max-width: 480px;
  padding: 28px 32px 24px;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  transition: max-width 300ms var(--ease);
  animation: rise 400ms var(--ease) both;
}
.card.wide {
  max-width: 760px;
}
.head {
  display: flex;
  align-items: center;
  gap: 14px;
}
.logo {
  border-radius: 14px;
  box-shadow: 0 6px 20px -6px rgba(42, 171, 238, 0.6);
}
.head h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.02em;
}
h2 {
  font-size: 17px;
  font-weight: 600;
  margin: 0 0 8px;
}
.stepper {
  list-style: none;
  display: flex;
  gap: 8px;
  padding: 0;
  margin: 22px 0 20px;
}
.stepper li {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-3);
  padding-top: 10px;
  border-top: 3px solid var(--border);
  transition: border-color 300ms var(--ease), color 300ms var(--ease);
}
.stepper li.on {
  border-color: var(--tga-blue);
  color: var(--text);
  font-weight: 600;
}
.stepper li.done {
  border-color: var(--success);
  color: var(--text-2);
}
.bullet {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-sunken);
  border: 1px solid var(--border-strong);
  flex: none;
}
.on .bullet {
  background: var(--tga-blue);
  border-color: var(--tga-blue);
  color: #fff;
}
.done .bullet {
  background: var(--success);
  border-color: var(--success);
  color: #fff;
}
.msg {
  margin-bottom: 14px;
}
.howto {
  padding-left: 20px;
  line-height: 1.7;
  margin: 8px 0 18px;
  color: var(--text-2);
}
.privacy {
  margin: 14px 0 0;
}
.seg {
  display: flex;
  padding: 4px;
  gap: 4px;
  border-radius: 10px;
  background: var(--bg-sunken);
  border: 1px solid var(--border);
  margin-bottom: 20px;
}
.seg button {
  flex: 1;
  height: 36px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.seg button.on {
  background: var(--bg-elev);
  outline: 1px solid var(--border-strong);
  color: var(--text);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}
.qr-layout {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 28px;
  align-items: center;
}
.qr-box {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.qr-img {
  width: 260px;
  height: 260px;
  background: #fff;
  border-radius: 16px;
  padding: 10px;
  box-shadow: 0 0 0 1px var(--border), 0 10px 30px -10px rgba(42, 171, 238, 0.45);
  animation: rise 300ms var(--ease) both;
}
.qr-img.placeholder {
  display: grid;
  place-items: center;
  background: var(--bg-sunken);
}
.qr-img :deep(svg) {
  width: 100%;
  height: 100%;
}
.center {
  text-align: center;
  margin: 10px 0 0;
}
.qr-help .howto {
  margin-top: 0;
}
.phone {
  width: 200px;
  border-radius: 24px;
  border: 2px solid var(--border-strong);
  padding: 10px 8px 14px;
  background: var(--bg-sunken);
}
.notch {
  width: 56px;
  height: 6px;
  border-radius: 6px;
  margin: 0 auto 10px;
  background: var(--border-strong);
}
.screen {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ph-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 7px 8px;
  border-radius: 8px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  animation: tap 4.5s var(--ease) infinite;
}
.ph-row span {
  width: 18px;
  text-align: center;
}
.ph-row.b {
  animation-delay: 1.5s;
}
.ph-row.c {
  animation-delay: 3s;
  font-weight: 600;
}
@keyframes tap {
  0%,
  8% {
    border-color: var(--border);
    box-shadow: none;
  }
  12%,
  28% {
    border-color: var(--tga-blue);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  34%,
  100% {
    border-color: var(--border);
    box-shadow: none;
  }
}
.phone-form {
  max-width: 400px;
  margin: 0 auto;
}
.code-input :deep(input) {
  letter-spacing: 0.4em;
  font-size: 20px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.foot-actions {
  margin-top: 16px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.success {
  text-align: center;
  padding: 12px 0 8px;
}
.check {
  width: 76px;
  height: 76px;
  margin-bottom: 10px;
}
.check circle {
  fill: none;
  stroke: var(--success);
  stroke-width: 3;
  stroke-dasharray: 151;
  stroke-dashoffset: 151;
  animation: draw 500ms var(--ease) forwards;
}
.check path {
  fill: none;
  stroke: var(--success);
  stroke-width: 4;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 40;
  stroke-dashoffset: 40;
  animation: draw 350ms 450ms var(--ease) forwards;
}
@keyframes draw {
  to {
    stroke-dashoffset: 0;
  }
}
.disclaimer {
  position: relative;
  max-width: 480px;
  margin-top: 18px;
  text-align: center;
}
@media (max-width: 720px) {
  .qr-layout {
    grid-template-columns: 1fr;
    justify-items: center;
  }
  .phone {
    display: none;
  }
  .card {
    padding: 22px 18px;
  }
}
</style>
