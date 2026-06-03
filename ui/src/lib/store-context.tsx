// Per-mount StoreContext. Each MusicPage mount creates its own store
// (factory bound to the injected MusicApi) and provides it via this
// context. Children call `useMusicStore(selector)` — same body shape as
// JarvYZ's `useStore(selector)`, only the hook name + identity differ.

import { createContext, useContext } from 'react'
import type { MusicState, MusicStore } from '../store'

const StoreContext = createContext<MusicStore | null>(null)

export const StoreProvider = StoreContext.Provider

/** Read from the Music store. Throws when used outside a StoreProvider —
 *  catches the "forgot to wrap" mistake at the first hook call. */
export function useMusicStore<T>(selector: (s: MusicState) => T): T {
  const store = useContext(StoreContext)
  if (!store) throw new Error('useMusicStore called outside StoreProvider')
  return store(selector)
}
