import { Box, Chip, Stack, Switch, Typography } from '@mui/material'
import CancelIcon from '@mui/icons-material/Cancel'
import LoopIcon from '@mui/icons-material/Loop'
import StarIcon from '@mui/icons-material/Star'
import { useMusicStore as useStore } from '../lib/store-context'

export function LibraryFallbackStrip() {
  const fallback = useStore((s) => s.music.fallback)
  const toggleFallback = useStore((s) => s.toggleFallback)
  const setFallbackLoop = useStore((s) => s.setFallbackLoop)
  const setError = useStore((s) => s.setMusicError)

  if (fallback.ids.length === 0) return null

  const handleLoop = async (loop: boolean) => {
    try {
      await setFallbackLoop(loop)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleRemove = async (videoId: string) => {
    try {
      await toggleFallback(videoId, false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Box
      sx={{
        px: 2,
        py: 1,
        borderTop: 1,
        borderColor: 'divider',
        bgcolor: 'background.paper',
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 1,
      }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', gap: 0.5 }}>
        <StarIcon fontSize="small" sx={{ color: 'warning.main' }} />
        <Typography variant="body2" sx={{ fontWeight: 500 }}>
          Ambient fallback ({fallback.ids.length})
        </Typography>
      </Stack>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, ml: 1 }}>
        <LoopIcon
          fontSize="small"
          sx={{ color: fallback.loop ? 'primary.main' : 'text.disabled' }}
        />
        <Switch
          size="small"
          checked={fallback.loop}
          onChange={(e) => handleLoop(e.target.checked)}
        />
        <Typography variant="caption" color="text.secondary">
          {fallback.loop ? 'loop' : 'once'}
        </Typography>
      </Box>
      <Box sx={{ flex: 1 }} />
      <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5 }}>
        {fallback.items.map(({ video_id, metadata }) => (
          <Chip
            key={video_id}
            size="small"
            label={metadata?.title ?? `(missing: ${video_id})`}
            variant={metadata ? 'filled' : 'outlined'}
            color={metadata ? 'default' : 'warning'}
            onDelete={() => handleRemove(video_id)}
            deleteIcon={<CancelIcon fontSize="small" />}
            sx={{ maxWidth: 280 }}
          />
        ))}
      </Stack>
    </Box>
  )
}
