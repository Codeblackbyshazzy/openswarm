import React, { createContext, useContext, useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { ClaudeTokens, lightTokens, darkTokens } from './claudeTokens';

type ThemeMode = 'light' | 'dark';

interface ThemeContextValue {
  mode: ThemeMode;
  tokens: ClaudeTokens;
  toggleMode: () => void;
  setMode: (mode: ThemeMode) => void;
}

const STORAGE_KEY = 'self-swarm-theme-mode';

function getInitialMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {}
  // Default light regardless of OS; the real choice arrives from settings a beat later, and matching that default here keeps the pre-load frame from flashing to the opposite theme.
  return 'light';
}

// A theme swap repaints every element, and the app's hover transitions (transition: background-color / all) would each crossfade the new color at their own duration: that staggered animation IS the flicker. Kill all transitions for the single swap frame so the change is one instant cut; hovers animate again next tick.
function p_suppressTransitionsForSwap(): () => void {
  const killer = document.createElement('style');
  killer.appendChild(document.createTextNode('*,*::before,*::after{transition:none!important}'));
  document.head.appendChild(killer);
  void document.body.offsetHeight; // force a synchronous restyle so the rule applies to this frame
  const id = window.setTimeout(() => killer.remove(), 0);
  return () => { window.clearTimeout(id); killer.remove(); };
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: 'light',
  tokens: lightTokens,
  toggleMode: () => {},
  setMode: () => {},
});

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setModeState] = useState<ThemeMode>(getInitialMode);
  const firstMount = useRef(true);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, mode); } catch {}
    if (firstMount.current) { firstMount.current = false; return; }
    return p_suppressTransitionsForSwap();
  }, [mode]);

  const tokens = useMemo(() => (mode === 'dark' ? darkTokens : lightTokens), [mode]);

  // Stable identities: SettingsLoader's "apply settings.theme" effect lists setMode in its deps, so a setter that changed every render made that effect re-fire on each toggle and re-assert the OLD persisted theme until the debounced save caught up: live theme snapped back for ~900ms = the switch flicker.
  const toggleMode = useCallback(() => setModeState((m) => (m === 'light' ? 'dark' : 'light')), []);
  const setMode = useCallback((m: ThemeMode) => setModeState(m), []);

  const value = useMemo(() => ({ mode, tokens, toggleMode, setMode }), [mode, tokens, toggleMode, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export const useClaudeTokens = (): ClaudeTokens => useContext(ThemeContext).tokens;
export const useThemeMode = () => {
  const { mode, toggleMode, setMode } = useContext(ThemeContext);
  return { mode, toggleMode, setMode };
};
