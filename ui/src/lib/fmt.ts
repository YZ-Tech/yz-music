export function fmtAge(mtime: number): string {
  const days = (Date.now() / 1000 - mtime) / 86400
  if (days < 1) return 'today'
  if (days < 30) return `${Math.floor(days)}d`
  if (days < 365) return `${Math.floor(days / 30)}mo`
  return `${Math.floor(days / 365)}y`
}

export function fmtDuration(s: number | null): string {
  if (!s || s <= 0) return ''
  const total = Math.floor(s)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const r = total % 60
  if (h) return `${h}:${m.toString().padStart(2, '0')}:${r.toString().padStart(2, '0')}`
  return `${m}:${r.toString().padStart(2, '0')}`
}

export function fmtTotalSize(mbSum: number): string {
  if (mbSum < 1024) return `${Math.round(mbSum)} MB`
  return `${(mbSum / 1024).toFixed(1)} GB`
}

export function fmtTotalLength(seconds: number): string {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h) return `${h}h ${m}m`
  return `${m}m`
}
