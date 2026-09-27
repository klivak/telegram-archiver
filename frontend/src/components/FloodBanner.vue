<script setup lang="ts">
import { NAlert } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useJobsStore } from '@/stores/jobs'
import { formatDuration, secondsUntil } from '@/utils'

const { t } = useI18n()
const jobs = useJobsStore()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => (timer = setInterval(() => (now.value = Date.now()), 1000)))
onBeforeUnmount(() => clearInterval(timer))

const waiting = computed(() => {
  void now.value
  const j = jobs.floodWaiting[0]
  if (!j) return null
  const left = secondsUntil(j.wait_until)
  const d = formatDuration(left)
  const takeout = j.progress?.flood_what === 'takeout'
  return { left, text: d.h ? t('time.hMin', { h: d.h, m: d.m }) : d.m ? t('time.minSec', { m: d.m, s: d.s }) : t('time.sec', { s: d.s }), takeout }
})
</script>

<template>
  <NAlert v-if="waiting" type="warning" style="margin: 12px clamp(16px, 3vw, 40px) 0" :title="t('flood.title', { time: waiting.text })">
    {{ waiting.takeout ? t('flood.takeout') : t('flood.text') }}
  </NAlert>
</template>
