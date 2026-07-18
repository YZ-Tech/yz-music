import { useMemo, useState } from 'react'
import {
  Box,
  Button,
  Card,
  Checkbox,
  CircularProgress,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import { DataGrid } from '@mui/x-data-grid'
import type {
  GridColDef,
  GridColumnVisibilityModel,
  GridRowSelectionModel,
} from '@mui/x-data-grid'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import GridViewIcon from '@mui/icons-material/GridView'
import LibraryMusicIcon from '@mui/icons-material/LibraryMusic'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import RefreshIcon from '@mui/icons-material/Refresh'
import SkipNextIcon from '@mui/icons-material/SkipNext'
import StarIcon from '@mui/icons-material/Star'
import StarBorderIcon from '@mui/icons-material/StarBorder'
import TableRowsIcon from '@mui/icons-material/TableRows'
import ViewColumnIcon from '@mui/icons-material/ViewColumn'
import { IconBtn } from './common/IconBtn'
import { useMusicStore as useStore } from '../lib/store-context'
import { useApi } from '../lib/api'
import { fmtAge, fmtDuration, fmtTotalLength, fmtTotalSize } from '../lib/fmt'
import type { LibraryItem, PlayMode, Source, View } from '../types'

interface Props {
  source: Source
  query: string
}

const COLUMN_LABELS: Record<string, string> = {
  fallback: 'Fallback',
  title: 'Title',
  channel: 'Channel',
  duration_seconds: 'Length',
  size_mb: 'Size',
  mtime: 'Age',
  actions: 'Actions',
}

const ALL_COLUMNS = [
  'fallback',
  'title',
  'channel',
  'duration_seconds',
  'size_mb',
  'mtime',
  'actions',
] as const

/** Shared loading / empty / no-match state for both views. Renders in
 *  place of the rows so the library region never collapses to a blank,
 *  ambiguous box — the user always sees whether it's loading, genuinely
 *  empty, or filtered to nothing. */
function LibraryPlaceholder({ kind }: { kind: 'loading' | 'empty' | 'no-match' }) {
  const text =
    kind === 'loading'
      ? 'Loading library…'
      : kind === 'no-match'
        ? 'No videos match your search.'
        : 'Your library is empty — search above to download tracks.'
  return (
    <Stack
      sx={{ alignItems: 'center', justifyContent: 'center', gap: 1.5, py: 6, color: 'text.secondary' }}
    >
      {kind === 'loading' ? (
        <CircularProgress size={26} />
      ) : (
        <LibraryMusicIcon sx={{ fontSize: 36, opacity: 0.4 }} />
      )}
      <Typography variant="body2">{text}</Typography>
    </Stack>
  )
}

/** Header + table/grid + selection bar + bulk actions + columns menu.
 *  All UI-only state (selection, view, columnVisibility, confirmDelete,
 *  busyBulk, columnsMenuAnchor) is local — none of it is read from
 *  outside this component. */
export function LibraryBrowser({ source, query }: Props) {
  const library = useStore((s) => s.music.library)
  const libraryLoading = useStore((s) => s.music.libraryLoading)
  const fetchLibrary = useStore((s) => s.fetchLibrary)
  const toggleFallback = useStore((s) => s.toggleFallback)
  const deleteLibraryItem = useStore((s) => s.deleteLibraryItem)
  const mediaPlay = useStore((s) => s.mediaPlay)
  const setError = useStore((s) => s.setMusicError)
  const api = useApi()

  const [view, setView] = useState<View>('table')
  const [selection, setSelection] = useState<GridRowSelectionModel>({
    type: 'include',
    ids: new Set(),
  })
  const [columnVisibility, setColumnVisibility] = useState<GridColumnVisibilityModel>({})
  // Length is hidden for the LOCAL library — the scan never populates
  // duration_seconds (needs ffprobe per file; satellite server.py marks it
  // future work), so it rendered as a 100%-empty column (2026-07-10).
  // YouTube results DO carry durations, so the column returns there.
  const effectiveColumnVisibility = useMemo<GridColumnVisibilityModel>(
    () =>
      source === 'local'
        ? { ...columnVisibility, duration_seconds: false }
        : columnVisibility,
    [columnVisibility, source],
  )
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busyBulk, setBusyBulk] = useState(false)
  const [columnsMenuAnchor, setColumnsMenuAnchor] = useState<HTMLElement | null>(null)

  const filteredLibrary = useMemo(() => {
    if (source !== 'local' || !query) return library
    const q = query.toLowerCase()
    return library.filter(
      (i) => i.title.toLowerCase().includes(q) || i.channel.toLowerCase().includes(q),
    )
  }, [library, source, query])

  const selectedIds = useMemo(() => {
    return selection.ids ? Array.from(selection.ids as Set<string>) : []
  }, [selection])

  const totalSizeMb = useMemo(
    () => filteredLibrary.reduce((acc, i) => acc + (i.size_mb || 0), 0),
    [filteredLibrary],
  )
  const totalLengthSec = useMemo(
    () => filteredLibrary.reduce((acc, i) => acc + (i.duration_seconds || 0), 0),
    [filteredLibrary],
  )

  const selectionCount = selectedIds.length

  const clearSelection = () => {
    setSelection({ type: 'include', ids: new Set() })
    setConfirmDelete(false)
  }

  const handleFallbackToggle = async (videoId: string, makeFallback: boolean) => {
    try {
      await toggleFallback(videoId, makeFallback)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handlePlay = async (url: string, mode: PlayMode) => {
    try {
      await mediaPlay(url, mode)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleBulkPlay = async (mode: PlayMode) => {
    if (!selectedIds.length) return
    setBusyBulk(true)
    try {
      // For "play": replace with the first, then append the rest as queue so
      // the selected set becomes the new playlist starting from the first row.
      // For "next" / "queue": iterate in selection order, let mpv slot them in.
      const items = filteredLibrary.filter((i) => selectedIds.includes(i.video_id))
      if (!items.length) return
      if (mode === 'play') {
        await mediaPlay(items[0].url, 'play')
        for (let i = 1; i < items.length; i++) {
          await mediaPlay(items[i].url, 'queue')
        }
      } else {
        for (const it of items) {
          await mediaPlay(it.url, mode)
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyBulk(false)
    }
  }

  const handleBulkDelete = async () => {
    if (!selectedIds.length) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setBusyBulk(true)
    try {
      for (const id of selectedIds) {
        await deleteLibraryItem(id)
      }
      clearSelection()
      await fetchLibrary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyBulk(false)
    }
  }

  const handleRefresh = async () => {
    try {
      await fetchLibrary()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const columns: GridColDef<LibraryItem>[] = [
    {
      field: 'fallback',
      headerName: '★',
      width: 50,
      sortable: false,
      disableColumnMenu: true,
      valueGetter: (_v, row) => row.is_fallback,
      renderCell: (p) => (
        <Tooltip
          title={p.row.is_fallback ? 'Remove from ambient fallback' : 'Add to ambient fallback'}
        >
          <IconButton
            size="small"
            onClick={() => handleFallbackToggle(p.row.video_id, !p.row.is_fallback)}
          >
            {p.row.is_fallback ? (
              <StarIcon fontSize="small" sx={{ color: 'warning.main' }} />
            ) : (
              <StarBorderIcon fontSize="small" sx={{ color: 'text.disabled' }} />
            )}
          </IconButton>
        </Tooltip>
      ),
    },
    { field: 'title', headerName: 'Title', flex: 2, minWidth: 200 },
    {
      field: 'channel',
      headerName: 'Channel',
      flex: 1,
      minWidth: 120,
      renderCell: (p) => (
        <Typography variant="caption" color="text.secondary">
          {p.value}
        </Typography>
      ),
    },
    {
      field: 'duration_seconds',
      headerName: 'Length',
      type: 'number',
      width: 95,
      renderCell: (p) => (
        <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
          {fmtDuration(p.value as number | null)}
        </Typography>
      ),
    },
    {
      field: 'size_mb',
      headerName: 'Size',
      type: 'number',
      width: 110,
      renderCell: (p) => (
        <Typography variant="caption" sx={{ fontFamily: 'ui-monospace, monospace' }}>
          {p.value} MB
        </Typography>
      ),
    },
    {
      field: 'mtime',
      headerName: 'Age',
      type: 'number',
      width: 80,
      renderCell: (p) => (
        <Typography variant="caption" color="text.secondary">
          {fmtAge(p.value as number)}
        </Typography>
      ),
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 170,
      sortable: false,
      disableColumnMenu: true,
      renderCell: (p) => (
        <Stack direction="row" spacing={0.5} sx={{ justifyContent: 'flex-end', width: '100%' }}>
          <Tooltip title="Play now">
            <IconButton size="small" onClick={() => handlePlay(p.row.url, 'play')}>
              <PlayArrowIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Play next">
            <IconButton size="small" onClick={() => handlePlay(p.row.url, 'next')}>
              <SkipNextIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Add to queue">
            <IconButton size="small" onClick={() => handlePlay(p.row.url, 'queue')}>
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ]

  return (
    <>
      <Box
        sx={{
          p: 2,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          flexWrap: 'wrap',
        }}
      >
        <Typography variant="subtitle2" color="text.secondary">
          Library — {filteredLibrary.length} {filteredLibrary.length === 1 ? 'video' : 'videos'}
          {source === 'local' && query && library.length !== filteredLibrary.length
            ? ` (of ${library.length})`
            : ''}
          {filteredLibrary.length > 0 && (
            <>
              {' · '}
              {fmtTotalSize(totalSizeMb)}
              {totalLengthSec > 0 ? ` · ${fmtTotalLength(totalLengthSec)}` : ''}
            </>
          )}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={view}
            onChange={(_, v) => v && setView(v)}
          >
            <ToggleButton value="table" aria-label="Table view">
              <TableRowsIcon fontSize="small" />
            </ToggleButton>
            <ToggleButton value="grid" aria-label="Grid view">
              <GridViewIcon fontSize="small" />
            </ToggleButton>
          </ToggleButtonGroup>
          {view === 'table' && (
            <Tooltip title="Toggle columns">
              <IconButton size="small" onClick={(e) => setColumnsMenuAnchor(e.currentTarget)}>
                <ViewColumnIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <IconBtn
            label={libraryLoading ? 'Loading…' : 'Refresh'}
            icon={<RefreshIcon />}
            onClick={handleRefresh}
            disabled={libraryLoading}
          />
        </Stack>
      </Box>

      <Menu
        anchorEl={columnsMenuAnchor}
        open={Boolean(columnsMenuAnchor)}
        onClose={() => setColumnsMenuAnchor(null)}
      >
        {ALL_COLUMNS.filter((f) => source !== 'local' || f !== 'duration_seconds').map((field) => {
          const visible = columnVisibility[field] !== false
          return (
            <MenuItem
              key={field}
              onClick={() =>
                setColumnVisibility((prev) => ({
                  ...prev,
                  [field]: !visible,
                }))
              }
            >
              <Checkbox checked={visible} size="small" sx={{ p: 0.5, mr: 1 }} />
              <ListItemText primary={COLUMN_LABELS[field]} />
            </MenuItem>
          )
        })}
      </Menu>

      {selectionCount > 0 && (
        <Box
          sx={{
            px: 2,
            py: 1,
            bgcolor: 'action.selected',
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            flexWrap: 'wrap',
            borderTop: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {selectionCount} selected
          </Typography>
          <Box sx={{ flex: 1 }} />
          {confirmDelete ? (
            <>
              <Typography variant="body2" color="error.main">
                Delete {selectionCount}? Not recoverable.
              </Typography>
              <Button
                size="small"
                color="error"
                variant="contained"
                onClick={handleBulkDelete}
                disabled={busyBulk}
              >
                Yes, delete
              </Button>
              <Button size="small" onClick={() => setConfirmDelete(false)} disabled={busyBulk}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button
                size="small"
                startIcon={<PlayArrowIcon fontSize="small" />}
                onClick={() => handleBulkPlay('play')}
                disabled={busyBulk}
              >
                Play all
              </Button>
              <Button
                size="small"
                startIcon={<SkipNextIcon fontSize="small" />}
                onClick={() => handleBulkPlay('next')}
                disabled={busyBulk}
              >
                Play next
              </Button>
              <Button
                size="small"
                startIcon={<AddIcon fontSize="small" />}
                onClick={() => handleBulkPlay('queue')}
                disabled={busyBulk}
              >
                Queue all
              </Button>
              <Button
                size="small"
                color="error"
                startIcon={<DeleteIcon fontSize="small" />}
                onClick={handleBulkDelete}
                disabled={busyBulk}
              >
                Delete {selectionCount}
              </Button>
              <Button size="small" onClick={clearSelection} disabled={busyBulk}>
                Clear
              </Button>
            </>
          )}
        </Box>
      )}

      {view === 'table' && (
        <Box sx={{ width: '100%' }}>
          <DataGrid<LibraryItem>
            rows={filteredLibrary}
            columns={columns}
            getRowId={(r) => r.video_id}
            loading={libraryLoading}
            slots={{
              noRowsOverlay: () => (
                <LibraryPlaceholder kind={query && library.length > 0 ? 'no-match' : 'empty'} />
              ),
            }}
            checkboxSelection
            disableRowSelectionOnClick
            disableColumnFilter
            disableColumnMenu
            hideFooter
            density="compact"
            rowSelectionModel={selection}
            onRowSelectionModelChange={setSelection}
            columnVisibilityModel={effectiveColumnVisibility}
            onColumnVisibilityModelChange={setColumnVisibility}
            initialState={{
              sorting: { sortModel: [{ field: 'mtime', sort: 'desc' }] },
            }}
            autoHeight
            sx={{
              border: 0,
              '& .MuiDataGrid-columnHeaders': { bgcolor: 'background.paper' },
              '& .MuiDataGrid-cell:focus, & .MuiDataGrid-cell:focus-within': {
                outline: 'none',
              },
              '& .MuiDataGrid-columnHeader:focus, & .MuiDataGrid-columnHeader:focus-within': {
                outline: 'none',
              },
            }}
          />
        </Box>
      )}

      {view === 'grid' && filteredLibrary.length === 0 && (
        <Box sx={{ borderTop: 1, borderColor: 'divider' }}>
          <LibraryPlaceholder
            kind={
              libraryLoading ? 'loading' : query && library.length > 0 ? 'no-match' : 'empty'
            }
          />
        </Box>
      )}

      {view === 'grid' && filteredLibrary.length > 0 && (
        <Box
          sx={{
            p: 2,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: 2,
            borderTop: 1,
            borderColor: 'divider',
          }}
        >
          {filteredLibrary.map((item) => {
            const isSelected = selectedIds.includes(item.video_id)
            return (
              <Card
                key={item.video_id}
                variant="outlined"
                sx={{
                  position: 'relative',
                  cursor: 'default',
                  borderColor: isSelected ? 'primary.main' : 'divider',
                  borderWidth: isSelected ? 2 : 1,
                  overflow: 'hidden',
                  '&:hover .yt-grid-actions': { opacity: 1 },
                }}
              >
                <Box
                  sx={{
                    position: 'relative',
                    aspectRatio: '16 / 9',
                    bgcolor: 'action.hover',
                    overflow: 'hidden',
                  }}
                >
                  <Box
                    component="img"
                    src={api.thumbnailUrl?.(item.video_id) ?? `/api/media/thumbnail/${item.video_id}`}
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
                  <Checkbox
                    size="small"
                    checked={isSelected}
                    onChange={(e) => {
                      setSelection((prev) => {
                        const ids = new Set(prev.ids as Set<string>)
                        if (e.target.checked) ids.add(item.video_id)
                        else ids.delete(item.video_id)
                        return { type: 'include', ids }
                      })
                    }}
                    sx={{
                      position: 'absolute',
                      top: 4,
                      left: 4,
                      bgcolor: 'rgba(0,0,0,.55)',
                      borderRadius: 1,
                      p: 0.25,
                      color: '#fff',
                      '&.Mui-checked': { color: 'primary.main' },
                    }}
                  />
                  {item.duration_seconds ? (
                    <Box
                      sx={{
                        position: 'absolute',
                        bottom: 4,
                        right: 4,
                        bgcolor: 'rgba(0,0,0,.75)',
                        color: '#fff',
                        px: 0.75,
                        py: 0.1,
                        borderRadius: 0.5,
                        fontFamily: 'ui-monospace, monospace',
                        fontSize: 11,
                        lineHeight: '16px',
                      }}
                    >
                      {fmtDuration(item.duration_seconds)}
                    </Box>
                  ) : null}
                  <Stack
                    className="yt-grid-actions"
                    direction="row"
                    spacing={0.25}
                    sx={{
                      position: 'absolute',
                      right: 4,
                      top: 4,
                      bgcolor: 'rgba(0,0,0,.55)',
                      borderRadius: 1,
                      px: 0.25,
                      py: 0.25,
                      opacity: 0,
                      transition: 'opacity .12s ease',
                    }}
                  >
                    <Tooltip title="Play now">
                      <IconButton
                        size="small"
                        sx={{ color: '#fff' }}
                        onClick={() => handlePlay(item.url, 'play')}
                      >
                        <PlayArrowIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Play next">
                      <IconButton
                        size="small"
                        sx={{ color: '#fff' }}
                        onClick={() => handlePlay(item.url, 'next')}
                      >
                        <SkipNextIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Add to queue">
                      <IconButton
                        size="small"
                        sx={{ color: '#fff' }}
                        onClick={() => handlePlay(item.url, 'queue')}
                      >
                        <AddIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </Box>
                <Box sx={{ p: 1.25 }}>
                  <Tooltip title={item.title}>
                    <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                      {item.title}
                    </Typography>
                  </Tooltip>
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {item.channel}
                  </Typography>
                  <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', mt: 0.5 }}>
                    <Tooltip
                      title={
                        item.is_fallback
                          ? 'Remove from ambient fallback'
                          : 'Add to ambient fallback'
                      }
                    >
                      <IconButton
                        size="small"
                        sx={{ p: 0.25 }}
                        onClick={() => handleFallbackToggle(item.video_id, !item.is_fallback)}
                      >
                        {item.is_fallback ? (
                          <StarIcon fontSize="small" sx={{ color: 'warning.main' }} />
                        ) : (
                          <StarBorderIcon fontSize="small" sx={{ color: 'text.disabled' }} />
                        )}
                      </IconButton>
                    </Tooltip>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ fontFamily: 'ui-monospace, monospace', ml: 'auto' }}
                    >
                      {item.size_mb} MB · {fmtAge(item.mtime)}
                    </Typography>
                  </Stack>
                </Box>
              </Card>
            )
          })}
        </Box>
      )}
    </>
  )
}
