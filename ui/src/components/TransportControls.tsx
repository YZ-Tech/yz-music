import { IconButton, Stack, Tooltip } from '@mui/material'
import PauseIcon from '@mui/icons-material/Pause'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import RepeatIcon from '@mui/icons-material/Repeat'
import RepeatOneIcon from '@mui/icons-material/RepeatOne'
import ShuffleIcon from '@mui/icons-material/Shuffle'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import SkipPreviousIcon from '@mui/icons-material/SkipPrevious'
import { useMusicStore as useStore } from '../lib/store-context'
import type { NowPlayingState } from '../types'

const isLoopActive = (v: NowPlayingState['loop_file']): boolean =>
  v !== null && v !== false && v !== 'no' && v !== 0

export function TransportControls() {
  const pause = useStore((s) => s.music.nowPlaying.pause)
  const loopFile = useStore((s) => s.music.nowPlaying.loop_file)
  const loopPlaylist = useStore((s) => s.music.nowPlaying.loop_playlist)
  const mediaControl = useStore((s) => s.mediaControl)

  const loopMode: 'off' | 'all' | 'one' = isLoopActive(loopFile)
    ? 'one'
    : isLoopActive(loopPlaylist)
      ? 'all'
      : 'off'
  const nextLoopAction =
    loopMode === 'off' ? 'loop_all' : loopMode === 'all' ? 'loop_one' : 'loop_off'
  const loopTitle =
    loopMode === 'off'
      ? 'Loop off — click for loop all'
      : loopMode === 'all'
        ? 'Loop playlist — click for loop one'
        : 'Loop one — click to turn off'

  return (
    <Stack direction="row" sx={{ alignItems: 'center', gap: 0.5, flex: '0 0 auto' }}>
      <Tooltip title="Shuffle remaining">
        <IconButton size="small" onClick={() => mediaControl('shuffle')}>
          <ShuffleIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Previous track">
        <IconButton size="small" onClick={() => mediaControl('prev')}>
          <SkipPreviousIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={pause ? 'Resume' : 'Pause'}>
        <IconButton
          size="small"
          onClick={() => mediaControl('pause')}
          sx={{
            bgcolor: 'primary.main',
            color: 'primary.contrastText',
            '&:hover': { bgcolor: 'primary.dark' },
          }}
        >
          {pause ? <PlayArrowIcon fontSize="small" /> : <PauseIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
      <Tooltip title="Next track">
        <IconButton size="small" onClick={() => mediaControl('next')}>
          <SkipNextIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={loopTitle}>
        <IconButton
          size="small"
          onClick={() => mediaControl(nextLoopAction)}
          sx={{ color: loopMode === 'off' ? 'text.disabled' : 'primary.main' }}
        >
          {loopMode === 'one' ? (
            <RepeatOneIcon fontSize="small" />
          ) : (
            <RepeatIcon fontSize="small" />
          )}
        </IconButton>
      </Tooltip>
    </Stack>
  )
}
