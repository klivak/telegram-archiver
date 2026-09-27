<script setup lang="ts">
import { NInput, NModal } from 'naive-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useChatsStore } from '@/stores/chats'
import { useJobsStore } from '@/stores/jobs'

const { t } = useI18n()
const app = useAppStore()
const chats = useChatsStore()
const jobs = useJobsStore()
const router = useRouter()
const q = ref('')
const sel = ref(0)
const input = ref<InstanceType<typeof NInput> | null>(null)

interface Cmd {
  label: string
  hint?: string
  run: () => void
}

const commands = computed<Cmd[]>(() => [
  { label: t('nav.dashboard'), run: () => router.push('/') },
  { label: t('nav.chats'), run: () => router.push('/chats') },
  { label: t('nav.downloads'), run: () => router.push('/downloads') },
  { label: t('nav.search'), run: () => router.push({ name: 'search', query: { q: q.value } }) },
  { label: t('nav.miniapps'), run: () => router.push('/miniapps') },
  { label: t('nav.monitor'), run: () => router.push('/monitor') },
  { label: t('nav.settings'), run: () => router.push('/settings') },
  { label: t('palette.refreshChats'), run: () => chats.refresh() },
  { label: t('palette.pauseAll'), run: () => jobs.all('pause') },
  { label: t('palette.resumeAll'), run: () => jobs.all('resume') },
  { label: t('tutorial.show'), run: () => (app.tutorialOpen = true) },
])

const results = computed<Cmd[]>(() => {
  const s = q.value.trim().toLowerCase()
  const cmds = commands.value.filter((c) => !s || c.label.toLowerCase().includes(s))
  const chatHits: Cmd[] = s
    ? chats.items
        .filter((c) => c.title.toLowerCase().includes(s) || (c.username ?? '').toLowerCase().includes(s))
        .slice(0, 8)
        .map((c) => ({ label: c.title, hint: t(`chatType.${c.type}`), run: () => router.push(`/chats/${c.id}`) }))
    : []
  const searchCmd: Cmd[] = s ? [{ label: t('palette.searchFor', { q: q.value }), run: () => router.push({ name: 'search', query: { q: q.value } }) }] : []
  return [...chatHits, ...cmds, ...searchCmd].slice(0, 14)
})

watch(
  () => app.paletteOpen,
  async (v) => {
    if (v) {
      q.value = ''
      sel.value = 0
      await nextTick()
      input.value?.focus()
    }
  },
)
watch(q, () => (sel.value = 0))

function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    sel.value = Math.min(sel.value + 1, results.value.length - 1)
    e.preventDefault()
  } else if (e.key === 'ArrowUp') {
    sel.value = Math.max(sel.value - 1, 0)
    e.preventDefault()
  } else if (e.key === 'Enter') {
    run(results.value[sel.value])
  }
}

function run(c?: Cmd) {
  if (!c) return
  app.paletteOpen = false
  c.run()
}
</script>

<template>
  <NModal v-model:show="app.paletteOpen" preset="card" style="width: 560px; max-width: 94vw" :title="t('palette.title')" size="small">
    <NInput ref="input" v-model:value="q" :placeholder="t('palette.placeholder')" clearable @keydown="onKey" />
    <div class="list">
      <div v-for="(c, i) in results" :key="i" class="item" :class="{ sel: i === sel }" @mouseenter="sel = i" @click="run(c)">
        <span class="grow ellipsis">{{ c.label }}</span>
        <span v-if="c.hint" class="small muted">{{ c.hint }}</span>
      </div>
    </div>
    <div class="small muted" style="margin-top: 8px">↑↓ Enter · Esc · Ctrl+K · / {{ t('nav.search') }}</div>
  </NModal>
</template>

<style scoped>
.list {
  margin-top: 8px;
  max-height: 50vh;
  overflow: auto;
}
.item {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.item.sel {
  background: rgba(42, 171, 238, 0.15);
}
</style>
