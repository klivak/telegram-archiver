import { createI18n } from 'vue-i18n'
import en from './en'
import uk from './uk'

export type Locale = 'uk' | 'en'
const saved = (localStorage.getItem('tga.locale') as Locale | null) ?? 'uk'

export const i18n = createI18n({
  legacy: false,
  locale: saved,
  fallbackLocale: 'en',
  messages: { uk, en },
  pluralRules: {
    // Ukrainian: one | few | many  ("1 чат | 2 чати | 5 чатів")
    uk: (choice: number, choicesLength: number) => {
      if (choicesLength < 3) return choice === 1 ? 0 : 1
      const n10 = choice % 10
      const n100 = choice % 100
      if (n10 === 1 && n100 !== 11) return 0
      if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return 1
      return 2
    },
  },
})

export function setLocale(l: Locale) {
  ;(i18n.global.locale as unknown as { value: Locale }).value = l
  localStorage.setItem('tga.locale', l)
  document.documentElement.lang = l
}
