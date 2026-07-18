import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Badge, Box, IconButton, Tooltip } from '@mui/material'
import { ThemeProvider, type Theme } from '@mui/material/styles'
import SettingsIcon from '@mui/icons-material/Settings'
import { ApiContext, type MusicApi } from './lib/api'
import { WSContext, type WSApi } from './lib/ws'
import { CapabilitiesContext, DEFAULT_CAPABILITIES, type Capabilities } from './lib/capabilities'
import { StoreProvider } from './lib/store-context'
import { createMusicStore } from './store'
import { useMusicWiring } from './hooks/useMusicWiring'
import { useMusicStore } from './lib/store-context'
import { useDependencies, dependencyNeedsAttention } from './hooks/useDependencies'
import { DependenciesDialog } from './components/DependenciesDialog'
import { DownloadsCard } from './components/DownloadsCard'
import { LibraryCard } from './components/LibraryCard'
import { NowPlayingCard } from './components/NowPlayingCard'


export interface MusicPageProps {
  /** Host's MUI theme. Wrapped in our own ThemeProvider so module-side
   *  `useTheme()` reads it. */
  theme?: Theme
  /** Host's WS API. Injected into the module's WSContext so the module's
   *  `useSubscription` reads from the host's connection. */
  wsApi?: WSApi
  /** Host's MusicApi implementation — module never knows URLs. */
  api: MusicApi
  /** Mode flags. */
  capabilities?: Capabilities
}


/** Root export — JarvYZ (and the standalone SPA) load this via
 *  @yz-dev/react-dynamic-module. Creates a per-mount Music store bound
 *  to the injected api, then provides Theme / WS / Api / Capabilities /
 *  Store contexts before rendering the inner page.
 *
 *  Why a per-mount store: the api injected from the host can vary
 *  (JarvYZ-embedded vs standalone vs different hosts), and rebinding
 *  zustand actions to the right api is cleaner with a fresh store
 *  per mount than a module-level singleton + setter dance. */
export function MusicPage({ theme, wsApi, api, capabilities }: MusicPageProps) {
  const caps = capabilities ?? DEFAULT_CAPABILITIES
  const store = useMemo(() => createMusicStore(api), [api])

  const inner = (
    <ApiContext.Provider value={api}>
      <WSContext.Provider
        value={wsApi ?? { send: () => {}, subscribe: () => () => {}, isConnected: false }}
      >
        <CapabilitiesContext.Provider value={caps}>
          <StoreProvider value={store}>
            <MusicPageInner />
          </StoreProvider>
        </CapabilitiesContext.Provider>
      </WSContext.Provider>
    </ApiContext.Provider>
  )

  return theme ? <ThemeProvider theme={theme}>{inner}</ThemeProvider> : inner
}


function MusicPageInner() {
  useMusicWiring()
  const error = useMusicStore((s) => s.music.error)
  const setError = useMusicStore((s) => s.setMusicError)

  // External-binaries (yt-dlp + mpv) lift to here so the gear icon's
  // badge and the Dialog share one fetch. Dialog state owned by parent
  // too — needed for auto-open-on-first-issue (mirrors WW trainer's
  // CorporaSetupDialog flow).
  const deps = useDependencies()
  const [depsOpen, setDepsOpen] = useState(false)
  const needsAttention = dependencyNeedsAttention(deps.status)
  const didInitialDepsCheck = useRef(false)

  // Auto-open ONCE on first detection of an issue. Don't re-pop after
  // the user closes — they've seen it. Re-checks via refresh leave the
  // dialog state alone.
  useEffect(() => {
    if (didInitialDepsCheck.current) return
    if (deps.status === null) return
    didInitialDepsCheck.current = true
    if (needsAttention) setDepsOpen(true)
  }, [deps.status, needsAttention])

  // No standalone "Music" title (2026-07-10 restyle — it repeated the nav
  // entry): the search row IS the header; now-playing + the setup gear ride
  // its right side via the `actions` slot.
  return (
    <Box>
      <DownloadsCard />

      {error && (
        <ErrorAlert error={error} onDismiss={() => setError(null)} sx={{ mb: 2 }} />
      )}

      <LibraryCard
        leading={<NowPlayingCard />}
        actions={
          <Tooltip
            title={
              needsAttention
                ? 'yt-dlp / mpv need attention — open setup'
                : 'Music setup — binaries, library path, playback'
            }
          >
            <IconButton size="small" onClick={() => setDepsOpen(true)}>
              <Badge variant="dot" color="warning" invisible={!needsAttention}>
                <SettingsIcon fontSize="small" />
              </Badge>
            </IconButton>
          </Tooltip>
        }
      />

      <DependenciesDialog
        open={depsOpen}
        onClose={() => setDepsOpen(false)}
        status={deps.status}
        loading={deps.loading}
        error={deps.error}
        refresh={deps.refresh}
      />
    </Box>
  )
}


/** Lightweight inline ErrorAlert — replaces JarvYZ's
 *  components/ErrorAlert which would have been a cross-bundle import. */
function ErrorAlert({
  error,
  onDismiss,
  sx,
}: {
  error: string
  onDismiss: () => void
  sx?: object
}) {
  const [shown, setShown] = useState(true)
  if (!shown) return null
  return (
    <Alert
      severity="error"
      onClose={() => {
        setShown(false)
        onDismiss()
      }}
      sx={sx}
    >
      {error}
    </Alert>
  )
}
