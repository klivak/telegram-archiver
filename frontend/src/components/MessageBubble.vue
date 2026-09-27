<script setup lang="ts">
import { NButton, NImage, NTag } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '@/api/client'
import type { Message } from '@/api/types'
import { formatBytes, senderName } from '@/utils'

const props = defineProps<{ m: Message; showSender: boolean; whisper: boolean }>()
const emit = defineEmits<{ download: [number]; transcribe: [number]; jump: [number] }>()
const { t } = useI18n()

const fileUrl = computed(() => (props.m.media_id && props.m.media_status === 'done' ? api.fileUrl(`/media/${props.m.media_id}/file`) : ''))
const kind = computed(() => props.m.media_type)
const isAudio = computed(() => kind.value === 'voice' || kind.value === 'audio')
const isVideo = computed(() => kind.value === 'video' || kind.value === 'round' || kind.value === 'gif')
const isImage = computed(() => kind.value === 'photo' || (kind.value === 'sticker' && !(props.m.file_name ?? '').match(/\.(tgs|webm)$/)))

/** Escape then apply UTF-16 based entities (same rules as the exporter). */
const html = computed(() => {
  const text = props.m.text ?? ''
  const ents = (props.m.raw?.entities ?? []) as { t: string; o: number; l: number; url?: string }[]
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  if (!ents.length) return linkify(esc(text))
  const units = [...text].flatMap((ch) => (ch.length === 2 ? [ch, ''] : [ch])) // UTF-16 index -> char
  const open: Record<number, string[]> = {}
  const close: Record<number, string[]> = {}
  const tags: Record<string, [string, string]> = { Bold: ['<b>', '</b>'], Italic: ['<i>', '</i>'], Underline: ['<u>', '</u>'], Strike: ['<s>', '</s>'], Code: ['<code>', '</code>'], Pre: ['<pre>', '</pre>'], Spoiler: ['<span class="spoiler">', '</span>'], Blockquote: ['<blockquote>', '</blockquote>'] }
  for (const e of ents) {
    let pair = tags[e.t]
    if (e.t === 'TextUrl' && e.url && /^(https?:|tg:|mailto:)/i.test(e.url)) pair = [`<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">`, '</a>']
    if (!pair) continue
    ;(open[e.o] ??= []).push(pair[0])
    ;(close[e.o + e.l] ??= []).unshift(pair[1])
  }
  let out = ''
  for (let i = 0; i <= units.length; i++) {
    out += (close[i] ?? []).join('') + (open[i] ?? []).join('')
    if (i < units.length) out += esc(units[i])
  }
  return linkify(out)
})

function linkify(s: string) {
  return s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>').replace(/\n/g, '<br>')
}
const time = computed(() => (props.m.date ?? '').slice(11, 16))
const mediaLabel = computed(() => (kind.value ? t(`media.${kind.value}`, kind.value) : ''))
</script>

