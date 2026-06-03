import { useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import SearchIcon from '@mui/icons-material/Search'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import { useApi } from '../lib/api'
import { useMusicStore as useStore } from '../lib/store-context'
import { fmtDuration } from '../lib/fmt'
import type { SearchResult, Source } from '../types'

interface Props {
  source: Source
  onSourceChange: (s: Source) => void
  query: string
  onQueryChange: (q: string) => void
}

/** Search row (toggle + input + button) and — when in YouTube mode — the
 *  results panel below it. Library filter is local UI state; YouTube
 *  results are local too because they're scoped to this card. */
export function LibrarySearch({ source, onSourceChange, query, onQueryChange }: Props) {
  const setError = useStore((s) => s.setMusicError)
  const mediaPlay = useStore((s) => s.mediaPlay)
  const api = useApi()
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)

  const placeholder =
    source === 'local' ? 'Filter library by title or channel' : 'Search YouTube — press Enter'

  const handleSearchYoutube = async () => {
    if (!query.trim()) return
    if (!api.searchYoutube) {
      setError('YouTube search not available in this mode')
      return
    }
    setSearching(true)
    setError(null)
    try {
      const results = await api.searchYoutube(query, 5)
      setSearchResults(results)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  const handlePlay = async (url: string, mode: 'play' | 'next' | 'queue') => {
    try {
      await mediaPlay(url, mode)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardContent>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={source}
              onChange={(_, v) => v && onSourceChange(v)}
            >
              <ToggleButton value="local">Local</ToggleButton>
              <ToggleButton value="youtube">YouTube</ToggleButton>
            </ToggleButtonGroup>
            <TextField
              fullWidth
              size="small"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder={placeholder}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && source === 'youtube') handleSearchYoutube()
              }}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon fontSize="small" />
                    </InputAdornment>
                  ),
                },
              }}
            />
            {source === 'youtube' && (
              <Button
                variant="contained"
                onClick={handleSearchYoutube}
                disabled={searching || !query.trim()}
              >
                {searching ? 'Searching…' : 'Search'}
              </Button>
            )}
          </Stack>
        </CardContent>
      </Card>

      {source === 'youtube' && searchResults.length > 0 && (
        <Card variant="outlined" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
              YouTube results — {searchResults.length}
            </Typography>
            <Stack divider={<Box sx={{ height: 1, bgcolor: 'divider' }} />} spacing={1}>
              {searchResults.map((r) => (
                <Stack
                  key={r.video_id}
                  direction="row"
                  spacing={1.5}
                  sx={{ alignItems: 'center', pt: 1 }}
                >
                  {/* YouTube CDN thumbnail — public, deterministic URL.
                      Hidden silently if request fails. */}
                  <Box
                    sx={{
                      flex: '0 0 auto',
                      width: 100,
                      aspectRatio: '16 / 9',
                      bgcolor: 'action.hover',
                      borderRadius: 1,
                      overflow: 'hidden',
                    }}
                  >
                    <Box
                      component="img"
                      src={`https://i.ytimg.com/vi/${r.video_id}/mqdefault.jpg`}
                      alt=""
                      loading="lazy"
                      referrerPolicy="no-referrer"
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
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 500 }} noWrap>
                      {r.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {r.channel}
                      {r.duration_seconds ? ` · ${fmtDuration(r.duration_seconds)}` : ''}
                    </Typography>
                  </Box>
                  <Tooltip title="Play now">
                    <IconButton size="small" onClick={() => handlePlay(r.url, 'play')}>
                      <PlayArrowIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Play next">
                    <IconButton size="small" onClick={() => handlePlay(r.url, 'next')}>
                      <SkipNextIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Add to queue">
                    <IconButton size="small" onClick={() => handlePlay(r.url, 'queue')}>
                      <AddIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}
    </>
  )
}
