import { useEffect } from 'react'
import { useWebSocket, useSubscription } from '../lib/ws'
import { useMusicStore } from '../lib/store-context'
import type { Download, NowPlayingState } from '../types'

/** Mount-once wiring for the Music page: initial fetches + WS subscriptions
 *  for `now_playing` and `download_progress`. Subscribes only while the page
 *  is mounted so off-page sessions don't pay for events they ignore.
 *
 *  WS protocol: uses JarvYZ's subscribe_event protocol (host's WS API).
 *  In JarvYZ-embedded mode the wsApi prop is the JarvYZ WS; in standalone
 *  mode App.tsx provides a bridge to the satellite's /events that emits
 *  the same shapes — components don't see the difference. */
export function useMusicWiring(): void {
  const { send, isConnected } = useWebSocket()
  const applyNowPlaying = useMusicStore((s) => s.applyNowPlaying)
  const applyDownloads = useMusicStore((s) => s.applyDownloads)
  const fetchNowPlaying = useMusicStore((s) => s.fetchNowPlaying)
  const fetchDownloads = useMusicStore((s) => s.fetchDownloads)
  const fetchLibrary = useMusicStore((s) => s.fetchLibrary)
  const fetchFallback = useMusicStore((s) => s.fetchFallback)
  const fetchLibraryPath = useMusicStore((s) => s.fetchLibraryPath)
  const setMusicError = useMusicStore((s) => s.setMusicError)

  useEffect(() => {
    // Surface library-load failure instead of swallowing it — otherwise a
    // failed fetch is indistinguishable from an empty library.
    fetchLibrary().catch((e) =>
      setMusicError(e instanceof Error ? e.message : `Failed to load library: ${String(e)}`),
    )
    fetchFallback()
    fetchNowPlaying()
    fetchDownloads()
    fetchLibraryPath()
  }, [
    fetchLibrary,
    fetchFallback,
    fetchNowPlaying,
    fetchDownloads,
    fetchLibraryPath,
    setMusicError,
  ])

  useEffect(() => {
    if (!isConnected) return
    send({ type: 'subscribe_event', event_type: 'now_playing' })
    send({ type: 'subscribe_event', event_type: 'download_progress' })
    fetchNowPlaying()
    return () => {
      send({ type: 'unsubscribe_event', event_type: 'now_playing' })
      send({ type: 'unsubscribe_event', event_type: 'download_progress' })
    }
  }, [send, isConnected, fetchNowPlaying])

  useSubscription<NowPlayingState>('now_playing', applyNowPlaying)
  useSubscription<{ downloads: Download[] }>('download_progress', (d) => {
    applyDownloads(Array.isArray(d.downloads) ? d.downloads : [])
  })
}
