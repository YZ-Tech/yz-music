import { useState } from 'react'
import { Box, Button, TextField, Typography } from '@mui/material'
import { useMusicStore as useStore } from '../lib/store-context'

interface Props {
  /** Saved path from the store. Pass as both prop and React `key` from
   *  the parent so the editor remounts (resetting the local draft) when
   *  the store value changes. */
  path: string
}

export function LibraryPathEditor({ path }: Props) {
  const saveLibraryPath = useStore((s) => s.saveLibraryPath)
  const setError = useStore((s) => s.setMusicError)
  // Lazy init from the prop. The parent owns "when to reset" via key —
  // this component never needs to sync draft↔path itself.
  const [draft, setDraft] = useState<string>(path)

  const handleSave = async () => {
    const trimmed = draft.trim()
    if (!trimmed || trimmed === path) return
    try {
      await saveLibraryPath(trimmed)
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
        gap: 1,
        flexWrap: 'wrap',
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 500, minWidth: 100 }}>
        Library path
      </Typography>
      <TextField
        size="small"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={path || 'D:\\Media\\YouTube'}
        sx={{ flex: 1, minWidth: 240, maxWidth: 480 }}
      />
      <Button
        size="small"
        variant="outlined"
        disabled={!draft.trim() || draft.trim() === path}
        onClick={handleSave}
      >
        Save
      </Button>
      <Typography variant="caption" color="text.secondary">
        yt-play picks up on next spawn — no JarvYZ restart
      </Typography>
    </Box>
  )
}
