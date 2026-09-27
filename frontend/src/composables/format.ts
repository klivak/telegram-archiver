import { useI18n } from 'vue-i18n'
import { formatCompact, formatNumber } from '@/utils'

/** Number formatters bound to the current UI locale. */
export function useFormat() {
  const { locale } = useI18n()
  return {
    n: (v: number | null | undefined) => formatNumber(v, locale.value),
    compact: (v: number | null | undefined) => formatCompact(v, locale.value),
  }
}
