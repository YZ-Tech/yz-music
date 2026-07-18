import { useState, type ReactNode } from 'react'
import { Card } from '@mui/material'
import { LibraryBrowser } from './LibraryBrowser'
import { LibraryFallbackStrip } from './LibraryFallbackStrip'
import { LibrarySearch } from './LibrarySearch'
import type { Source } from '../types'

/** Library card — orchestrator. Holds two pieces of local UI state
 *  (source toggle + search query) that both LibrarySearch and
 *  LibraryBrowser need to read. Everything else (selection, view,
 *  fallback, library) is either local to a child or pulled from the
 *  store directly.
 *
 *  The library-path / audio-only / lipsync-delay settings rows moved to
 *  the Music setup dialog (2026-07-10): they were orphaned chrome glued
 *  under the table, and settings already had a second home behind the
 *  gear — now there's one. */
export function LibraryCard({
  actions,
  leading,
}: {
  actions?: ReactNode
  leading?: ReactNode
}) {
  const [source, setSource] = useState<Source>('local')
  const [query, setQuery] = useState('')

  return (
    <>
      <LibrarySearch
        source={source}
        onSourceChange={setSource}
        query={query}
        onQueryChange={setQuery}
        actions={actions}
        leading={leading}
      />

      <Card variant="outlined">
        <LibraryBrowser source={source} query={query} />
        <LibraryFallbackStrip />
      </Card>
    </>
  )
}
