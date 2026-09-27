<script setup lang="ts">
import { darkTheme, dateEnUS, dateUkUA, enUS, NConfigProvider, NDialogProvider, NGlobalStyle, NLoadingBarProvider, NMessageProvider, NNotificationProvider, ukUA, type GlobalThemeOverrides } from 'naive-ui'
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import AppShell from './components/AppShell.vue'
import { useAppStore } from './stores/app'

const app = useAppStore()
const { locale } = useI18n()
const systemDark = ref(window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false)
window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', (e) => (systemDark.value = e.matches))

const dark = computed(() => app.themePref === 'dark' || (app.themePref === 'auto' && systemDark.value))
watchEffect(() => {
  document.documentElement.dataset.theme = dark.value ? 'dark' : 'light'
  if (app.settings) localStorage.setItem('tga.theme', app.settings.theme)
})

const FONT = "'Inter', 'Segoe UI Variable Text', 'Segoe UI', system-ui, -apple-system, Roboto, 'Helvetica Neue', Arial, sans-serif"
const shared: GlobalThemeOverrides['common'] = {
  fontFamily: FONT,
  primaryColor: '#2AABEE',
  primaryColorHover: '#4CBCF2',
  primaryColorPressed: '#1D8FCA',
  primaryColorSuppl: '#2AABEE',
  infoColor: '#2AABEE',
  infoColorHover: '#4CBCF2',
  infoColorPressed: '#1D8FCA',
  infoColorSuppl: '#2AABEE',
  borderRadius: '8px',
  borderRadiusSmall: '6px',
  fontSize: '14px',
  fontWeightStrong: '600',
}
const darkOverrides: GlobalThemeOverrides = {
  common: {
    ...shared,
    bodyColor: '#0b0d12',
    cardColor: '#12151c',
    modalColor: '#141821',
    popoverColor: '#161a23',
    tableColor: '#12151c',
    inputColor: 'rgba(255,255,255,0.04)',
    actionColor: 'rgba(255,255,255,0.03)',
    hoverColor: 'rgba(255,255,255,0.05)',
    borderColor: 'rgba(255,255,255,0.08)',
    dividerColor: 'rgba(255,255,255,0.07)',
    textColor1: '#e8ebf1',
    textColor2: '#c9cfda',
    textColor3: '#8a93a3',
    successColor: '#3ECF8E',
    warningColor: '#F5A524',
    errorColor: '#F2555A',
  },
  Card: { borderRadius: '12px', color: '#12151c', borderColor: 'rgba(255,255,255,0.07)' },
  Button: { fontWeight: '500' },
  Tag: { borderRadius: '6px' },
  Tabs: { tabFontWeightActive: '600' },
  Drawer: { color: '#0f1218' },
}
const lightOverrides: GlobalThemeOverrides = {
  common: {
    ...shared,
    primaryColor: '#1A9AD9',
    primaryColorSuppl: '#1A9AD9',
    bodyColor: '#f6f7f9',
    cardColor: '#ffffff',
    modalColor: '#ffffff',
    popoverColor: '#ffffff',
    borderColor: 'rgba(15,23,42,0.1)',
    dividerColor: 'rgba(15,23,42,0.08)',
    hoverColor: 'rgba(15,23,42,0.04)',
    textColor1: '#0f1623',
    textColor2: '#2e3847',
    textColor3: '#6b7384',
    successColor: '#16A34A',
    warningColor: '#D97706',
    errorColor: '#DC2626',
  },
  Card: { borderRadius: '12px', borderColor: 'rgba(15,23,42,0.08)' },
  Button: { fontWeight: '500' },
  Tag: { borderRadius: '6px' },
  Tabs: { tabFontWeightActive: '600' },
}

onMounted(() => app.init())
</script>

<template>
  <NConfigProvider :theme="dark ? darkTheme : null" :theme-overrides="dark ? darkOverrides : lightOverrides" :locale="locale === 'uk' ? ukUA : enUS" :date-locale="locale === 'uk' ? dateUkUA : dateEnUS">
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
