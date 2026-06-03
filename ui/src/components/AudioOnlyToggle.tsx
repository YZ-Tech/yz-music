import { Box, FormControlLabel, Switch, Tooltip, Typography } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'

/** Play audio only (no video window). Useful when the Music page is for
 *  listening, not watching. On WSL it additionally dodges the RDP
 *  video-vs-audio channel contention. Setting is read at every spawn —
 *  no JarvYZ restart, takes effect on the next time you click play. */
export function AudioOnlyToggle() {
  const audioOnly = useStore((s) => s.music.audioOnly)
  const save = useStore((s) => s.saveAudioOnly)
  const setError = useStore((s) => s.setMusicError)

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    try {
      await save(e.target.checked)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
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
        gap: 1,
        flexWrap: 'wrap',
      }}
    >
      <Tooltip title="When on, mpv runs with --no-video. Audio only — no mpv window. Useful when you're listening, not watching. Default is on for Linux/WSL, off for Windows." arrow>
        <FormControlLabel
          control={<Switch size="small" checked={audioOnly} onChange={handleChange} />}
          label={
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              Audio-only playback
            </Typography>
          }
        />
      </Tooltip>
      <Typography variant="caption" color="text.secondary">
        applied on next mpv launch
      </Typography>
    </Box>
  )
}
