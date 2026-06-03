import { Box, Card, CardContent, LinearProgress, Stack, Typography } from '@mui/material'
import CloudDownloadIcon from '@mui/icons-material/CloudDownload'
import ErrorOutlineIcon from '@mui/icons-material/Error'
import { useMusicStore as useStore } from '../lib/store-context'
import type { Download } from '../types'

function DownloadRow({ d }: { d: Download }) {
  const pct = Math.max(0, Math.min(100, d.percent ?? 0))
  const isError = d.status === 'error'
  const isDone = d.status === 'done'
  const isStarting = d.status === 'starting'
  // Lifecycle coloring: indeterminate while starting, real % while
  // downloading, green flash on done, red lock at last-seen % on error.
  const barColor: 'primary' | 'success' | 'error' = isError
    ? 'error'
    : isDone
      ? 'success'
      : 'primary'
  const variant = isStarting ? 'indeterminate' : 'determinate'
  const icon = isError ? (
    <ErrorOutlineIcon fontSize="small" color="error" />
  ) : (
    <CloudDownloadIcon fontSize="small" color={isDone ? 'success' : 'action'} />
  )
  const label = d.title || d.url || 'Downloading…'
  const rightSlot = isError
    ? d.exit_code != null
      ? `exit ${d.exit_code}`
      : 'error'
    : isDone
      ? 'done'
      : isStarting
        ? 'starting…'
        : [`${pct.toFixed(0)}%`, d.eta && `ETA ${d.eta}`, d.rate].filter(Boolean).join(' · ')
  return (
    <Box>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mb: 0.5 }}>
        {icon}
        <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }} noWrap>
          {label}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ flex: '0 0 auto' }}>
          {rightSlot}
        </Typography>
      </Stack>
      <LinearProgress
        variant={variant}
        value={pct}
        color={barColor}
        sx={{ height: 4, borderRadius: 1 }}
      />
    </Box>
  )
}

export function DownloadsCard() {
  const downloads = useStore((s) => s.music.downloads)
  if (downloads.length === 0) return null
  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent sx={{ pb: '12px !important' }}>
        <Stack sx={{ gap: 1 }}>
          {downloads.map((d) => (
            <DownloadRow key={d.id} d={d} />
          ))}
        </Stack>
      </CardContent>
    </Card>
  )
}
