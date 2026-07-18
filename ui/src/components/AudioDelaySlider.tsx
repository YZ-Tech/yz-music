import { useEffect, useState } from 'react'
import { Box, Slider, Tooltip, Typography } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'

/** Lipsync / audio-delay compensation (ms). Shifts mpv's audio earlier to
 *  line up with downstream speaker/Bluetooth latency. Same value the voice
 *  command "audio delay 400" / "lipsync delay <ms>" sets — persisted to the
 *  satellite settings and live-applied to a playing mpv via the control IPC. */
export function AudioDelaySlider() {
  const stored = useStore((s) => s.music.audioDelayMs)
  const save = useStore((s) => s.saveAudioDelay)
  const setError = useStore((s) => s.setMusicError)

  // Local draft so dragging is smooth; commit (persist + live-apply) only on
  // release, not on every tick.
  const [local, setLocal] = useState(stored)
  useEffect(() => { setLocal(stored) }, [stored])

  const commit = async (_e: unknown, v: number | number[]) => {
    const ms = Array.isArray(v) ? v[0] : v
    try {
      await save(ms)
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
        gap: 2,
        flexWrap: 'wrap',
      }}
    >
      <Tooltip
        title="Audio-delay compensation. Positive ms shifts audio EARLIER (compensates downstream speaker / Bluetooth latency); negative shifts it LATER (when the video path lags the audio). Same as the voice command 'audio delay 400'. 0 = off. Persisted; live-applies to a playing track. The backend accepts up to ±3000 via voice."
        arrow
      >
        <Typography variant="body2" sx={{ fontWeight: 500, minWidth: 110 }}>
          Lipsync delay
        </Typography>
      </Tooltip>
      <Slider
        size="small"
        value={local}
        min={-1000}
        max={1000}
        step={10}
        marks={[
          { value: -1000, label: '-1000' },
          { value: -500, label: '-500' },
          { value: 0, label: '0' },
          { value: 500, label: '500' },
          { value: 1000, label: '1000' },
        ]}
        valueLabelDisplay="auto"
        valueLabelFormat={(v) => `${v} ms`}
        onChange={(_e, v) => setLocal(Array.isArray(v) ? v[0] : v)}
        onChangeCommitted={commit}
        sx={{ flex: 1, minWidth: 160 }}
      />
      <Typography variant="caption" color="text.secondary" sx={{ minWidth: 52, textAlign: 'right' }}>
        {local} ms
      </Typography>
    </Box>
  )
}
