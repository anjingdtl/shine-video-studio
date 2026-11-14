import { useSyncExternalStore } from 'react';
import { getTheme, subscribeTheme } from './themeStore';
import type { ThemeId } from './themes';

/** Subscribe any component to the one theme source. */
export function useTheme(): ThemeId {
  return useSyncExternalStore(subscribeTheme, getTheme, getTheme);
}
