export type ChatType = 'user' | 'bot' | 'group' | 'supergroup' | 'channel' | 'forum' | 'saved' | 'unknown'
export type JobStatus = 'queued' | 'running' | 'paused' | 'flood_wait' | 'failed' | 'done' | 'cancelled'
export type MediaType = 'photo' | 'video' | 'round' | 'voice' | 'audio' | 'document' | 'sticker' | 'gif'
export const MEDIA_TYPES: MediaType[] = ['photo', 'video', 'round', 'voice', 'audio', 'document', 'sticker', 'gif']
export const FORMATS = ['md', 'txt', 'json', 'jsonl', 'html', 'csv'] as const
export type ExportFormat = (typeof FORMATS)[number]
export const SPLIT_MODES = ['month', 'single', 'day', 'week', 'year', 'size', 'topic', 'llm'] as const
export type SplitMode = (typeof SPLIT_MODES)[number]

export interface Me {
  id: number
  first_name?: string
  last_name?: string
  username?: string
  phone?: string
}

export interface AuthStatus {
  configured: boolean
  api_id: number | null
  authorized: boolean
  state: 'need_config' | 'idle' | 'qr' | 'code_sent' | 'password' | 'ready'
  me: Me | null
  password_hint: string | null
}

export interface Chat {
  id: number
  type: ChatType
  title: string
  username: string | null
  is_forum: number
  is_archived: number
  unread_count: number
  unread_mentions: number
  last_message_at: string | null
  noforwards: number
  stored_messages: number
  media_count: number
  media_done: number
  folders: number[]
  synced: number | null
  total_messages: number | null
}

export interface Folder {
  id: number
  name: string
  color: string | null
  emoji: string | null
  parent_id: number | null
  source: 'user' | 'telegram'
  chats: number
}

export interface Job {
  id: number
  kind: string
  title: string
  params: Record<string, any>
  status: JobStatus
  progress: Record<string, any>
  error: string | null
  attempts: number
  wait_until: string | null
  parent_id: number | null
  created_at: string
  updated_at: string
}

export interface Message {
  chat_id: number
  id: number
  topic_id: number
  sender_id: number | null
  out: number
  date: string
  edit_date: string | null
  text: string
  reply_to: number | null
  fwd_from: { from_id?: number; from_name?: string; date?: string } | null
  media_type: string | null
  has_media: number
  reactions: { emoji: string; count: number }[] | null
  buttons: { text: string; type: string; url?: string }[][] | null
  service_action: string | null
  raw: Record<string, any> | null
  first_name: string | null
  last_name: string | null
  username: string | null
  media_id: number | null
  file_name: string | null
  media_size: number | null
  media_status: string | null
  mime: string | null
  duration: number | null
  transcript: string | null
}

export interface MediaItem {
  id: number
  chat_id: number
  message_id: number
  type: MediaType
  file_name: string
  size: number
  bytes_done: number
  status: 'available' | 'pending' | 'downloading' | 'paused' | 'done' | 'failed' | 'skipped'
  priority: number
  attempts: number
  error: string | null
  chat_title: string
}

export interface Preset {
  id?: number
  kind: 'search' | 'export'
  name: string
  data: Record<string, any>
}

export interface ExportRequest {
  chat_ids: number[]
  formats: ExportFormat[]
  split: { mode: SplitMode; size_mb?: number; tokens?: number; overlap?: number }
  media_types: MediaType[]
  no_media: boolean
  filters: { date_from?: string | null; date_to?: string | null; max_size_mb?: number | null; from?: 'me' | 'others' | null; extensions?: string[] }
  also_full: boolean
  include_transcripts: boolean
  takeout?: boolean | null
}

export interface MiniApp {
  id: number
  bot_id: number | null
  bot_username: string | null
  kind: string
  short_name: string
  title: string | null
  url: string
  chat_title: string | null
  snapshots: number
  last_seen: string
}

export interface Settings {
  api_id: number | null
  archive_root: string
  port: number
  language: 'uk' | 'en'
  theme: 'auto' | 'light' | 'dark'
  history_rps: number
  media_concurrency: number
  max_retries: number
  use_takeout: boolean
  protected_content: boolean
  import_tg_folders: boolean
  path_template: string
  download_window: string
  daily_gb_limit: number
  check_updates: boolean
  tutorial_done: boolean
  advanced_mode: boolean
  whisper: { enabled: boolean; model: string; device: string; compute_type: string; beam_size: number; language: string; auto: boolean; auto_chat_ids: number[] }
  ai: { enabled: boolean; provider: 'ollama' | 'anthropic' | 'openai' | 'openrouter'; model: string; ollama_url: string; mask_pii: boolean; daily_token_limit: number; schedule_time: string; prompts: Record<string, string> }
  monitor: { enabled: boolean; interval_min: number; chat_ids: number[]; folder_ids: number[]; ignore_chat_ids: number[]; mentions_only: boolean; mark_read_after: boolean; stay_offline: boolean }
  secrets: Record<string, boolean>
}
