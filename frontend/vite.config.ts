/// <reference types="vitest/config" />
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type Plugin } from 'vite'

const API = process.env.TGARCHIVER_API ?? 'http://127.0.0.1:8765'

function appDir(): string {
  if (process.env.TGARCHIVER_HOME) return process.env.TGARCHIVER_HOME
  if (process.platform === 'win32') return join(process.env.APPDATA ?? join(homedir(), 'AppData', 'Roaming'), 'TelegramArchiver')
  if (process.platform === 'darwin') return join(homedir(), 'Library', 'Application Support', 'TelegramArchiver')
  return join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'TelegramArchiver')
}

/** Dev only: inject the token written by `tgarchiver --dev`, so http://localhost:5173 just works. */
function devToken(): Plugin {
  return {
    name: 'tga-dev-token',
    apply: 'serve',
    transformIndexHtml(html) {
      const f = join(appDir(), 'dev-token')
      if (!existsSync(f)) return html
      const token = readFileSync(f, 'utf-8').trim()
      return html.replace('<head>', `<head><script>window.__TGA_TOKEN__=${JSON.stringify(token)}</script>`)
    },
  }
}

export default defineConfig({
  plugins: [vue(), devToken()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: false },
      '/ws': { target: API.replace('http', 'ws'), ws: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/vue') || id.includes('pinia') || id.includes('vue-router') || id.includes('vue-i18n') || id.includes('@intlify')) return 'vue'
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.ts'],
  },
})
