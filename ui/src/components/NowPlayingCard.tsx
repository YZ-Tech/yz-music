import { useMemo } from 'react'
import { Box, Card, CardContent, Stack, Typography } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'
import { useApi } from '../lib/api'
import { TransportControls } from './TransportControls'
import { NowPlayingProgress } from './NowPlayingProgress'

export function NowPlayingCard() {
  const path = useStore((s) => s.music.nowPlaying.path)
  const videoId = useStore((s) => s.music.nowPlaying.video_id)
  const idle = useStore((s) => s.music.nowPlaying.idle)
  const playlistPos = useStore((s) => s.music.nowPlaying.playlist_pos)
  const playlistCount = useStore((s) => s.music.nowPlaying.playlist_count)
  const library = useStore((s) => s.music.library)
  const api = useApi()

  // Resolve currently-playing track title from the library when mpv reports
  // a video_id. Fallback: whatever's after the last slash in path.
  const npTitle = useMemo(() => {
    if (!videoId) {
      if (!path) return ''
      const tail = path.split(/[\\/]/).pop() ?? path
      return tail.replace(/\s*\[[A-Za-z0-9_-]{11}\]\.\w+$/, '')
    }
    const item = library.find((i) => i.video_id === videoId)
    return item ? item.title : videoId
  }, [videoId, path, library])

  const npChannel = useMemo(() => {
    if (!videoId) return ''
    return library.find((i) => i.video_id === videoId)?.channel ?? ''
  }, [videoId, library])

  if (idle || !path) return null

  return (
    <Card variant="outlined" sx={{ flex: 1, minWidth: 0, maxWidth: 875 }}>
      <CardContent sx={{ pb: '12px !important' }}>
        {/* Row 1: thumbnail + title + transport controls (far right) */}
        <Stack direction="row" sx={{ alignItems: 'center', gap: 1.5 }}>
          {videoId && (
            <Box
              sx={{
                flex: '0 0 auto',
                width: 80,
                aspectRatio: '16 / 9',
                bgcolor: 'action.hover',
                borderRadius: 1,
                overflow: 'hidden',
              }}
            >
              <Box
                component="img"
                key={videoId /* force reload on track change */}
                src={api.thumbnailUrl?.(videoId) ?? `/api/media/thumbnail/${videoId}`}
                alt=""
                loading="lazy"
                sx={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                  display: 'block',
                }}
                onError={(e) => {
                  ;(e.currentTarget as HTMLImageElement).style.visibility = 'hidden'
                }}
              />
            </Box>
          )}
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
              {npTitle || 'Now playing'}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {npChannel}
              {playlistCount && playlistCount > 1
                ? ` · ${(playlistPos ?? 0) + 1} / ${playlistCount}`
                : ''}
            </Typography>
          </Box>
          <TransportControls />
        </Stack>

        {/* Row 2: time | progress | duration — its own component so the
            rAF re-render is confined here, not the whole card. */}
        <NowPlayingProgress />
      </CardContent>
    </Card>
  )
}
