import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../src/i18n/en'
import uk from '../src/i18n/uk'

function keys(obj: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`]))
}

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.(vue|ts)$/.test(f) ? [p] : []
  })
}

describe('i18n', () => {
  const ukKeys = new Set(keys(uk))

  it('uk and en have the same keys', () => {
    expect(keys(en).sort()).toEqual([...ukKeys].sort())
  })

  it('every static t() key used in the UI exists', () => {
    const missing: string[] = []
    for (const f of files(join(__dirname, '../src'))) {
      const src = readFileSync(f, 'utf-8')
      for (const m of src.matchAll(/\bt\(\s*'([a-zA-Z0-9_.]+)'/g)) if (!ukKeys.has(m[1])) missing.push(`${f}: ${m[1]}`)
    }
    expect(missing).toEqual([])
  })

  it('messages avoid vue-i18n special characters', () => {
    for (const k of ukKeys) {
      const get = (o: any) => k.split('.').reduce((a, p) => a[p], o) as string
      for (const v of [get(uk), get(en)]) {
        expect(v, k).not.toMatch(/[@|]/)
      }
    }
  })
})
