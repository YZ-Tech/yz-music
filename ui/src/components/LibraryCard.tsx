import { useState } from 'react'
import { Card } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'
import { AudioOnlyToggle } from './AudioOnlyToggle'
import { LibraryBrowser } from './LibraryBrowser'
import { LibraryFallbackStrip } from './LibraryFallbackStrip'
import { LibraryPathEditor } from './LibraryPathEditor'
import { LibrarySearch } from './LibrarySearch'
import type { Source } from '../types'

/** Library card — orchestrator. Holds two pieces of local UI state
 *  (source toggle + search query) that both LibrarySearch and
 *  LibraryBrowser need to read. Everything else (selection, view,
 *  fallback, library, library_path) is either local to a child or
 *  pulled from the store directly.
 *
 *  We read `libraryPath` here only to use it as the React `key` for
 *  LibraryPathEditor — that remounts the editor whenever the saved
 *  path changes, resetting its local draft without a sync-effect. */
export function LibraryCard() {
  const [source, setSource] = useState<Source>('local')
  const [query, setQuery] = useState('')
  const libraryPath = useStore((s) => s.music.libraryPath)

  return (
    <>
      <LibrarySearch
        source={source}
        onSourceChange={setSource}
        query={query}
        onQueryChange={setQuery}
      />

      <Card variant="outlined">
        <LibraryBrowser source={source} query={query} />
        <LibraryPathEditor key={libraryPath} path={libraryPath} />
        <AudioOnlyToggle />
        <LibraryFallbackStrip />
      </Card>
    </>
  )
}
