/** The one browser-side copy of the AI copilot profile.
 *
 *  The server's `AppSetting` remains authoritative; this store only keeps the
 *  last read value so the global floating copilot can show a nickname that was
 *  saved on the configuration page without a page reload.  A failed read keeps
 *  the documented default and never blocks the copilot.
 */
import { api } from './api';

export type CopilotProfile = {
  assistant_nickname: string;
  user_salutation: string;
  startup_memory: string;
  memory_enabled: boolean;
  auto_memory_enabled: boolean;
  remote_memory_processing_consent: boolean;
  remote_context_consent: boolean;
  web_search: boolean;
  config_revision: number;
  updated_at: string | null;
};

export const DEFAULT_COPILOT_PROFILE: CopilotProfile = {
  assistant_nickname: 'AI 管家',
  user_salutation: '',
  startup_memory: '',
  memory_enabled: true,
  auto_memory_enabled: false,
  remote_memory_processing_consent: false,
  remote_context_consent: false,
  web_search: false,
  config_revision: 0,
  updated_at: null,
};

let current: CopilotProfile = { ...DEFAULT_COPILOT_PROFILE };
let loading: Promise<CopilotProfile> | null = null;
const listeners = new Set<(profile: CopilotProfile) => void>();

function normalise(raw: Partial<CopilotProfile> | null | undefined): CopilotProfile {
  const value = raw || {};
  return {
    ...DEFAULT_COPILOT_PROFILE,
    ...value,
    assistant_nickname:
      typeof value.assistant_nickname === 'string' && value.assistant_nickname.trim()
        ? value.assistant_nickname.trim()
        : DEFAULT_COPILOT_PROFILE.assistant_nickname,
    user_salutation: typeof value.user_salutation === 'string' ? value.user_salutation : '',
    startup_memory: typeof value.startup_memory === 'string' ? value.startup_memory : '',
    memory_enabled: value.memory_enabled !== false,
    auto_memory_enabled: value.auto_memory_enabled === true,
    remote_memory_processing_consent: value.remote_memory_processing_consent === true,
    remote_context_consent: value.remote_context_consent === true,
    web_search: value.web_search === true,
    config_revision: Number.isFinite(value.config_revision) ? Number(value.config_revision) : 0,
    updated_at: typeof value.updated_at === 'string' ? value.updated_at : null,
  };
}

function notify(): void {
  for (const listener of listeners) listener(current);
}

/** Put a validated server response into the shared store. */
export function applyCopilotProfile(raw: Partial<CopilotProfile> | null | undefined): CopilotProfile {
  current = normalise(raw);
  notify();
  return current;
}

export function getCopilotProfile(): CopilotProfile {
  return current;
}

export function subscribeCopilotProfile(listener: (profile: CopilotProfile) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Read the authoritative profile once; concurrent callers share one request. */
export async function loadCopilotProfile(force = false): Promise<CopilotProfile> {
  if (!force && loading) return loading;
  loading = api<CopilotProfile>('/support/preferences', { cache: 'no-store' })
    .then((payload) => applyCopilotProfile(payload))
    .catch(() => {
      // Offline or unconfigured: the default keeps the floating button usable.
      return current;
    })
    .finally(() => {
      loading = null;
    });
  return loading;
}