<template>
  <div v-if="m.service_action" class="svc">{{ t('chat.service', { action: m.service_action }) }}</div>
  <div v-else class="msg" :class="{ out: m.out }" :id="`m${m.id}`">
    <div v-if="showSender && !m.out" class="from">{{ senderName(m) }}</div>
    <div v-if="m.fwd_from" class="fwd">{{ t('chat.forwarded', { from: m.fwd_from.from_name ?? m.fwd_from.from_id ?? '?' }) }}</div>
    <div v-if="m.reply_to" class="reply" @click="emit('jump', m.reply_to!)">↩ #{{ m.reply_to }}</div>
    <template v-if="m.media_id">
      <template v-if="fileUrl">
        <NImage v-if="isImage" :src="fileUrl" lazy object-fit="contain" class="img" />
        <video v-else-if="isVideo" :src="fileUrl" controls preload="none" :class="{ round: kind === 'round' }" :loop="kind === 'gif'" />
        <div v-else-if="isAudio">
          <audio :src="fileUrl" controls preload="none" style="width: 100%" />
        </div>
        <a v-else :href="fileUrl" target="_blank" class="file">📎 {{ m.file_name }} <span class="small muted">{{ formatBytes(m.media_size) }}</span></a>
      </template>
      <div v-else class="placeholder">
        <span>{{ mediaLabel }} · {{ m.file_name }} · {{ formatBytes(m.media_size) }}</span>
        <NTag v-if="m.media_status === 'pending' || m.media_status === 'downloading'" size="small" type="info">{{ t(`mediaStatus.${m.media_status}`) }}</NTag>
        <NButton v-else size="tiny" @click="emit('download', m.media_id!)">⬇ {{ t('chat.downloadNow') }}</NButton>
      </div>
      <div v-if="m.transcript" class="transcript">🎙 {{ m.transcript }}</div>
      <NButton v-else-if="whisper && (kind === 'voice' || kind === 'round')" size="tiny" quaternary @click="emit('transcribe', m.media_id!)">🎙 {{ t('chat.transcribe') }}</NButton>
    </template>
    <div v-else-if="kind && kind !== 'webpage'" class="placeholder"><i>{{ mediaLabel }}</i><span v-if="m.raw?.poll"> — {{ m.raw.poll.question }}</span></div>
    <div v-if="m.text" class="text" v-html="html"></div>
    <div v-if="m.buttons?.length" class="buttons">
      <template v-for="(row, ri) in m.buttons" :key="ri">
        <span v-for="(b, bi) in row" :key="bi" class="btn">{{ b.type === 'WebView' || b.type === 'SimpleWebView' ? '🧩 ' : '' }}{{ b.text }}</span>
      </template>
    </div>
    <div v-if="m.reactions?.length" class="react">
      <span v-for="r in m.reactions" :key="r.emoji">{{ r.emoji }} {{ r.count }}</span>
    </div>
    <div class="meta">{{ m.edit_date ? t('chat.edited') + ' · ' : '' }}{{ time }}</div>
  </div>
</template>

<style scoped>
.msg {
  background: var(--bubble, rgba(128, 128, 128, 0.1));
  border-radius: 12px;
  padding: 6px 10px;
  margin: 3px 0;
  max-width: min(78%, 620px);
  width: fit-content;
  word-wrap: break-word;
}
.msg.out {
  margin-left: auto;
  background: rgba(42, 171, 238, 0.18);
}
.from {
  font-weight: 600;
  color: #2aabee;
  font-size: 13px;
}
.fwd,
.reply {
  font-size: 12px;
  opacity: 0.7;
  border-left: 2px solid #2aabee;
  padding-left: 6px;
  margin: 2px 0;
}
.reply {
  cursor: pointer;
}
.img :deep(img) {
  max-width: 100%;
  max-height: 360px;
  border-radius: 8px;
}
video {
  max-width: 100%;
  max-height: 360px;
  border-radius: 8px;
}
video.round {
  width: 240px;
  height: 240px;
  border-radius: 50%;
  object-fit: cover;
}
.placeholder {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  opacity: 0.8;
  flex-wrap: wrap;
}
.transcript {
  font-size: 13px;
  opacity: 0.8;
  border-top: 1px dashed rgba(128, 128, 128, 0.4);
  margin-top: 4px;
  padding-top: 4px;
}
.text :deep(pre),
.text :deep(code) {
  background: rgba(128, 128, 128, 0.15);
  border-radius: 4px;
  padding: 0 3px;
}
.text :deep(.spoiler) {
  background: currentColor;
}
.text :deep(.spoiler:hover) {
  background: none;
}
.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.btn {
  font-size: 12px;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 6px;
  padding: 2px 6px;
}
.react {
  font-size: 12px;
  display: flex;
  gap: 6px;
  margin-top: 2px;
}
.meta {
  font-size: 11px;
  opacity: 0.55;
  text-align: right;
}
.svc {
  text-align: center;
  font-size: 12px;
  opacity: 0.6;
  margin: 6px 0;
}
.file {
  display: block;
}
</style>
