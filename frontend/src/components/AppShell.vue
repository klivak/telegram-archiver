<script setup lang="ts">
import { NAlert, NButton, NDropdown, NResult, NSpin, NTooltip, useMessage, useNotification } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ApiError, events } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'
import { initials } from '@/utils'
import CommandPalette from './CommandPalette.vue'
import FloodBanner from './FloodBanner.vue'
import TutorialOverlay from './TutorialOverlay.vue'

const { t } = useI18n()
const app = useAppStore()
const jobs = useJobsStore()
const chats = useChatsStore()
const route = useRoute()
const router = useRouter()
const message = useMessage()
const notification = useNotification()
const booting = ref(true)
const collapsed = ref(localStorage.getItem('tga.sider') === '1')
watch(collapsed, (v) => localStorage.setItem('tga.sider', v ? '1' : '0'))
// the tour points at menu items, so show the full sidebar while it runs
const isCollapsed = computed(() => collapsed.value && !app.tutorialOpen)

const bare = computed(() => route.meta.bare === true || !app.ready)

interface NavItem {
  key: string
  icon: string
  badge?: number
}
const groups = computed<{ label: string; items: NavItem[] }[]>(() => [
  {
    label: t('nav.groupMain'),
    items: [
      { key: 'dashboard', icon: '🏠' },
      { key: 'chats', icon: '💬' },
      { key: 'search', icon: '🔎' },
    ],
  },
  {
    label: t('nav.groupArchive'),
    items: [
      { key: 'downloads', icon: '⬇️', badge: jobs.active.length },
      { key: 'miniapps', icon: '🧩' },
      { key: 'monitor', icon: '🔔' },
    ],
  },
])
const activeKey = computed(() => (route.name === 'chat' ? 'chats' : String(route.name ?? 'dashboard')))
const me = computed(() => app.auth?.me)
const meName = computed(() => [me.value?.first_name, me.value?.last_name].filter(Boolean).join(' ') || t('app.name'))

const THEME_NEXT = { auto: 'light', light: 'dark', dark: 'auto' } as const
const THEME_ICON = { auto: '🖥️', light: '☀️', dark: '🌙' } as const
const cycleTheme = () => app.setTheme(THEME_NEXT[app.themePref])

const helpMenu = computed(() => [
  { key: 'tour', label: t('tutorial.show') },
  { key: 'palette', label: `${t('palette.title')} · Ctrl+K` },
  { key: 'search', label: `${t('nav.search')} · /` },
])
function onHelp(k: string) {
  if (k === 'tour') app.tutorialOpen = true
  else if (k === 'palette') app.paletteOpen = true
  else router.push({ name: 'search', query: { focus: '1' } })
}

async function boot() {
  await app.loadAuth()
  booting.value = false
  if (app.backendDown) return
  if (!app.auth?.authorized) {
    if (route.name !== 'onboarding') router.replace('/onboarding')
    return
  }
  await afterLogin()
}

async function afterLogin() {
  await app.loadSettings()
  app.loadModules()
  jobs.start()
  chats.start()
  app.checkUpdates()
  if (route.name === 'onboarding') {
    while (app.celebrating) await new Promise((r) => setTimeout(r, 100))
    router.replace('/chats')
  }
  if (!app.settings?.tutorial_done) setTimeout(() => (app.tutorialOpen = true), 800)
}

watch(
  () => app.auth?.authorized,
  (v, old) => {
    if (v && !old && !booting.value) afterLogin()
    if (v === false && route.name !== 'onboarding') router.replace('/onboarding')
  },
)

function isTyping(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

async function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    app.paletteOpen = true
    return
  }
  if (isTyping(e) || !app.ready) return
  if (e.key === '/') {
    e.preventDefault()
    router.push({ name: 'search', query: { focus: '1' } })
  } else if (e.code === 'Space' && route.name === 'downloads') {
    e.preventDefault()
    const anyRunning = jobs.active.some((j) => j.status !== 'paused')
    await jobs.all(anyRunning ? 'pause' : 'resume')
    message.info(anyRunning ? t('downloads.pausedAll') : t('downloads.resumedAll'))
  }
}

// Last-resort handler: a failed API call in any view shows a translated message instead of failing silently.
function onRejection(e: PromiseRejectionEvent) {
  if (!(e.reason instanceof ApiError)) return
  e.preventDefault()
  const r = e.reason
  if (r.status === 401) app.loadAuth()
  else message.error(r.status === 422 ? t('common.invalid') : t('common.failed'))
}

