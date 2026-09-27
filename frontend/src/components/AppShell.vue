<script setup lang="ts">
import { NAlert, NBadge, NButton, NLayout, NLayoutContent, NLayoutSider, NMenu, NResult, NSpin, NTooltip, useMessage, useNotification, type MenuOption } from 'naive-ui'
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ApiError, events } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'
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

const bare = computed(() => route.meta.bare === true || !app.ready)

const icon = (e: string) => () => h('span', { style: 'font-size:18px' }, e)
const menu = computed<MenuOption[]>(() => [
  { key: 'dashboard', label: t('nav.dashboard'), icon: icon('🏠') },
  { key: 'chats', label: t('nav.chats'), icon: icon('💬') },
  {
    key: 'downloads',
    label: () => h('span', { class: 'row' }, [t('nav.downloads'), jobs.active.length ? h(NBadge, { value: jobs.active.length, max: 99, type: 'info' }) : null]),
    icon: icon('⬇️'),
  },
  { key: 'search', label: t('nav.search'), icon: icon('🔎') },
  { key: 'miniapps', label: t('nav.miniapps'), icon: icon('🧩') },
  { key: 'monitor', label: t('nav.monitor'), icon: icon('🔔') },
  { key: 'settings', label: t('nav.settings'), icon: icon('⚙️') },
])
const activeKey = computed(() => (route.name === 'chat' ? 'chats' : String(route.name ?? 'dashboard')))

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
  if (route.name === 'onboarding') router.replace('/chats')
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
  <div v-if="booting" style="height: 100%; display: grid; place-items: center"><NSpin size="large" /></div>
  <div v-else-if="app.backendDown" style="height: 100%; display: grid; place-items: center">
    <NResult status="warning" :title="app.tokenInvalid ? t('app.tokenInvalidTitle') : t('app.backendDownTitle')" :description="app.tokenInvalid ? t('app.tokenInvalidText') : t('app.backendDownText')">
      <template #footer><NButton type="primary" @click="retryBackend">{{ t('common.retry') }}</NButton></template>
    </NResult>
  </div>
  <RouterView v-else-if="bare" />
  <NLayout v-else has-sider style="height: 100%">
    <NLayoutSider bordered collapse-mode="width" :collapsed-width="64" :width="220" :collapsed="collapsed" show-trigger @collapse="collapsed = true" @expand="collapsed = false" :native-scrollbar="false">
      <div class="brand" :class="{ collapsed }" @click="router.push('/')">
        <img src="/logo.png" alt="" width="32" height="32" />
        <span v-if="!collapsed">Telegram Archiver</span>
      </div>
      <NMenu :value="activeKey" :options="menu" :collapsed="collapsed" :collapsed-width="64" @update:value="(k: string) => router.push({ name: k })" data-tour="nav" />
      <div class="sider-foot" v-if="!collapsed">
        <NTooltip>
          <template #trigger>
            <span class="dot" :class="app.wsConnected ? 'ok' : 'bad'"></span>
          </template>
          {{ app.wsConnected ? t('app.connected') : t('app.reconnecting') }}
        </NTooltip>
        <span class="small muted ellipsis">{{ app.auth?.me?.first_name }} {{ app.auth?.me?.username ? '@' + app.auth.me.username : '' }}</span>
        <NButton quaternary circle size="small" :title="t('tutorial.show')" @click="app.tutorialOpen = true">?</NButton>
      </div>
    </NLayoutSider>
    <NLayoutContent :native-scrollbar="false" content-style="min-height:100%">
      <NAlert v-if="app.update?.available" type="info" closable style="margin: 8px 16px 0" :title="t('update.available', { v: app.update.latest })">
        <a :href="app.update.url" target="_blank" rel="noopener">{{ t('update.download') }}</a>
      </NAlert>
      <FloodBanner />
      <RouterView v-slot="{ Component }">
        <KeepAlive :include="['ChatsView']"><component :is="Component" /></KeepAlive>
      </RouterView>
    </NLayoutContent>
  </NLayout>
  <CommandPalette v-if="app.ready" />
  <TutorialOverlay v-if="app.ready && app.tutorialOpen" />
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  font-weight: 700;
  cursor: pointer;
}
.brand.collapsed {
  justify-content: center;
  padding: 14px 0;
}
.sider-foot {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex: none;
}
.dot.ok {
  background: #18a058;
}
.dot.bad {
  background: #d03050;
}
</style>
