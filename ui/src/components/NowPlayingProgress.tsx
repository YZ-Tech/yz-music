import { useState } from 'react'
import { Slider, Stack, Typography } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'
import { useLiveTime } from '../hooks/useLiveTime'
import { fmtDuration } from '../lib/fmt'

/** Isolated leaf — owns the rAF interpolator and the seek-drag override.
 *  Nothing outside this component re-renders on a time_pos tick. */
export function NowPlayingProgress() {
  const duration = useStore((s) => s.music.nowPlaying.duration)
  const mediaControl = useStore((s) => s.mediaControl)
  const livePos = useLiveTime()
  const [seekDrag, setSeekDrag] = useState<number | null>(null)

  // While dragging, show the user's slider position instead of the
  // rAF-interpolated one. Cleared on commit + a short delay so the
  // slider doesn't snap back to the old livePos before the next WS push.
  const displayPos = seekDrag ?? livePos

  return (
    <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mt: 1 }}>
      <Typography
        variant="caption"
        sx={{
          fontFamily: 'ui-monospace, monospace',
          color: 'text.secondary',
          minWidth: 48,
          textAlign: 'right',
        }}
      >
        {fmtDuration(displayPos)}
      </Typography>
      <Slider
        size="small"
        value={displayPos}
        min={0}
        max={Math.max(1, duration ?? 0)}
        disabled={!duration}
        onChange={(_, v) => setSeekDrag(typeof v === 'number' ? v : v[0])}
        onChangeCommitted={(_, v) => {
          const pos = typeof v === 'number' ? v : v[0]
          mediaControl('seek_abs', Math.floor(pos))
          setTimeout(() => setSeekDrag(null), 600)
        }}
        sx={{
          flex: 1,
          height: 4,
          py: '13px',
          '& .MuiSlider-rail': { opacity: 0.3 },
          '& .MuiSlider-track': {
            border: 'none',
            backgroundColor: 'primary.main',
            opacity: 1,
          },
          '& .MuiSlider-thumb': {
            width: 10,
            height: 10,
            opacity: seekDrag !== null ? 1 : 0,
            transition: 'opacity .12s ease',
          },
          '&:hover .MuiSlider-thumb': { opacity: 1 },
          '& .MuiSlider-thumb::after': { width: 24, height: 24 },
        }}
      />
      <Typography
        variant="caption"
        sx={{
          fontFamily: 'ui-monospace, monospace',
          color: 'text.secondary',
          minWidth: 48,
        }}
      >
        {fmtDuration(duration ?? 0)}
      </Typography>
    </Stack>
  )
}
