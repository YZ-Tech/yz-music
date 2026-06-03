import { useEffect, useRef, useState } from 'react'
import { useMusicStore } from '../lib/store-context'

/** Interpolated playback position. Authoritative time_pos arrives via WS
 *  at ~1 Hz; we tick a requestAnimationFrame loop against wall-clock in
 *  between so the slider doesn't visibly step.
 *
 *  Design:
 *  - The wall-clock anchor lives in a ref (no re-render on WS push).
 *  - The interpolated value lives in state (so the render body stays
 *    pure — no ref access, no impure calls).
 *  - The rAF callback reads the ref, computes, and writes state.
 *
 *  Call from exactly one tiny leaf (the progress slider) — the rAF
 *  setState is the per-frame cost we want to contain. */
export function useLiveTime(): number {
  const time_pos = useMusicStore((s) => s.music.nowPlaying.time_pos)
  const duration = useMusicStore((s) => s.music.nowPlaying.duration)
  const pause = useMusicStore((s) => s.music.nowPlaying.pause)
  const idle = useMusicStore((s) => s.music.nowPlaying.idle)
  const video_id = useMusicStore((s) => s.music.nowPlaying.video_id)

  const anchorRef = useRef<{ time_pos: number; at: number }>({ time_pos: 0, at: 0 })
  const prevVidRef = useRef<string | null | undefined>(undefined)
  const [liveTime, setLiveTime] = useState<number>(0)

  useEffect(() => {
    const trackChanged = prevVidRef.current !== video_id
    if (trackChanged) prevVidRef.current = video_id
    if (typeof time_pos !== 'number') {
      if (trackChanged) {
        anchorRef.current = { time_pos: 0, at: performance.now() }
        setLiveTime(0)
      }
      return
    }
    anchorRef.current = { time_pos, at: performance.now() }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLiveTime(time_pos)
  }, [video_id, time_pos])

  useEffect(() => {
    if (idle || pause !== false) return
    let raf = 0
    let cancelled = false
    const loop = () => {
      if (cancelled) return
      const a = anchorRef.current
      const elapsed = (performance.now() - a.at) / 1000
      let t = a.time_pos + elapsed
      if (duration && t > duration) t = duration
      setLiveTime(t)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
    }
  }, [idle, pause, duration])

  return liveTime
}
