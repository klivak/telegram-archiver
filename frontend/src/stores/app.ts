import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError, events } from '@/api/client'
import type { AuthStatus, Settings } from '@/api/types'
import { i18n, setLocale } from '@/i18n'

export const useAppStore = defineStore('app', () => {
  const auth = ref<AuthStatus | null>(null)
  const settings = ref<Settings | null>(null)
  const modules = ref<{ whisper: boolean; playwright: boolean; cryptg: boolean }>({ whisper: false, playwright: false, cryptg: false })
  const wsConnected = ref(false)
  const backendDown = ref(false)
  /** The backend answers but rejects our token (server restarted with a new one). */
  const tokenInvalid = ref(false)
  const tutorialOpen = ref(false)
  const paletteOpen = ref(false)
  const update = ref<{ available: boolean; latest: string | null; url?: string } | null>(null)

  const ready = computed(() => auth.value?.authorized === true)
  const advanced = computed(() => settings.value?.advanced_mode === true)

  async function loadAuth() {
    try {
      auth.value = await api.get<AuthStatus>('/auth/status')
      backendDown.value = false
      tokenInvalid.value = false
    } catch (e) {
      backendDown.value = true
      tokenInvalid.value = e instanceof ApiError && e.status === 401
    }
  }

  async function loadSettings() {
    settings.value = await api.get<Settings>('/settings')
    setLocale(settings.value.language)
  }

  async function saveSettings(patch: Partial<Settings> | Record<string, unknown>) {
    settings.value = await api.put<Settings>('/settings', patch)
    setLocale(settings.value.language)
  }

  async function loadModules() {
    modules.value = await api.get('/modules')
  }

  async function checkUpdates() {
    try {
      update.value = await api.get('/updates')
    } catch {
      /* offline is fine */
    }
  }

  function init() {
    events.onStatus = (c) => {
      wsConnected.value = c
      // Lost the event stream: find out whether the server is gone or restarted with a new token (shows the matching screen).
      if (!c) loadAuth()
    }
    events.connect()
    events.on('auth.ready', () => loadAuth())
    events.on('reconnected', () => loadAuth())
  }

  const locale = computed(() => (i18n.global.locale as unknown as { value: string }).value)

  return { auth, settings, modules, wsConnected, backendDown, tokenInvalid, tutorialOpen, paletteOpen, update, ready, advanced, locale, loadAuth, loadSettings, saveSettings, loadModules, checkUpdates, init }
})
