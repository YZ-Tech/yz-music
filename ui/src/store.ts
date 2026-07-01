// Satellite-internal zustand store for the Music page.
//
// Factory pattern: createMusicStore(api) returns a store bound to the
// host-provided MusicApi. The module root (MusicPage.tsx) creates the
// store on mount with useMemo + provides it via StoreContext. Components
// read with useMusicStore — identical body to the old JarvYZ-side
// useStore calls, only the import line + the store identity differ.
//
// This preserves the existing slice shape: state lives under `s.music.*`,
// actions are at root. The JarvYZ-side storeMusic.ts can later be deleted
// in favor of this one (its slice was always Music-page-only state).

import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { produce } from 'immer'
import type { MusicApi } from './lib/api'
import {
  INITIAL_NOW_PLAYING,
  type Download,
  type DownloadsSnapshot,
  type FallbackResponse,
  type LibraryItem,
  type NowPlayingState,
  type PlayMode,
} from './types'


export interface MusicSlice {
  nowPlaying: NowPlayingState
  library: LibraryItem[]
  fallback: FallbackResponse
  downloads: Download[]
  libraryPath: string
  audioOnly: boolean
  audioDelayMs: number
  libraryLoading: boolean
  error: string | null
}

export interface MusicState {
  music: MusicSlice

  // ── mutations + actions ─────────────────────────────────────
  setMusicError: (e: string | null) => void
  applyNowPlaying: (data: Partial<NowPlayingState>) => void
  applyDownloads: (downloads: Download[]) => void

  fetchNowPlaying: () => Promise<void>
  fetchDownloads: () => Promise<void>
  fetchLibrary: () => Promise<void>
  fetchFallback: () => Promise<void>
  fetchLibraryPath: () => Promise<void>

  saveLibraryPath: (path: string) => Promise<void>
  saveAudioOnly: (value: boolean) => Promise<void>
  saveAudioDelay: (ms: number) => Promise<void>

  toggleFallback: (videoId: string, makeFallback: boolean) => Promise<void>
  setFallbackLoop: (loop: boolean) => Promise<void>

  deleteLibraryItem: (videoId: string) => Promise<void>
  mediaControl: (action: string, value?: number) => Promise<void>
  mediaPlay: (url: string, mode: PlayMode) => Promise<void>
}


export type MusicStore = UseBoundStore<StoreApi<MusicState>>


/** Create a Music store bound to the host-provided MusicApi.
 *  Called once at module mount (MusicPage.tsx). The returned store is
 *  provided to children via StoreContext. */
export function createMusicStore(api: MusicApi): MusicStore {
  return create<MusicState>((set) => ({
    music: {
      nowPlaying: { ...INITIAL_NOW_PLAYING },
      library: [],
      fallback: { ids: [], loop: true, items: [] },
      downloads: [],
      libraryPath: '',
      audioOnly: true,
      audioDelayMs: 0,
      libraryLoading: false,
      error: null,
    },

    setMusicError: (e) => set(produce((s: MusicState) => { s.music.error = e })),

    applyNowPlaying: (data) =>
      set(produce((s: MusicState) => { Object.assign(s.music.nowPlaying, data) })),

    applyDownloads: (downloads) =>
      set(produce((s: MusicState) => { s.music.downloads = downloads })),

    fetchNowPlaying: async () => {
      try {
        const np = await api.nowPlaying()
        set(produce((s: MusicState) => { s.music.nowPlaying = np }))
      } catch { /* soft-fail — WS will catch up */ }
    },

    fetchDownloads: async () => {
      try {
        const d: DownloadsSnapshot = await api.downloads()
        set(produce((s: MusicState) => { s.music.downloads = d.downloads }))
      } catch { /* soft-fail */ }
    },

    fetchLibrary: async () => {
      set(produce((s: MusicState) => { s.music.libraryLoading = true }))
      try {
        const items = await api.library()
        set(produce((s: MusicState) => {
          s.music.library = items
          s.music.libraryLoading = false
        }))
      } catch (e) {
        set(produce((s: MusicState) => { s.music.libraryLoading = false }))
        throw e
      }
    },

    fetchFallback: async () => {
      try {
        const fb = await api.getFallback?.()
        if (fb) set(produce((s: MusicState) => { s.music.fallback = fb }))
      } catch { /* soft-fail */ }
    },

    fetchLibraryPath: async () => {
      try {
        const s = await api.getSettings()
        set(produce((draft: MusicState) => {
          draft.music.libraryPath = s.library_path
          if (typeof s.audio_only === 'boolean') {
            draft.music.audioOnly = s.audio_only
          }
          if (typeof s.audio_delay_ms === 'number') {
            draft.music.audioDelayMs = s.audio_delay_ms
          }
        }))
      } catch { /* soft-fail */ }
    },

    saveLibraryPath: async (path: string) => {
      const s = await api.patchSettings({ library_path: path })
      set(produce((draft: MusicState) => { draft.music.libraryPath = s.library_path }))
    },

    saveAudioOnly: async (value: boolean) => {
      await api.patchSettings({ audio_only: value })
      set(produce((draft: MusicState) => { draft.music.audioOnly = value }))
    },

    saveAudioDelay: async (ms: number) => {
      // Persist to satellite settings (applied on next mpv spawn via
      // --audio-delay-ms) AND live-apply to a currently-playing mpv via the
      // control IPC (best-effort — no-op if nothing is playing).
      await api.patchSettings({ audio_delay_ms: ms })
      Promise.resolve(api.control('audio_delay', ms)).catch(() => { /* mpv may be down */ })
      set(produce((draft: MusicState) => { draft.music.audioDelayMs = ms }))
    },

    toggleFallback: async (videoId, makeFallback) => {
      // Best-effort: use host's fallback toggle when provided. Otherwise
      // optimistically flip the is_fallback bit on the library item.
      try {
        if (api.setFallback && api.getFallback) {
          const cur = await api.getFallback()
          const next = makeFallback
            ? [...cur.ids.filter((id) => id !== videoId), videoId]
            : cur.ids.filter((id) => id !== videoId)
          const fb = await api.setFallback(next, cur.loop)
          set(produce((s: MusicState) => {
            s.music.fallback = fb
            s.music.library = s.music.library.map((it) =>
              it.video_id === videoId ? { ...it, is_fallback: makeFallback } : it,
            )
          }))
        }
      } catch { /* soft-fail */ }
    },

    setFallbackLoop: async (loop) => {
      if (api.setFallback && api.getFallback) {
        const cur = await api.getFallback()
        const fb = await api.setFallback(cur.ids, loop)
        set(produce((s: MusicState) => { s.music.fallback = fb }))
      }
    },

    deleteLibraryItem: async (videoId) => {
      if (api.deleteLibraryItem) await api.deleteLibraryItem(videoId)
    },

    mediaControl: async (action, value = 0) => {
      await api.control(action, value)
    },

    mediaPlay: async (url, mode) => {
      await api.play(url, mode)
    },
  }))
}
