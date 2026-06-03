import { createContext, useContext } from 'react'

export interface Capabilities {
  apiBase: string                          // '' for JarvYZ-embedded; TBD for standalone
  deployTarget: 'jarvis' | 'standalone'    // affects features that need JarvYZ (e.g. some search paths)
}

export const DEFAULT_CAPABILITIES: Capabilities = {
  apiBase: '',
  deployTarget: 'jarvis',
}

export const CapabilitiesContext = createContext<Capabilities>(DEFAULT_CAPABILITIES)

export const useCapabilities = () => useContext(CapabilitiesContext)
