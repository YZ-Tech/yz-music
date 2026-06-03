import { useCallback, useEffect, useState } from 'react'
import { useApi, type DependenciesStatus, type DependencyInfo } from '../lib/api'

/** Shared dependencies fetcher used by both the Music-page gear icon
 *  (for badge state) and the DependenciesCard (for full rendering).
 *  Single source of truth — lift this hook into the parent that needs
 *  both views and pass `status` / `refresh` down to keep them in sync. */
export function useDependencies() {
  const api = useApi()
  const [status, setStatus] = useState<DependenciesStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!api.getDependencies) {
      setError('Satellite does not expose /dependencies — upgrade the music satellite.')
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      setStatus(await api.getDependencies())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { status, loading, error, refresh }
}


/** Does the dependency state warrant the user's attention?
 *  Anything missing or outdated → yes. Anything `outdated: null`
 *  (couldn't check upstream) → no, because we'd rather not nag on
 *  transient GitHub unreachability. */
export function dependencyNeedsAttention(s: DependenciesStatus | null): boolean {
  if (!s) return false
  return [s.ytdlp, s.mpv].some(depNeedsAttention)
}

export function depNeedsAttention(d: DependencyInfo): boolean {
  if (!d.found) return true
  if (d.outdated === true) return true
  return false
}


/** How many binaries need attention (for a numeric badge if we want it).
 *  Returns 0 when all good or status unknown. */
export function attentionCount(s: DependenciesStatus | null): number {
  if (!s) return 0
  return [s.ytdlp, s.mpv].filter(depNeedsAttention).length
}