let offs: (() => void)[] = []
onMounted(() => {
  boot()
  window.addEventListener('keydown', onKey)
  window.addEventListener('unhandledrejection', onRejection)
  offs = [
    events.on('notification', (ev) => {
      const code = ev.data?.code as string
      const text = t(`notify.${code}`)
      notification.info({ title: t('app.name'), content: text, duration: 8000 })
      if ('Notification' in window && Notification.permission === 'granted') new Notification(t('app.name'), { body: text })
    }),
    events.on('flood_wait', (ev) => message.warning(t('flood.toast', { time: humanWait(ev.data.seconds) }), { duration: 6000 })),
    events.on('auth.ready', () => app.loadAuth()),
  ]
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('unhandledrejection', onRejection)
  offs.forEach((f) => f())
})

function humanWait(s: number) {
  const m = Math.floor(s / 60)
  return m ? t('time.minSec', { m, s: s % 60 }) : t('time.sec', { s })
}

async function retryBackend() {
  booting.value = true
  await boot()
}
</script>

<template>
  <div v-if="booting" class="center-screen"><NSpin size="large" /></div>
  <div v-else-if="app.backendDown" class="center-screen">
    <NResult status="warning" :title="app.tokenInvalid ? t('app.tokenInvalidTitle') : t('app.backendDownTitle')" :description="app.tokenInvalid ? t('app.tokenInvalidText') : t('app.backendDownText')">
      <template #footer><NButton type="primary" @click="retryBackend">{{ t('common.retry') }}</NButton></template>
    </NResult>
  </div>
  <RouterView v-else-if="bare" v-slot="{ Component }">
    <Transition name="fade" mode="out-in"><component :is="Component" /></Transition>
  </RouterView>
  <div v-else class="shell" :class="{ collapsed: isCollapsed }">
    <aside class="sider">
      <div class="brand" @click="router.push('/')">
        <img src="/logo.png" alt="" width="30" height="30" />
        <span v-if="!isCollapsed" class="brand-name">Telegram Archiver</span>
      </div>
      <button class="collapse-btn" :title="isCollapsed ? t('nav.expand') : t('nav.collapse')" @click="collapsed = !isCollapsed">{{ isCollapsed ? '›' : '‹' }}</button>
      <nav class="nav" data-tour="nav">
        <div v-for="g in groups" :key="g.label" class="nav-group">
          <div v-if="!isCollapsed" class="nav-label">{{ g.label }}</div>
          <div v-else class="nav-sep"></div>
          <NTooltip v-for="it in g.items" :key="it.key" placement="right" :disabled="!isCollapsed">
            <template #trigger>
              <button class="nav-item" :class="{ active: activeKey === it.key }" @click="router.push({ name: it.key })">
                <span class="nav-icon">{{ it.icon }}</span>
                <span v-if="!isCollapsed" class="nav-text">{{ t(`nav.${it.key}`) }}</span>
                <span v-if="it.badge" class="nav-badge num">{{ it.badge > 99 ? '99+' : it.badge }}</span>
              </button>
            </template>
            {{ t(`nav.${it.key}`) }}
          </NTooltip>
        </div>
      </nav>
      <div class="sider-bottom">
        <NTooltip placement="right" :disabled="!isCollapsed">
          <template #trigger>
            <button class="nav-item" :class="{ active: activeKey === 'settings' }" @click="router.push({ name: 'settings' })">
              <span class="nav-icon">⚙️</span><span v-if="!isCollapsed" class="nav-text">{{ t('nav.settings') }}</span>
            </button>
          </template>
          {{ t('nav.settings') }}
        </NTooltip>
        <div class="tools" :class="{ vertical: isCollapsed }">
          <NTooltip>
            <template #trigger>
              <button class="tool" data-testid="theme-toggle" @click="cycleTheme">
                <span>{{ THEME_ICON[app.themePref] }}</span><span v-if="!isCollapsed" class="small">{{ t(`theme.${app.themePref}`) }}</span>
              </button>
            </template>
            {{ t('theme.toggle') }}
          </NTooltip>
          <NDropdown :options="helpMenu" placement="top-start" trigger="click" @select="onHelp">
            <button class="tool" :title="t('nav.help')"><span>?</span><span v-if="!isCollapsed" class="small">{{ t('nav.help') }}</span></button>
          </NDropdown>
        </div>
        <div class="account" :title="meName">
          <div class="avatar">
            {{ initials(meName) }}<span class="dot" :class="app.wsConnected ? 'ok' : 'bad'" :title="app.wsConnected ? t('app.connected') : t('app.reconnecting')"></span>
          </div>
          <div v-if="!isCollapsed" class="grow">
            <div class="acc-name ellipsis">{{ meName }}</div>
            <div class="small muted ellipsis">{{ me?.username ? '@' + me.username : app.wsConnected ? t('app.connected') : t('app.reconnecting') }}</div>
          </div>
        </div>
      </div>
    </aside>
    <main class="content">
      <div v-if="app.update?.available" class="banners">
        <NAlert type="info" closable :title="t('update.available', { v: app.update.latest })">
          <a :href="app.update.url" target="_blank" rel="noopener">{{ t('update.download') }}</a>
        </NAlert>
      </div>
      <FloodBanner />
      <div class="route-host">
        <RouterView v-slot="{ Component }">
          <Transition name="route" mode="out-in">
            <KeepAlive :include="['ChatsView']"><component :is="Component" /></KeepAlive>
          </Transition>
        </RouterView>
      </div>
    </main>
  </div>
  <CommandPalette v-if="app.ready" />
  <TutorialOverlay v-if="app.ready && app.tutorialOpen" />
