import { test, expect } from '@playwright/test'

/** Standalone-mode smoke + contract tests for the music satellite.
 *
 *  Verifies (without mutating mpv state):
 *  - SPA loads + standalone shell renders
 *  - HTTP contract: /health, /library shape, /settings round-trip, /downloads shape
 *  - UI: real track titles surface from /library, audio-only toggle flips
 *  - WS /events emits an initial now_playing snapshot on connect
 *
 *  Run from satellites/yz-music/ui/:  npx playwright test
 *
 *  Deliberately does NOT exercise POST /play or POST /control with
 *  pause/seek — those mutate shared mpv state which the user may be
 *  using right now (JarvYZ-embedded and standalone share the same
 *  IPC socket). Settings tests restore their pre-test value at the
 *  end so the suite is idempotent. */


// ─────────────────────── SPA shell ────────────────────────────────


test('standalone SPA loads + header strip + library renders', async ({ page }) => {
  await page.goto('/')

  // Header strip rendered (from App.tsx StandaloneHeader). Selector by
  // src instead of role because the logo is decorative (alt="") so its
  // ARIA role flips to "presentation" — getByRole('img') wouldn't find
  // it. Asserting by src also proves the public/logo.svg is being
  // served by FastAPI StaticFiles.
  await expect(page.locator('header img[src="/logo.svg"]')).toBeVisible()
  await expect(page.locator('header').getByText('Music', { exact: true })).toBeVisible()
  await expect(page.getByText(/satellite · standalone/i)).toBeVisible()

  // Library data-grid rendered with at least one real track row beyond
  // the header. Proves api.library() → /library round-trip end-to-end.
  await expect(page.locator('[role="row"]').nth(1)).toBeVisible({ timeout: 10_000 })
})


// ─────────────────────── /health ──────────────────────────────────


test('GET /health responds 200 ok', async ({ request }) => {
  const res = await request.get('/health')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  expect(body.ok).toBe(true)
  expect(body.version).toMatch(/^\d+\.\d+\.\d+$/)
  expect(body.python).toMatch(/^\d+\.\d+/)
  expect(body.platform).toMatch(/linux|win32|darwin/)
})


// ─────────────────────── /library shape ───────────────────────────


test('GET /library returns array of well-shaped tracks', async ({ request }) => {
  const res = await request.get('/library')
  expect(res.ok()).toBeTruthy()
  const items = await res.json()
  expect(Array.isArray(items)).toBe(true)

  // Empty library is a valid satellite state (a fresh install). The
  // shape test only fires when there's content to inspect.
  if (items.length === 0) test.info().annotations.push({
    type: 'note', description: 'library is empty — shape assertions skipped',
  })
  if (items.length > 0) {
    const sample = items[0]
    expect(sample).toHaveProperty('video_id')
    expect(sample).toHaveProperty('title')
    expect(sample).toHaveProperty('channel')
    expect(sample).toHaveProperty('size_mb')
    expect(sample).toHaveProperty('mtime')
    expect(sample).toHaveProperty('path')
    expect(sample).toHaveProperty('url')
    expect(sample).toHaveProperty('is_fallback')
    expect(sample.video_id).toMatch(/^[A-Za-z0-9_-]{11}$/)
    expect(sample.url).toContain(sample.video_id)
  }
})


// ─────────────────────── /downloads shape ─────────────────────────


test('GET /downloads returns {downloads: []} shape', async ({ request }) => {
  const res = await request.get('/downloads')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  expect(body).toHaveProperty('downloads')
  expect(Array.isArray(body.downloads)).toBe(true)
})


// ─────────────────────── /settings round-trip ─────────────────────


test('PATCH /settings round-trips + restores', async ({ request }) => {
  const before = await (await request.get('/settings')).json()
  expect(before).toHaveProperty('library_path')
  expect(before).toHaveProperty('audio_only')
  expect(before).toHaveProperty('audio_delay_ms')
  expect(before).toHaveProperty('fallback_video_ids')
  expect(before).toHaveProperty('fallback_loop')

  // Flip audio_only, verify it took, restore.
  const flipped = !before.audio_only
  const patchRes = await request.patch('/settings', {
    data: { audio_only: flipped },
  })
  expect(patchRes.ok()).toBeTruthy()
  const after = await patchRes.json()
  expect(after.audio_only).toBe(flipped)

  const restored = await (await request.patch('/settings', {
    data: { audio_only: before.audio_only },
  })).json()
  expect(restored.audio_only).toBe(before.audio_only)
})


// ─────────────────────── UI: real titles surface ──────────────────


test('library row cells expose real track metadata', async ({ page, request }) => {
  // Skip when library is empty (fresh install).
  const items = await (await request.get('/library')).json()
  test.skip(!Array.isArray(items) || items.length === 0, 'library empty — skipping UI assertion')

  await page.goto('/')

  // Use the FIRST track from the API as the search target. Any partial
  // substring of its title should appear in the grid somewhere.
  const titleNeedle = items[0].title.split(/\s+/).find((w: string) => w.length >= 4)
  if (titleNeedle) {
    await expect(page.locator('[role="row"]').filter({ hasText: titleNeedle }).first())
      .toBeVisible({ timeout: 10_000 })
  } else {
    // Fall back: just assert any cell text matches the channel.
    await expect(page.getByText(items[0].channel, { exact: false }).first())
      .toBeVisible({ timeout: 10_000 })
  }
})


// ─────────────────────── UI: audio-only toggle flips ──────────────


test('audio-only toggle flips + persists to satellite', async ({ page, request }) => {
  const before = (await (await request.get('/settings')).json()).audio_only

  await page.goto('/')

  // MUI Switch renders as <input type="checkbox"> with no role="switch"
  // attribute. Targeting via the visible label string is robust and
  // semantic — the FormControlLabel wires htmlFor→input id for us.
  const toggle = page.getByLabel(/audio-only playback/i)
  await expect(toggle).toBeVisible({ timeout: 5_000 })

  // Reflects the satellite's current state on first render.
  if (before) await expect(toggle).toBeChecked()
  else await expect(toggle).not.toBeChecked()

  // Click toggles + saves. Wait for the PATCH to land server-side.
  await toggle.click()
  await page.waitForResponse(
    (r) => r.url().endsWith('/settings') && r.request().method() === 'PATCH' && r.ok(),
    { timeout: 5_000 },
  )

  // Satellite now reports the flipped value.
  const flipped = (await (await request.get('/settings')).json()).audio_only
  expect(flipped).toBe(!before)

  // Restore via API so the suite is idempotent across re-runs.
  await request.patch('/settings', { data: { audio_only: before } })
})


// ─────────────────────── WS /events emits ─────────────────────────


test('WS /events pushes an initial now_playing frame on connect', async ({ page }) => {
  // Open WS from inside the page so it shares origin + same CORS rules
  // as the real SPA does in production.
  await page.goto('/')

  const frame = await page.evaluate(
    () => new Promise<unknown>((resolve, reject) => {
      const ws = new WebSocket(`ws://${location.host}/events`)
      const t = setTimeout(() => {
        ws.close()
        reject(new Error('timeout waiting for /events frame'))
      }, 5_000)
      ws.onmessage = (e) => {
        clearTimeout(t)
        ws.close()
        try { resolve(JSON.parse(e.data)) } catch { resolve(e.data) }
      }
      ws.onerror = () => {
        clearTimeout(t)
        reject(new Error('ws error'))
      }
    }),
  )

  expect(frame).toHaveProperty('event')
  // server.py's @events handler sends an initial now_playing snapshot
  // before entering the queue loop.
  expect((frame as { event: string }).event).toBe('now_playing')
})
