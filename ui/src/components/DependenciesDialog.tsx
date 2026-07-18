import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Link,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import WarningIcon from '@mui/icons-material/Warning'
import ErrorIcon from '@mui/icons-material/Error'
import HelpOutlineIcon from '@mui/icons-material/HelpOutlined'
import CloseIcon from '@mui/icons-material/Close'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import RefreshIcon from '@mui/icons-material/Refresh'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'

import {
  useApi,
  type DependenciesStatus,
  type DependencyInfo,
  type DependencyUpdateResult,
} from '../lib/api'
import { useMusicStore as useStore } from '../lib/store-context'
import { AudioDelaySlider } from './AudioDelaySlider'
import { AudioOnlyToggle } from './AudioOnlyToggle'
import { LibraryPathEditor } from './LibraryPathEditor'


interface Props {
  open: boolean
  onClose: () => void
  status: DependenciesStatus | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
}


/** External binaries (yt-dlp + mpv) status + repair UI, surfaced as a
 *  Dialog so it doesn't reflow the Music page when opened. Pattern
 *  matches the wake-word trainer's CorporaSetupDialog: parent owns
 *  `open`, auto-opens once on first detection of an issue, gear icon
 *  in the page header is the always-visible affordance + badge. */
export function DependenciesDialog({ open, onClose, status, loading, error, refresh }: Props) {
  const api = useApi()
  const libraryPath = useStore((s) => s.music.libraryPath)
  const [allBusy, setAllBusy] = useState(false)
  const [allResult, setAllResult] = useState<DependencyUpdateResult | null>(null)

  // How many of the three need action (missing OR outdated) — drives the
  // batch button's badge. can't-tell (outdated === null) is not counted.
  const pending = status
    ? (['ytdlp', 'mpv', 'ffmpeg'] as const).filter(
        (k) => !status[k].found || status[k].outdated === true,
      ).length
    : 0

  const runAll = async () => {
    if (!api.runDependencyUpdateAll) return
    setAllBusy(true)
    setAllResult(null)
    try {
      const r = await api.runDependencyUpdateAll()
      setAllResult(r)
      // async → a window/terminal was launched (re-check picks up the result);
      // noop → nothing to do. Both are worth a refresh.
      if (r.ok && (r.kind === 'async' || r.kind === 'noop')) await refresh()
    } catch (e) {
      setAllResult({ ok: false, error: e instanceof Error ? e.message : String(e) })
    } finally {
      setAllBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      slotProps={{ paper: { sx: { borderRadius: 1 } } }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Typography variant="h6" sx={{ flex: 1 }}>
          Music setup
        </Typography>
        <Tooltip title="Re-check">
          <IconButton size="small" onClick={() => void refresh()} disabled={loading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <IconButton size="small" onClick={onClose} aria-label="close">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <Divider />

      <DialogContent sx={{ pt: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          External binaries
        </Typography>
        {allResult && (
          <Alert
            severity={
              allResult.kind === 'noop'
                ? 'success'
                : allResult.kind === 'async'
                  ? 'info'
                  : allResult.error
                    ? 'error'
                    : 'warning'
            }
            onClose={() => setAllResult(null)}
            sx={{ mb: 2 }}
          >
            {allResult.error
              ? `Install failed to launch: ${allResult.error}`
              : (allResult.message ?? 'Done.')}
          </Alert>
        )}
        {loading && !status ? (
          <Stack direction="row" sx={{ alignItems: 'center', gap: 1, py: 2 }}>
            <CircularProgress size={16} />
            <Typography variant="caption" color="text.secondary">
              Checking yt-dlp, mpv + ffmpeg…
            </Typography>
          </Stack>
        ) : error || !status ? (
          <Alert severity="warning">
            Couldn't check binary dependencies: {error}
          </Alert>
        ) : (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              These are the external CLI tools the music satellite shells out to.
              They aren't Python deps — install them via your OS package manager.
              {' '}<b>yt-dlp</b> needs to stay current (YouTube updates the player
              frequently); <b>mpv</b> is fine to leave at whatever your package
              manager offers; <b>ffmpeg</b> is used for library thumbnails.
            </Typography>
            <Stack spacing={3}>
              <DependencyRow
                id="ytdlp"
                name="yt-dlp"
                dep={status.ytdlp}
                platform={status.platform}              />
              <Divider />
              <DependencyRow
                id="mpv"
                name="mpv"
                dep={status.mpv}
                platform={status.platform}              />
              <Divider />
              <DependencyRow
                id="ffmpeg"
                name="ffmpeg"
                dep={status.ffmpeg}
                platform={status.platform}              />
            </Stack>
          </>
        )}

        {/* Library + playback settings — moved here from the library card's
            tail (2026-07-10): three orphaned rows glued under the table,
            while settings already had a second home behind this gear. The
            rows keep their own borderTop separators. `key` remounts the
            path editor when the saved path changes (resets its draft). */}
        <Typography variant="subtitle2" sx={{ mt: 3 }}>
          Library &amp; playback
        </Typography>
        <LibraryPathEditor key={libraryPath} path={libraryPath} />
        <AudioOnlyToggle />
        <AudioDelaySlider />
      </DialogContent>

      <DialogActions sx={{ justifyContent: 'space-between' }}>
        <Tooltip title="Check all three and install/update only what's missing or outdated — one UAC / sudo prompt for the lot.">
          <span>
            <Button
              variant="contained"
              startIcon={allBusy ? <CircularProgress size={14} /> : <AutoAwesomeIcon />}
              onClick={runAll}
              disabled={allBusy || !status || !api.runDependencyUpdateAll}
            >
              {pending > 0 ? `Install / repair all (${pending})` : 'Install / repair all'}
            </Button>
          </span>
        </Tooltip>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}


// ─────────────────────────── per-dep row ──────────────────────────


function DependencyRow({
  id,
  name,
  dep,
  platform,
}: {
  id: 'ytdlp' | 'mpv' | 'ffmpeg'
  name: string
  dep: DependencyInfo
  platform: string
}) {
  const api = useApi()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<DependencyUpdateResult | null>(null)
  const [copied, setCopied] = useState(false)

  const isMissing = !dep.found
  const isOutdated = dep.found && dep.outdated === true
  const isOk = dep.found && dep.outdated === false
  const isUnknown = dep.found && dep.outdated === null

  const cmd = isMissing ? dep.install_hint.install_cmd : dep.install_hint.update_cmd

  const copyCmd = async () => {
    try {
      await navigator.clipboard.writeText(cmd)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard blocked (insecure context) — user can still select + copy by hand
    }
  }

  const runUpdate = async () => {
    if (!api.runDependencyUpdate) return
    setBusy(true)
    setResult(null)
    try {
      const r = await api.runDependencyUpdate(id)
      setResult(r)
      // async now on every platform: an elevated window / terminal was
      // launched, so there's nothing to show yet — the user clicks the
      // dialog's Re-check when it finishes. No premature auto-refresh.
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Box>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, minWidth: 80 }}>
          {name}
        </Typography>
        {isMissing && (
          <Chip
            size="small"
            icon={<ErrorIcon sx={{ fontSize: 14 }} />}
            label="not found"
            color="error"
          />
        )}
        {isOutdated && (
          <Chip
            size="small"
            icon={<WarningIcon sx={{ fontSize: 14 }} />}
            label="update recommended"
            color="warning"
          />
        )}
        {isOk && (
          <Chip
            size="small"
            icon={<CheckCircleIcon sx={{ fontSize: 14 }} />}
            label="up to date"
            color="success"
          />
        )}
        {isUnknown && (
          <Chip
            size="small"
            icon={<HelpOutlineIcon sx={{ fontSize: 14 }} />}
            label="latest unknown (offline?)"
          />
        )}
        <Box sx={{ flex: 1 }} />
        {dep.upstream && (
          <Tooltip title={dep.upstream.note}>
            <Chip
              size="small"
              icon={<AutoAwesomeIcon sx={{ fontSize: 12 }} />}
              label={`upstream ${dep.upstream.version}`}
              variant="outlined"
              color="info"
              sx={{ height: 22, fontSize: '0.7rem' }}
            />
          </Tooltip>
        )}
        {dep.found && (
          <Tooltip
            title={
              dep.source
                ? `Comparing against ${sourceLabel(dep.source)} (what the install command can deliver).`
                : ''
            }
          >
            <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace', color: 'text.disabled' }}>
              {dep.version}
              {dep.latest && dep.outdated === true && (
                <>
                  {' → '}
                  <Box component="span" sx={{ color: 'warning.main' }}>{dep.latest}</Box>
                </>
              )}
            </Typography>
          </Tooltip>
        )}
      </Stack>

      {dep.path && (
        <Typography variant="caption" sx={{ display: 'block', color: 'text.disabled', fontFamily: 'ui-monospace, monospace', fontSize: '0.7rem', mb: 1 }}>
          {dep.path}
        </Typography>
      )}

      {dep.upstream && (
        <Alert severity="info" sx={{ mb: 1, py: 0.5, fontSize: '0.8rem' }}>
          {dep.upstream.note}
        </Alert>
      )}

      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 0.5 }}>
        {dep.install_hint.label} (on {platform})
      </Typography>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
        <Box
          sx={{
            px: 1,
            py: 0.5,
            bgcolor: 'background.default',
            border: 1,
            borderColor: 'divider',
            borderRadius: 0.5,
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.8rem',
            flex: 1,
            minWidth: 0,
            overflow: 'auto',
            whiteSpace: 'pre',
          }}
        >
          {cmd}
        </Box>
        <Tooltip title={copied ? 'Copied!' : 'Copy command'}>
          <IconButton size="small" onClick={copyCmd}>
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {api.runDependencyUpdate && (isMissing || isOutdated) && (
          <Tooltip
            title={
              platform === 'linux'
                ? 'Spawns a terminal window — sudo prompts there if the command needs it.'
                : platform === 'windows'
                  ? 'Triggers a UAC prompt — click Yes to elevate.'
                  : 'Runs the command (may need admin/sudo permissions).'
            }
          >
            <Button
              size="small"
              variant="outlined"
              color={isMissing ? 'error' : 'warning'}
              startIcon={busy ? <CircularProgress size={12} /> : <PlayArrowIcon />}
              onClick={runUpdate}
              disabled={busy}
            >
              {isMissing ? 'Run install' : 'Run update'}
            </Button>
          </Tooltip>
        )}
        <Tooltip title="Open install docs">
          <IconButton size="small" component={Link} href={dep.install_hint.docs_url} target="_blank" rel="noopener">
            <OpenInNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      {result && (
        <Alert
          severity={alertSeverity(result)}
          sx={{ mt: 1, fontSize: '0.8rem' }}
          onClose={() => setResult(null)}
        >
          {result.kind === 'async' ? (
            <>{result.message ?? `Update launched in ${result.spawned_terminal}.`}</>
          ) : result.kind === 'copy' ? (
            <>{result.message ?? 'No automatic update available — copy the command and run it in a terminal.'}</>
          ) : result.error ? (
            <>Update failed to launch: {result.error}</>
          ) : result.ok ? (
            <>{result.message ?? `Update completed (exit ${result.exit_code}).`} Re-checking…</>
          ) : (
            <>
              {result.message ?? `Update returned exit code ${result.exit_code}.`}
              {result.stdout && (
                <Box component="pre" sx={{ mt: 1, fontSize: '0.7rem', overflow: 'auto', maxHeight: 100, m: 0 }}>
                  {result.stdout}
                </Box>
              )}
              {result.stderr && (
                <Box component="pre" sx={{ mt: 1, fontSize: '0.7rem', overflow: 'auto', maxHeight: 200, m: 0 }}>
                  {result.stderr}
                </Box>
              )}
            </>
          )}
        </Alert>
      )}
    </Box>
  )
}


// ─────────────────────────── helpers ──────────────────────────────


function alertSeverity(r: DependencyUpdateResult): 'info' | 'warning' | 'success' | 'error' {
  if (r.kind === 'async') return 'info'
  if (r.kind === 'copy') return 'warning'
  if (r.error) return 'error'
  return r.ok ? 'success' : 'error'
}


function sourceLabel(source: string): string {
  const [scheme, ...rest] = source.split(':')
  const target = rest.join(':')
  if (scheme === 'github') return `GitHub releases (${target})`
  if (scheme === 'apt') return `apt package '${target}'`
  return source
}
