<script setup lang="ts">
import { NButton, NCard, NSpace } from 'naive-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'

/** Coach marks (docs/21): highlight an element + a short card. Own component instead of a dependency. */
const { t } = useI18n()
const app = useAppStore()
const chats = useChatsStore()
const router = useRouter()

interface Step {
  key: string
  route?: string
  target?: string
  module?: 'whisper' | 'playwright' | 'ai'
}
const allSteps: Step[] = [
  { key: 'welcome' },
  { key: 'chats', route: '/chats', target: '[data-tour="chat-list"]' },
  { key: 'export', route: '/chats', target: '[data-tour="export-btn"]' },
  { key: 'media', route: '/chats', target: '[data-tour="export-btn"]' },
  { key: 'split', route: '/chats', target: '[data-tour="export-btn"]' },
  { key: 'downloads', route: '/downloads', target: '[data-tour="downloads"]' },
  { key: 'search', route: '/search', target: '[data-tour="search-input"]' },
  { key: 'folders', route: '/chats', target: '[data-tour="folders"]' },
  { key: 'miniapps', route: '/miniapps', target: '[data-tour="page"]', module: 'playwright' },
  { key: 'monitor', route: '/monitor', target: '[data-tour="page"]', module: 'ai' },
  { key: 'try', route: '/chats', target: '[data-tour="export-btn"]' },
]
const steps = computed(() => allSteps)
const i = ref(0)
const step = computed(() => steps.value[i.value])
const rect = ref<DOMRect | null>(null)
let highlighted: Element | null = null

const moduleOff = computed(() => {
  const m = step.value.module
  if (m === 'playwright') return !app.modules.playwright
  if (m === 'ai') return !app.settings?.ai.enabled
  return false
})

async function show() {
  highlighted?.classList.remove('tga-highlight')
  highlighted = null
  rect.value = null
  if (step.value.route && router.currentRoute.value.path !== step.value.route) await router.push(step.value.route)
  if (!step.value.target) return
  for (let n = 0; n < 20; n++) {
    await nextTick()
    const el = document.querySelector(step.value.target)
    if (el) {
      // Instant scroll, then measure on the next frame so the card is placed against the final position.
      el.scrollIntoView({ block: 'nearest', behavior: 'auto' })
      el.classList.add('tga-highlight')
      highlighted = el
      await new Promise((r) => requestAnimationFrame(() => r(null)))
      measure()
      return
    }
    await new Promise((r) => setTimeout(r, 100))
  }
}

function measure() {
  if (highlighted) rect.value = highlighted.getBoundingClientRect()
}
watch(i, show)
onMounted(() => {
  show()
  window.addEventListener('resize', measure)
  document.addEventListener('scroll', measure, true)
})
onBeforeUnmount(() => {
  highlighted?.classList.remove('tga-highlight')
  window.removeEventListener('resize', measure)
  document.removeEventListener('scroll', measure, true)
})

async function finish() {
  highlighted?.classList.remove('tga-highlight')
  app.tutorialOpen = false
  if (!app.settings?.tutorial_done) await app.saveSettings({ tutorial_done: true })
}

async function tryIt() {
  // Step 11: real text-only export of Saved Messages.
  const saved = chats.items.find((c) => c.type === 'saved')
  if (saved) {
    await api.post('/export', { chat_ids: [saved.id], formats: ['md'], split: { mode: 'month' }, media_types: [], no_media: true, filters: {}, also_full: false, include_transcripts: true })
    await finish()
    router.push('/downloads')
  } else {
    await finish()
  }
}

const CARD_W = 360
const CARD_H = 210
const GAP = 14
/** Place the card below, above, right or left of the target - whichever fits; inside the target's bottom-right corner as a last resort. Always clamped to the viewport. */
const cardStyle = computed(() => {
  const r = rect.value
  if (!r) return { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }
  const vw = window.innerWidth
  const vh = window.innerHeight
  const clampX = (x: number) => Math.min(Math.max(12, x), vw - CARD_W - 12)
  const clampY = (y: number) => Math.min(Math.max(12, y), vh - CARD_H - 12)
  let left: number
  let top: number
  if (r.bottom + GAP + CARD_H < vh) [left, top] = [clampX(r.left), r.bottom + GAP]
  else if (r.top - GAP - CARD_H > 0) [left, top] = [clampX(r.left), r.top - GAP - CARD_H]
  else if (r.right + GAP + CARD_W < vw) [left, top] = [r.right + GAP, clampY(r.top)]
  else if (r.left - GAP - CARD_W > 0) [left, top] = [r.left - GAP - CARD_W, clampY(r.top)]
  else [left, top] = [clampX(Math.min(r.right, vw) - CARD_W - 24), clampY(Math.min(r.bottom, vh) - CARD_H - 24)]
  return { left: `${left}px`, top: `${top}px` }
})
</script>

<template>
  <div class="veil" v-if="!rect" @click.self="finish"></div>
  <NCard class="coach" :style="cardStyle" size="small">
    <div class="row">
      <span class="icon-chip sm">🧭</span>
      <span class="small muted grow">{{ t('tutorial.title') }}</span>
      <span class="small muted num">{{ i + 1 }} / {{ steps.length }}</span>
    </div>
    <p style="margin: 10px 0 12px; line-height: 1.5">
      {{ moduleOff ? t('tutorial.moduleOff') : t(`tutorial.steps.${step.key}`) }}
    </p>
    <div class="dots">
      <span v-for="(_, n) in steps" :key="n" :class="{ on: n === i }"></span>
    </div>
    <NSpace justify="space-between" style="margin-top: 10px">
      <NButton size="small" quaternary @click="finish">{{ t('tutorial.skip') }}</NButton>
      <NSpace size="small">
        <NButton size="small" :disabled="i === 0" @click="i--">{{ t('tutorial.back') }}</NButton>
        <NButton v-if="step.key === 'try'" size="small" type="primary" @click="tryIt">{{ t('tutorial.tryIt') }}</NButton>
        <NButton v-else-if="i < steps.length - 1" size="small" type="primary" @click="i++">{{ t('tutorial.next') }}</NButton>
        <NButton v-else size="small" type="primary" @click="finish">{{ t('tutorial.done') }}</NButton>
      </NSpace>
    </NSpace>
  </NCard>
</template>

<style scoped>
.veil {
  position: fixed;
  inset: 0;
  background: rgba(3, 6, 12, 0.55);
  z-index: 3000;
  animation: fadein 200ms var(--ease);
}
.coach {
  position: fixed;
  width: 360px;
  max-width: calc(100vw - 24px);
  z-index: 3002;
  box-shadow: var(--shadow-lg);
  border-radius: 14px;
  transition: left 250ms var(--ease), top 250ms var(--ease);
}
.dots {
  display: flex;
  gap: 5px;
}
.dots span {
  transition: width 200ms var(--ease), background 200ms var(--ease);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #8886;
}
.dots span.on {
  width: 16px;
  border-radius: 3px;
  background: var(--tga-blue);
}
@keyframes fadein {
  from {
    opacity: 0;
  }
}
</style>