</template>

<style scoped>
.center-screen {
  height: 100%;
  display: grid;
  place-items: center;
}
.shell {
  display: grid;
  grid-template-columns: var(--sider-w) minmax(0, 1fr);
  height: 100%;
  transition: grid-template-columns 220ms var(--ease);
}
.shell.collapsed {
  grid-template-columns: var(--sider-w-collapsed) minmax(0, 1fr);
}
.sider {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 14px 12px 12px;
  background: var(--bg-sunken);
  border-right: 1px solid var(--border);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px 16px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
}
.brand img {
  border-radius: 8px;
  flex: none;
}
.brand-name {
  font-weight: 650;
  letter-spacing: -0.01em;
  font-size: 15px;
}
.collapsed .brand {
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}
.collapse-btn {
  position: absolute;
  top: 20px;
  right: -12px;
  z-index: 5;
  width: 24px;
  height: 24px;
  padding: 0 0 2px;
  border-radius: 50%;
  border: 1px solid var(--border-strong);
  background: var(--bg-elev);
  color: var(--text-2);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  display: grid;
  place-items: center;
  box-shadow: var(--shadow-sm);
  opacity: 0;
  transition: opacity var(--dur) var(--ease), color var(--dur) var(--ease);
}
.sider:hover .collapse-btn,
.shell.collapsed .collapse-btn,
.collapse-btn:focus-visible {
  opacity: 1;
}
.collapse-btn:hover {
  color: var(--accent);
}
.nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.nav-group + .nav-group {
  margin-top: 14px;
}
.nav-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-3);
  padding: 6px 10px;
}
.nav-sep {
  height: 1px;
  background: var(--border);
  margin: 6px 8px 8px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 38px;
  padding: 0 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  margin: 1px 0;
  position: relative;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.nav-item.active {
  background: var(--bg-active);
  color: var(--text);
  font-weight: 600;
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 9px;
  bottom: 9px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--tga-blue);
}
.nav-icon {
  width: 22px;
  text-align: center;
  font-size: 16px;
  flex: none;
}
.nav-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-badge {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--tga-blue);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  display: grid;
  place-items: center;
}
.collapsed .nav-item {
  justify-content: center;
  padding: 0;
}
.collapsed .nav-badge {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 16px;
  height: 16px;
  font-size: 10px;
  padding: 0 4px;
}
.sider-bottom {
  border-top: 1px solid var(--border);
  padding-top: 10px;
  margin-top: 8px;
}
.tools {
  display: flex;
  gap: 6px;
  margin: 6px 0 8px;
}
.tools.vertical {
  flex-direction: column;
}
.tool {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  min-width: 0;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-elev);
  color: var(--text-2);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), color var(--dur) var(--ease);
}
.tool:hover {
  border-color: var(--border-strong);
  color: var(--text);
}
.account {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 6px 2px;
}
.collapsed .account {
  justify-content: center;
  padding: 8px 0 2px;
}
.avatar {
  position: relative;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 650;
  color: #fff;
  background: linear-gradient(135deg, var(--tga-blue), var(--tga-navy));
}
.acc-name {
  font-size: 13.5px;
  font-weight: 600;
}
.dot {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid var(--bg-sunken);
}
.dot.ok {
  background: var(--success);
}
.dot.bad {
  background: var(--danger);
}
.content {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--bg);
}
.banners {
  padding: 12px clamp(16px, 3vw, 40px) 0;
}
.route-host {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  position: relative;
}
</style>
