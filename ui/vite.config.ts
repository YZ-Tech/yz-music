import { defineConfig, type UserConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Mode 'lib': IIFE module loaded by JarvYZ via @yz-dev/react-dynamic-module.
//   - Externalises react/react-dom (host injects via window globals).
//   - Bundles MUI/emotion (theme propagates via ledfx pattern: theme prop +
//     module's own ThemeProvider seeded with it. Same pattern proven by the
//     wakeword-trainer satellite UI.)
//
// Mode 'pages' (default): standalone SPA. Built into ../yz_music/static/ so a
// `pip install yz-music` user gets a working UI at http://127.0.0.1:9002/.
const libConfig: UserConfig = {
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: 'dist-lib',
    emptyOutDir: true,
    lib: {
      entry: fileURLToPath(new URL('./src/index.ts', import.meta.url)),
      name: 'YzMusic',
      formats: ['iife'],
      fileName: () => 'yz-music.iife.js',
    },
    // Zustand v5 transitively pulls in `use-sync-external-store/shim/
    // with-selector` which is CJS-only and does `require("react")` literal.
    // Vite's rollup-plugin-commonjs leaves those require() calls in place
    // because react is `external` — but the IIFE has no module system, so
    // they fail at runtime ("require is not defined").
    //
    // Inject a tiny `require` shim at the top of the IIFE that resolves
    // the externalized modules from window globals. Hacky but explicit:
    // the IIFE is now self-contained even when bundled deps use CJS.
    rollupOptions: {
      external: ['react', 'react-dom'],
      output: {
        globals: { react: 'React', 'react-dom': 'ReactDOM' },
        exports: 'named',
        extend: true,
        banner:
          'var require = function(id) {' +
          ' if (id === "react") return window.React;' +
          ' if (id === "react-dom") return window.ReactDOM;' +
          ' throw new Error("require not handled: " + id);' +
          ' };',
      },
    },
  },
}

const SAT = process.env.VITE_SATELLITE_URL || 'http://127.0.0.1:9002'

const pagesConfig: UserConfig = {
  plugins: [react()],
  server: {
    port: 5183,
    host: '127.0.0.1',
    // Forward satellite-native paths to a running satellite. In production
    // the satellite serves the SPA itself (same origin → no proxy needed).
    proxy: {
      '/health': SAT,
      '/settings': SAT,
      '/play': SAT,
      '/control': SAT,
      '/now_playing': SAT,
      '/library': SAT,
      '/downloads': SAT,
      '/download': SAT,
      '/events': { target: SAT, ws: true },
    },
  },
  build: {
    // Pages-mode output lands INSIDE the Python package so a `pip install`
    // user gets a working UI out of the box. The satellite's server.py
    // conditionally mounts /static when this dir is populated. To rebuild
    // the SPA into this location: `npm run build:pages`.
    outDir: fileURLToPath(new URL('../yz_music/static', import.meta.url)),
    emptyOutDir: true,
  },
}

export default defineConfig(({ mode }) => (mode === 'lib' ? libConfig : pagesConfig))
