#!/usr/bin/env node
// Copy the built IIFE to BOTH:
//   - frontend/public/modules/  (Vite source-of-truth for public assets)
//   - backend/jarvyz/web/static/modules/       (Jarvis production-serve dir, the
//                                actual outDir of the frontend's Vite build)
//
// Why both: Jarvis serves `backend/jarvyz/web/static/` directly (see web/server.py),
// NOT `frontend/public/`. The frontend's Vite build copies public/ →
// ../backend/jarvyz/web/static/ as part of its pipeline. During dev iteration on the
// music module, we don't want to require a full frontend rebuild just to
// deploy a new IIFE. So `npm run ship` lands the file in both places.
//
// Cross-platform (no `cp` — works on Windows cmd, WSL bash, Git Bash).
import { copyFileSync, mkdirSync, statSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// Layout: satellites/yz-music/ui/scripts/ → climb 4 levels to project root
const src = resolve(here, '..', 'dist-lib', 'yz-music.iife.js')
const projectRoot = resolve(here, '..', '..', '..', '..')
const targets = [
  resolve(projectRoot, 'frontend', 'public', 'modules', 'yz-music.iife.js'),
  resolve(projectRoot, 'backend', 'jarvyz', 'web', 'static', 'modules', 'yz-music.iife.js'),
]

try {
  statSync(src)
} catch {
  console.error(`✗ ${src} not found. Run \`npm run build:lib\` first.`)
  process.exit(1)
}

console.log(`✓ ${src}`)
for (const dst of targets) {
  mkdirSync(dirname(dst), { recursive: true })
  copyFileSync(src, dst)
  const { size } = statSync(dst)
  console.log(`  → ${dst}`)
  console.log(`    ${(size / 1024).toFixed(1)} KB`)
}
