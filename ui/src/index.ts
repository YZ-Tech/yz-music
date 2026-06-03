// Lib (IIFE) entry. The IIFE attaches these exports to `window.YzMusic`;
// JarvYZ loads it via @yz-dev/react-dynamic-module.
export { MusicPage } from './MusicPage'
export type { MusicPageProps } from './MusicPage'
export type { WSApi } from './lib/ws'
export type { Capabilities } from './lib/capabilities'
export { createSatelliteApi, NotSupportedError } from './lib/api'
export type {
  MusicApi,
  SatelliteSettings,
} from './lib/api'
