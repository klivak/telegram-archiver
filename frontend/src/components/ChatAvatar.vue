<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { colorFor, initials } from '@/utils'

const props = withDefaults(defineProps<{ id: number; title: string; size?: number; photo?: boolean }>(), { size: 40, photo: true })
const failed = ref(false)
const visible = ref(false)
const el = ref<HTMLElement | null>(null)
let io: IntersectionObserver | undefined

// Lazy: avatars are fetched (and cached by the backend) only when scrolled into view.
onMounted(() => {
  if (!props.photo) return
  io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) {
      visible.value = true
      io?.disconnect()
    }
  })
  if (el.value) io.observe(el.value)
})
onBeforeUnmount(() => io?.disconnect())

const src = computed(() => (visible.value && !failed.value ? api.fileUrl(`/chats/${props.id}/avatar`) : ''))
const style = computed(() => ({ width: `${props.size}px`, height: `${props.size}px`, fontSize: `${props.size * 0.38}px`, background: colorFor(props.id) }))
</script>

<template>
  <div ref="el" class="avatar" :style="style">
    <img v-if="src" :src="src" alt="" loading="lazy" @error="failed = true" />
    <span v-else>{{ initials(title) }}</span>
  </div>
</template>

<style scoped>
.avatar {
  border-radius: 50%;
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 600;
  overflow: hidden;
  flex: none;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
