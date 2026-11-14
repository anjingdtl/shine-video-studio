import { getThemeDefinition, parseThemeId, type ThemeId } from './themes';

const STORAGE_KEY = 'shine-theme';
const THEME_COLOR_KEY = 'theme-color';

let current: ThemeId = 'light';
let initialized = false;
let bootstrapped = false;
const listeners = new Set<() => void>();

function readStoredTheme(): ThemeId {
  try {
    return parseThemeId(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    // Private mode / blocked storage: keep the in-memory value and stay usable.
    return 'light';
  }
}

function persistTheme(id: ThemeId): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Storage is an optimisation, never a hard dependency.
  }
}

function notify(): void {
  for (const listener of listeners) listener();
}

function updateThemeColor(id: ThemeId): void {
  if (typeof document === 'undefined') return;
  const definition = getThemeDefinition(id);
  let meta = document.querySelector<HTMLMetaElement>(`meta[name="${THEME_COLOR_KEY}"]`);
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = THEME_COLOR_KEY;
    document.head.appendChild(meta);
  }
  meta.content = definition.preview.background;
}

/** Apply one theme to the document: stable id, appearance and browser colour. */
export function applyThemeToDocument(id: ThemeId): void {
  if (typeof document === 'undefined') return;
  const definition = getThemeDefinition(id);
  const root = document.documentElement;
  root.dataset.theme = id;
  root.dataset.appearance = definition.appearance;
  root.style.colorScheme = definition.appearance;
  updateThemeColor(id);
}

/** Current theme. Reading lazily keeps the store usable before bootstrap. */
export function getTheme(): ThemeId {
  if (!initialized) {
    current = readStoredTheme();
    initialized = true;
  }
  return current;
}

/**
 * Switch themes immediately. Only the document and the local preference are
 * touched: no routing, no API call, no form unmount, no production restart.
 */
export function setTheme(next: unknown): void {
  const id = parseThemeId(next);
  initialized = true;
  const changed = id !== current;
  current = id;
  persistTheme(id);
  applyThemeToDocument(id);
  if (changed) notify();
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function handleStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY) return;
  const next = parseThemeId(event.newValue);
  if (next === current) return;
  current = next;
  initialized = true;
  applyThemeToDocument(next);
  notify();
}

/**
 * One bootstrap for the whole workbench. Called from main.tsx before React
 * renders so the stored theme is restored without a light flash.
 */
export function bootstrapTheme(): ThemeId {
  if (bootstrapped) return current;
  bootstrapped = true;
  current = readStoredTheme();
  initialized = true;
  applyThemeToDocument(current);
  if (typeof window !== 'undefined') window.addEventListener('storage', handleStorage);
  return current;
}

/** Test-only hook: drop listeners and in-memory state without touching storage. */
export function __resetThemeStoreForTests(): void {
  current = 'light';
  initialized = false;
  bootstrapped = false;
  listeners.clear();
}
