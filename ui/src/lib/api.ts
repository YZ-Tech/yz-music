// Semantic API contract for the music module.
//
// Same pattern as the wakeword-trainer satellite: the module declares
// named operations; the host (JarvYZ or the standalone SPA) provides an
// implementation. Decouples the module from any specific URL scheme.
//
// Adapters shipped with the module:
//   - createSatelliteApi() — wraps the satellite's native routes (/play,
//     /control, /library, ...). Used by the standalone SPA + by JarvYZ
//     after Phase 4 (JarvYZ proxies /api/media/* to satellite anyway, so
//     a JarvYZ-embedded adapter is functionally identical — see App.tsx
//     and the JarvYZ-side dynamic loader).
//
// A host can also write its own adapter that implements MusicApi.

import { createContext, useContext } from 'react'
import type {
  Download,
  DownloadsSnapshot,
  FallbackResponse,
  LibraryItem,
  NowPlayingState,
  PlayMode,
  SearchResult,
} from '../types'

export interface SatelliteSettings {
  library_path: string
  audio_only: boolean
  audio_delay_ms: number
  fallback_video_ids: string[]
  fallback_loop: boolean
}

/** The complete API surface the music module needs from its host.
 *  All methods throw on failure (Error with backend message). */
export interface MusicApi {
  // Playback
  play(url: string, mode?: PlayMode): Promise<void>
  control(action: string, value?: number): Promise<{ ok: boolean; reason?: string }>

  // State reads
  nowPlaying(): Promise<NowPlayingState>
  library(): Promise<LibraryItem[]>
  downloads(): Promise<DownloadsSnapshot>

  // Settings
  getSettings(): Promise<SatelliteSettings>
  patchSettings(patch: Partial<SatelliteSettings>): Promise<SatelliteSettings>

  // YouTube search (Phase 5: JarvYZ-only — satellite doesn't expose this
  // yet. Standalone SPA throws NotSupportedError until satellite adds it.)
  searchYoutube?(query: string, limit?: number): Promise<SearchResult[]>

  // Library item ops (Phase 5: JarvYZ-only)
  deleteLibraryItem?(videoId: string): Promise<void>
  thumbnailUrl?(videoId: string): string

  // Fallback list
  getFallback?(): Promise<FallbackResponse>
  setFallback?(ids: string[], loop: boolean): Promise<FallbackResponse>

  // Dependencies status (yt-dlp + mpv) — surfaced in the DependenciesCard
  // so new users see exactly what's missing + outdated users get a nudge.
  // Both methods optional because the standalone-pre-Phase-N satellite
  // didn't ship them; in practice both adapters provide them now.
  getDependencies?(): Promise<DependenciesStatus>
  runDependencyUpdate?(name: 'ytdlp' | 'mpv' | 'ffmpeg'): Promise<DependencyUpdateResult>
}

export interface DependencyInfo {
  found: boolean
  path: string | null
  version: string | null
  /** What the package manager (per `source`) can install. NOT necessarily
   *  upstream's latest — apt's candidate on LTS is permanently behind
   *  mpv's GitHub releases, for example. See `upstream` for the
   *  divergence-aware view. */
  latest: string | null
  /** URI of where `latest` came from. Schemes: 'github:owner/repo' or
   *  'apt:package'. Used by the UI's tooltip ('via apt' / 'via GitHub'). */
  source?: string
  /** Three-state: true (outdated), false (fresh), null (can't compare —
   *  e.g. source unreachable or version string unparseable). */
  outdated: boolean | null
  /** Soft note for "newer version exists upstream but the configured
   *  package manager can't reach there". Only present when source !=
   *  upstream AND upstream is genuinely newer than what's installable
   *  via update_cmd. UI shows this as a small ✨ pill, NOT as red/amber. */
  upstream?: {
    version: string
    source: string
    note: string
  } | null
  install_hint: {
    label: string
    install_cmd: string
    update_cmd: string
    docs_url: string
  }
}

export interface DependenciesStatus {
  platform: 'windows' | 'linux' | 'macos'
  ytdlp: DependencyInfo
  mpv: DependencyInfo
  ffmpeg: DependencyInfo
}

export interface DependencyUpdateResult {
  ok: boolean
  /** How the update was attempted, drives UI rendering:
   *  - 'sync'  — Windows UAC path; check exit_code + stdout/stderr
   *  - 'async' — Linux terminal path; check spawned_terminal + message
   *  - 'copy'  — no automation available; show message + the copy command */
  kind?: 'sync' | 'async' | 'copy'
  command?: string
  exit_code?: number
  stdout?: string
  stderr?: string
  error?: string
  /** Linux only — name of the terminal binary that was launched
   *  (e.g. 'tilix', 'gnome-terminal'). */
  spawned_terminal?: string
  /** User-friendly status hint. Always present except on hard error. */
  message?: string
}

// ---------------------------------------------------------------------------

export class NotSupportedError extends Error {
  constructor(operation: string) {
    super(`Operation '${operation}' is not supported by this host`)
    this.name = 'NotSupportedError'
  }
}

const stub = <T>(name: string): Promise<T> =>
  Promise.reject(new NotSupportedError(name))

const NO_API: MusicApi = {
  play: () => stub('play'),
  control: () => stub('control'),
  nowPlaying: () => stub('nowPlaying'),
  library: () => stub('library'),
  downloads: () => stub('downloads'),
  getSettings: () => stub('getSettings'),
  patchSettings: () => stub('patchSettings'),
}

export const ApiContext = createContext<MusicApi>(NO_API)
export const useApi = () => useContext(ApiContext)

// ---------------------------------------------------------------------------
// Satellite adapter — wraps the satellite's native routes via fetch.
// Used by App.tsx (standalone SPA) and (via JarvYZ's /api/media/* proxy)
// by the JarvYZ-embedded path indirectly.

interface HttpClient {
  request<T>(method: string, path: string, body?: unknown): Promise<T>
}

function httpClient(apiBase: string): HttpClient {
  return {
    async request<T>(method: string, path: string, body?: unknown): Promise<T> {
      const url = apiBase + path
      const res = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        throw new Error(`${method} ${url} → ${res.status} ${detail}`)
      }
      const text = await res.text()
      return (text ? JSON.parse(text) : undefined) as T
    },
  }
}

export function createSatelliteApi({ apiBase = '' }: { apiBase?: string } = {}): MusicApi {
  const h = httpClient(apiBase)
  return {
    play: async (url, mode = 'play') => {
      await h.request('POST', '/play', { url, mode })
    },
    control: (action, value = 0) =>
      h.request('POST', '/control', { action, value }),

    nowPlaying: () => h.request('GET', '/now_playing'),
    library: () => h.request('GET', '/library'),
    downloads: () => h.request('GET', '/downloads'),

    getSettings: () => h.request('GET', '/settings'),
    patchSettings: (patch) => h.request('PATCH', '/settings', patch),

    getDependencies: () => h.request('GET', '/dependencies'),
    runDependencyUpdate: (name) => h.request('POST', '/dependencies/update', { name }),

    // Standalone-only: thumbnail comes from the satellite. (Phase 6 — for
    // now the SPA can render YouTube's i.ytimg.com directly.)
    thumbnailUrl: (videoId: string) =>
      `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`,
  }
}
