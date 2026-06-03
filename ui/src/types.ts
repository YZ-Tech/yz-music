// Shared shapes for the Music UI. Mirrors what the satellite emits +
// what the components consume. Kept light — no JarvYZ-internal types.

export interface LibraryItem {
  video_id: string
  title: string
  channel: string
  size_mb: number
  mtime: number
  duration_seconds: number | null
  url: string
  is_fallback: boolean
}

export interface FallbackResponse {
  ids: string[]
  loop: boolean
  items: { video_id: string; metadata: LibraryItem | null }[]
}

export interface Download {
  id: string
  status: 'starting' | 'downloading' | 'done' | 'error'
  url: string
  video_id?: string
  title?: string
  percent?: number
  eta?: string
  rate?: string
  exit_code?: number
  updated_at?: number
}

export interface DownloadsSnapshot {
  downloads: Download[]
}

export interface NowPlayingState {
  path: string | null
  video_id: string | null
  time_pos: number | null
  duration: number | null
  pause: boolean | null
  playlist_pos: number | null
  playlist_count: number | null
  volume: number | null
  idle: boolean
  // mpv returns False for "no", "inf" for unbounded, or an integer count.
  // Anything not False/null and not "no"/0 means active.
  loop_file: boolean | string | number | null
  loop_playlist: boolean | string | number | null
}

export interface SearchResult {
  video_id: string
  title: string
  channel: string
  url: string
  duration_seconds: number | null
}

export type Source = 'local' | 'youtube'
export type PlayMode = 'play' | 'next' | 'queue'
export type View = 'table' | 'grid'

export const INITIAL_NOW_PLAYING: NowPlayingState = {
  path: null,
  video_id: null,
  time_pos: null,
  duration: null,
  pause: null,
  playlist_pos: null,
  playlist_count: null,
  volume: null,
  idle: true,
  loop_file: null,
  loop_playlist: null,
}
