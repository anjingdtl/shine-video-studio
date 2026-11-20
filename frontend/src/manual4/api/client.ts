/** Isolated API client for Manual4 (`/api/v1/manual4/*` only).
 *  Never calls seven-stage endpoints. */

const M4_API_BASE = '/api/v1';

export type M4StageKey = 'storyboard' | 'materials' | 'production' | 'delivery';

export type M4Blocker = {
  code: string;
  stage: M4StageKey;
  message: string;
  action?: string;
  shot_id?: string;
  shot_code?: string;
  requirement_id?: string;
  run_id?: string;
};

export type M4StageSummary = {
  key: M4StageKey;
  title: string;
  done: boolean;
  status: 'done' | 'active' | 'blocked' | 'pending';
  blocker_count: number;
  blockers: M4Blocker[];
  facts: Record<string, unknown>;
};

export type M4ReadinessVerdict = {
  project_id: string;
  workflow_kind: 'manual4';
  next_stage: M4StageKey;
  all_done: boolean;
  stages: M4StageSummary[];
  can_confirm_storyboard: boolean;
  can_start_production: boolean;
  can_assemble_delivery: boolean;
  summary: Record<string, unknown>;
};

export type M4Overview = {
  project: {
    id: string;
    title: string;
    description: string;
    language: string;
    max_shot_seconds: number;
    workflow_kind: 'manual4';
    created_at: string;
  };
  readiness: M4ReadinessVerdict;
  worker_online: boolean;
  gpu: {
    busy: boolean;
    active_leases: Array<Record<string, unknown>>;
    reconcile_required: Array<Record<string, unknown>>;
  };
  recent_events: Array<{
    id: string;
    stage: string;
    event_type: string;
    entity_type: string;
    entity_id: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
};

export async function m4Request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? undefined);
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    headers.set('x-shine-local-action', '1');
  }
  const response = await fetch(`${M4_API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === 'string') detail = payload.detail;
      else if (payload?.detail?.message) detail = payload.detail.message;
      else if (payload?.detail?.code) detail = payload.detail.code;
    } catch { /* ignore body parse error */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function fetchM4Overview(projectId: string): Promise<M4Overview> {
  return m4Request<M4Overview>(`/manual4/projects/${projectId}/overview`, { cache: 'no-store' });
}
