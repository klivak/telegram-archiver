<script setup lang="ts">
import { darkTheme, dateEnUS, dateUkUA, enUS, NConfigProvider, NDialogProvider, NGlobalStyle, NLoadingBarProvider, NMessageProvider, NNotificationProvider, ukUA, type GlobalThemeOverrides } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppShell from './components/AppShell.vue'
import { useAppStore } from './stores/app'

const app = useAppStore()
const { locale } = useI18n()
const systemDark = ref(window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false)
window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', (e) => (systemDark.value = e.matches))

const dark = computed(() => {
  const t = app.settings?.theme ?? (localStorage.getItem('tga.theme') as string | null) ?? 'auto'
  return t === 'dark' || (t === 'auto' && systemDark.value)
})
const overrides: GlobalThemeOverrides = {
  common: { primaryColor: '#2AABEE', primaryColorHover: '#4cbcf2', primaryColorPressed: '#1d8fca', primaryColorSuppl: '#2AABEE', borderRadius: '8px' },
}

onMounted(() => app.init())
</script>

<template>
  <NConfigProvider :theme="dark ? darkTheme : null" :theme-overrides="overrides" :locale="locale === 'uk' ? ukUA : enUS" :date-locale="locale === 'uk' ? dateUkUA : dateEnUS">
    <NGlobalStyle />
    <NLoadingBarProvider>
      <NMessageProvider>
        <NNotificationProvider>
          <NDialogProvider>
            <AppShell />
          </NDialogProvider>
        </NNotificationProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
