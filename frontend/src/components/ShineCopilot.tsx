import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, ChevronDown, Download, FileText, Globe, Loader2,
  RefreshCw, RotateCcw, Send, ShieldCheck, Sparkles, Wrench, X,
} from 'lucide-react';
import { API, api } from '../api';
import { currentCopilotFault, faultKey, faultLabel, subscribeCopilotFault, type CopilotFault } from '../copilotContext';
import {
  getCopilotProfile,
  loadCopilotProfile,
  subscribeCopilotProfile,
  type CopilotProfile,
} from '../copilotProfile';
import { TaskGrantCard, type TaskGrant } from './TaskGrantCard';

type Fact = { key: string; value: string; source: string };

export type RecommendedAction = {
  kind: string;
  label: string;
  reason: string;
  requires_authorization: boolean;
  params: Record<string, unknown>;
};

export type CapabilityInfo = {
  action_id: string;
  params?: string[];
  param_contract?: Array<{
    name: string;
    type: string;
    required: boolean;
    binding: string;
    max_length?: number | null;
    enum?: unknown[];
    default?: unknown;
  }>;
  scope?: string;
};

/** The installed build's knowledge/contract binding, in plain language. */
type BuildInfo = {
  state: string;
  notice?: string;
  user_notice?: string;
};

/** Build the preview payload from the capability registry, never from habit.
 *
 *  Every action has its own parameter contract: `managed_release` takes `only`,
 *  `project_create` takes no project, and a project-scoped action may inherit
 *  the current project only when its contract actually declares that field.
 *  The scope sent alongside is separate from the executor parameters.
 */
export function actionPayload(
  action: Pick<RecommendedAction, 'kind' | 'params'>,
  capabilities: Record<string, CapabilityInfo>,
  activeProjectId?: string | null,
): { params: Record<string, unknown>; scopeProjectId: string | null } {
  const capability = capabilities[action.kind];
  const declared = Array.isArray(capability?.params) ? capability!.params! : null;
  const params: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(action.params || {})) {
    if (declared && !declared.includes(key)) continue;
    if (value === undefined || value === null || value === '') continue;
    params[key] = value;
  }
  if (declared && declared.includes('project_id') && !params.project_id && activeProjectId) {
    params.project_id = activeProjectId;
  }
  const scopeProjectId = capability?.scope === 'PROJECT' ? activeProjectId || null : null;
  return { params, scopeProjectId };
}

type HealthCheckItem = {
  code: string;
  label: string;
  status: 'OK' | 'WARNING' | 'BLOCKED' | string;
  detail?: string;
  jump?: string | null;
  error_code?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
};

type HealthCheckGroup = {
  key: string;
  label: string;
  status: 'OK' | 'WARNING' | 'BLOCKED' | string;
  summary?: string;
  jump?: string | null;
  items: HealthCheckItem[];
};

type HealthCheckResponse = {
  generated_at: string;
  project_id: string | null;
  project_title?: string | null;
  summary: { blocking: number; warnings: number; status: string };
  groups: HealthCheckGroup[];
  blockers: Array<HealthCheckItem & { group?: string; group_label?: string }>;
  read_only: boolean;
};

type ReadToolTraceItem = {
  tool: string;
  arguments?: Record<string, unknown>;
  status: string;
  error_code?: string | null;
  elapsed_ms?: number;
  ids?: Record<string, string[]>;
  authority?: string | null;
};

type ActionProposal = {
  type: 'action';
  action_id: string;
  label: string;
  params: Record<string, unknown>;
  reason?: string;
  summary?: string;
  valid: boolean;
  error?: string | null;
  tier?: string;
  gpu?: boolean;
  paid?: boolean;
};

type ProjectGrantProposal = {
  type: 'project_grant';
  objective: string;
  capability_groups: string[];
  reason?: string;
  project_id?: string | null;
  valid: boolean;
  error?: string | null;
};

type ProposalCard = ActionProposal | ProjectGrantProposal;

type KnowledgeCitation = {
  title: string;
  status: string;
  summary: string;
  sources: string[];
  apis?: string[];
  verification?: string;
};

type KnowledgeAnswer = {
  answered: boolean;
  citations: KnowledgeCitation[];
  metadata: { state: string; notice: string; build_commit?: string | null; source_commit?: string | null };
  unknown_reason?: string | null;
};

type Diagnosis = {
  category: string;
  category_label: string;
  severity: string;
  symptom: string;
  facts: Fact[];
  hypotheses?: { cause: string; confidence: string }[];
  recommended_actions: RecommendedAction[];
  next_step: string;
  outcome: string;
  report_kind: 'BLOCKER' | 'BUG' | 'REQUIREMENT';
  confidence: string;
  policy_refusal?: { blocked: string[]; executed: boolean } | null;
  llm?: { used: boolean; status: string };
  readiness_reference?: { mode?: string; label?: string; code?: string | null };
  evidence_id?: string;
};

type CopilotMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  kind: string;
  content: string;
  payload: {
    diagnosis?: Diagnosis;
    tool_trace?: { tool: string; status: string }[];
    read_tools?: ReadToolTraceItem[];
    proposals?: ProposalCard[];
    action_id?: string;
    action_kind?: string;
    status?: string;
    verification?: { outcome?: string; authority?: string; mode?: string };
    last_error?: string | null;
    llm?: { status: string; used?: boolean; model?: string | null };
    intent?: string | null;
    project_knowledge?: KnowledgeAnswer | null;
  };
  created_at: string;
};

type CopilotSession = {
  id: string;
  page_id: string;
  project_id: string | null;
  messages: CopilotMessage[];
  context?: {
    snapshot?: { setup?: { verdict?: { mode?: string; label?: string; code?: string | null } } };
  };
};

type ContextResponse = {
  snapshot: {
    setup?: { verdict?: { mode?: string; label?: string; code?: string | null } };
    runtime?: { llm?: { configured?: boolean; provider?: string | null; model?: string | null } };
  };
  suggested_questions: string[];
  web_search_enabled: boolean;
};

const PANEL_SIZE_KEY = 'shine.copilot.panelSize.v1';
const DEFAULT_PANEL_SIZE = { width: 420, height: 560 };
const MIN_PANEL_WIDTH = 320;
const MIN_PANEL_HEIGHT = 320;

function clampPanelSize(width: number, height: number): { width: number; height: number } {
  const maxW = typeof window !== 'undefined' ? Math.max(MIN_PANEL_WIDTH, Math.min(960, window.innerWidth - 36)) : 960;
  const maxH = typeof window !== 'undefined' ? Math.max(MIN_PANEL_HEIGHT, Math.min(900, window.innerHeight - 96)) : 900;
  return {
    width: Math.round(Math.min(maxW, Math.max(MIN_PANEL_WIDTH, width))),
    height: Math.round(Math.min(maxH, Math.max(MIN_PANEL_HEIGHT, height))),
  };
}

function readPanelSize(): { width: number; height: number } {
  try {
    const raw = window.localStorage.getItem(PANEL_SIZE_KEY);
    if (!raw) return DEFAULT_PANEL_SIZE;
    const parsed = JSON.parse(raw) as { width?: unknown; height?: unknown };
    if (typeof parsed.width === 'number' && typeof parsed.height === 'number') {
      return clampPanelSize(parsed.width, parsed.height);
    }
  } catch { /* ignore */ }
  return DEFAULT_PANEL_SIZE;
}

type ActionState = {
  id: string;
  kind: string;
  label: string;
  status: string;
  preview_hash: string;
  preview: {
    summary?: string;
    blocked?: boolean;
    blocked_code?: string | null;
    download_bytes?: number;
    will_start_gpu?: boolean;
    will_modify_user_comfyui?: boolean;
    authorization_summary?: string;
    processes?: { id: string; kind: string; state: string }[];
    active_production?: { jobs: number; series: number };
    will_create_project?: boolean;
    episodes?: { episode_id: string; title?: string; state?: string; state_label?: string }[];
    blocked_episodes?: { episode_id: string; title?: string; state?: string; state_label?: string }[];
    document?: { filename?: string; source_format?: string; text_length?: number };
    parameter_diff?: { key: string; before: unknown; after: unknown }[];
    blockers?: string[];
    llm_role?: { configured?: boolean; model?: string | null };
  };
  original_task_id: string | null;
  verification?: { outcome?: string; task_status?: string; mode?: string; authority?: string };
  last_error?: string | null;
};

const ACTION_STATUS_LABELS: Record<string, string> = {
  PREVIEWED: '待确认',
  APPROVED: '已确认，等待执行',
  DELEGATED: '已交给原执行器',
  EXECUTED: '已执行，等待权威复核',
  SUCCEEDED: '已完成并通过权威复核',
  FAILED: '执行失败，可重新预览',
  REFUSED: '已拒绝执行，可处理后重新预览',
  EXPIRED: '预览已过期，请重新确认',
  NEEDS_REVIEW: '需要人工核对，可重新预览',
  SUPERSEDED: '原任务已被新任务取代',
  ROLLED_BACK: '已回滚',
};

const TERMINAL_ACTION_STATUSES = new Set([
  'SUCCEEDED', 'FAILED', 'REFUSED', 'EXPIRED', 'NEEDS_REVIEW', 'SUPERSEDED', 'ROLLED_BACK',
]);

type ReportMeta = {
  id: string;
  kind: string;
  title: string;
  filename: string;
  display_path: string;
  status: string;
};

export type ShineCopilotProps = {
  pageId: string;
  pageLabel: string;
  projectId?: string;
  jobId?: string;
  errorCode?: string;
  needsSetup?: boolean;
};

const SESSION_PREFIX = 'shine.copilot.session.';
const MAX_INPUT = 2000;

/** 会话按「项目 + 关联故障」绑定：切换页面/七阶段仍延续，换项目或换任务则分开。 */
function sessionKey(projectId: string | undefined, fault: CopilotFault | null) {
  return `${SESSION_PREFIX}${projectId || 'none'}.${faultKey(fault)}`;
}

function formatBytes(value: number | undefined) {
  if (!value) return '0';
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.ceil(value / 1024)} KB`;
}

function severityLabel(severity: string) {
  return ({ BLOCKER: '阻断', HIGH: '高', MEDIUM: '中', LOW: '低', INFO: '提示' } as Record<string, string>)[severity] || severity;
}

export function ShineCopilot({ pageId, pageLabel, projectId, jobId, errorCode, needsSetup }: ShineCopilotProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<CopilotSession | null>(null);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [fault, setFault] = useState<CopilotFault | null>(() => currentCopilotFault());
  const [profile, setProfile] = useState<CopilotProfile>(() => getCopilotProfile());
  const [preview, setPreview] = useState<ActionState | null>(null);
  const [lastAction, setLastAction] = useState<RecommendedAction | null>(null);
  const [approving, setApproving] = useState(false);
  const [report, setReport] = useState<ReportMeta | null>(null);
  const [reporting, setReporting] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const [togglingWeb, setTogglingWeb] = useState(false);
  const [showEvidence, setShowEvidence] = useState<Record<string, boolean>>({});
  const [capabilities, setCapabilities] = useState<Record<string, CapabilityInfo>>({});
  const [buildInfo, setBuildInfo] = useState<BuildInfo | null>(null);
  const [healthCheck, setHealthCheck] = useState<HealthCheckResponse | null>(null);
  const [healthExpanded, setHealthExpanded] = useState(false);
  //: The objective and optional explicit capability groups of the task grant on screen.
  const [taskObjective, setTaskObjective] = useState<string | null>(null);
  const [taskGroups, setTaskGroups] = useState<string[] | null>(null);
  //: A grant already authorized for this session: reopening the panel
  //: reconnects to the SAME task instead of previewing a second one.
  const [restoredGrant, setRestoredGrant] = useState<TaskGrant | null>(null);
  const [panelSize, setPanelSize] = useState<{ width: number; height: number }>(() => readPanelSize());
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionRef = useRef<CopilotSession | null>(null);
  const handledFaultRef = useRef<number>(0);

  const applySession = useCallback((next: CopilotSession | null) => {
    sessionRef.current = next;
    setSession(next);
  }, []);

  // The active association: a concrete failure (job/attempt/error code) wins
  // over the generic page context.  Switching pages never changes it, so the
  // same diagnosis continues across the seven stages.
  const active = {
    pageId,
    projectId: fault?.projectId || projectId,
    jobId: fault?.jobId || jobId,
    attemptId: fault?.attemptId,
    taskId: fault?.taskId,
    errorCode: fault?.errorCode || errorCode,
  };

  useEffect(() => {
    const unsubscribe = subscribeCopilotProfile(setProfile);
    void loadCopilotProfile();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    void loadCopilotProfile();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const payload = await api<{
          capabilities?: CapabilityInfo[];
          build?: BuildInfo;
        }>('/support/capabilities/actions');
        if (cancelled) return;
        const map: Record<string, CapabilityInfo> = {};
        for (const row of payload.capabilities || []) map[row.action_id] = row;
        setCapabilities(map);
        setBuildInfo(payload.build ?? null);
      } catch {
        // The registry is a contract aid; a failure must not block diagnosis.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const node = scrollRef.current;
    if (!node) return;
    if (typeof node.scrollTo === 'function') node.scrollTo({ top: node.scrollHeight });
    else node.scrollTop = node.scrollHeight;
  }, [open, session]);

  useEffect(
    () =>
      subscribeCopilotFault((next) => {
        if (!next) return;
        setFault(next);
        setOpen(true);
      }),
    [],
  );

  const loadContext = useCallback(async (ctx = active) => {
    try {
      const params = new URLSearchParams({ page_id: ctx.pageId });
      if (ctx.projectId) params.set('project_id', ctx.projectId);
      if (ctx.jobId) params.set('job_id', ctx.jobId);
      if (ctx.attemptId) params.set('attempt_id', ctx.attemptId);
      if (ctx.taskId) params.set('task_id', ctx.taskId);
      if (ctx.errorCode) params.set('error_code', ctx.errorCode);
      const hcParams = new URLSearchParams();
      if (ctx.projectId) hcParams.set('project_id', ctx.projectId);
      const hcUrl = hcParams.toString() ? `/support/health-check?${hcParams.toString()}` : '/support/health-check';
      const [data, hc] = await Promise.all([
        api<ContextResponse>(`/support/context?${params.toString()}`),
        api<HealthCheckResponse>(hcUrl).catch(() => null),
      ]);
      setContext(data);
      setSuggestions(data.suggested_questions);
      setWebEnabled(data.web_search_enabled);
      setHealthCheck(hc);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取本机环境状态。');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ctx is derived from the same props/fault
  }, [pageId, projectId, fault]);

  useEffect(() => {
    const onLlmUpdated = () => {
      if (open) void loadContext();
    };
    window.addEventListener('shine:copilot-llm-updated', onLlmUpdated);
    return () => window.removeEventListener('shine:copilot-llm-updated', onLlmUpdated);
  }, [open, loadContext]);

  const startResize = useCallback(
    (axis: 'w' | 'n' | 'nw') => (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const startW = panelSize.width;
      const startH = panelSize.height;
      let latest = { width: startW, height: startH };
      const onMove = (moveEvt: MouseEvent) => {
        const nextW = axis === 'w' || axis === 'nw' ? startW + (startX - moveEvt.clientX) : startW;
        const nextH = axis === 'n' || axis === 'nw' ? startH + (startY - moveEvt.clientY) : startH;
        latest = clampPanelSize(nextW, nextH);
        setPanelSize(latest);
      };
      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        try { window.localStorage.setItem(PANEL_SIZE_KEY, JSON.stringify(latest)); } catch { /* ignore */ }
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [panelSize],
  );

  const resetPanelSize = useCallback(() => {
    const reset = clampPanelSize(DEFAULT_PANEL_SIZE.width, DEFAULT_PANEL_SIZE.height);
    setPanelSize(reset);
    try { window.localStorage.setItem(PANEL_SIZE_KEY, JSON.stringify(reset)); } catch { /* ignore */ }
  }, []);

  const restore = useCallback(async (ctx = active) => {
    const key = sessionKey(ctx.projectId, fault);
    const stored = window.localStorage.getItem(key);
    if (!stored) {
      applySession(null);
      setRestoredGrant(null);
      await loadContext(ctx);
      return null;
    }
    try {
      const restored = await api<CopilotSession>(`/support/sessions/${stored}`);
      applySession(restored);
      // Reconnect to the session's ONE authorization, if any: the same grant,
      // the same original tasks and the same budget survive a closed panel.
      try {
        const grantState = await api<{ grant: TaskGrant | null }>(
          `/support/sessions/${restored.id}/task-grant`,
        );
        if (grantState.grant) {
          setRestoredGrant(grantState.grant);
          setTaskObjective(grantState.grant.objective);
        } else {
          setRestoredGrant(null);
        }
      } catch {
        setRestoredGrant(null);
      }
      await loadContext(ctx);
      return restored;
    } catch {
      window.localStorage.removeItem(key);
      applySession(null);
      setRestoredGrant(null);
      await loadContext(ctx);
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadContext, fault, applySession]);

  useEffect(() => {
    if (open) void restore();
  }, [open, restore]);

  /** 从失败卡片进入：绑定真实任务/错误码，并只诊断一次。 */
  useEffect(() => {
    if (!fault || fault.at === handledFaultRef.current) return;
    // A second failure can arrive while the first diagnosis is still running.
    // Wait for the current turn to finish instead of dropping the new binding.
    if (busy) return;
    handledFaultRef.current = fault.at;
    setOpen(true);
    setPreview(null);
    setReport(null);
    setError('');
    setNotice('');
    void (async () => {
      const restored = await restore();
      const question = `帮我诊断这个失败${fault.label ? `：${fault.label}` : fault.errorCode ? `：${fault.errorCode}` : ''}`;
      // An existing session must not swallow a NEW fault: rebind it to the
      // current job/attempt/error and diagnose again, instead of returning
      // silently with the old context on screen.
      await ask(question, { rebind: Boolean(restored) });
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fault, busy]);

  const ask = async (question: string, opts?: { rebind?: boolean }) => {
    const content = question.trim();
    if (!content || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    setPreview(null);
    try {
      const current = sessionRef.current;
      if (!current) {
        const created = await api<{ session: CopilotSession }>('/support/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            page_id: active.pageId,
            project_id: active.projectId || null,
            job_id: active.jobId || null,
            attempt_id: active.attemptId || null,
            task_id: active.taskId || null,
            error_code: active.errorCode || null,
            question: content,
          }),
        });
        applySession(created.session);
        window.localStorage.setItem(sessionKey(active.projectId, fault), created.session.id);
      } else {
        const result = await api<{ session: CopilotSession }>(`/support/sessions/${current.id}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(opts?.rebind
            ? {
              content,
              page_id: active.pageId,
              job_id: active.jobId || null,
              attempt_id: active.attemptId || null,
              task_id: active.taskId || null,
              error_code: active.errorCode || null,
            }
            : { content }),
        });
        applySession(result.session);
      }
      setDraft('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '诊断请求没有完成。');
    } finally {
      setBusy(false);
    }
  };

  /** The first natural-language objective (or an AI-proposed project grant) enters
   *  the one-click grant flow directly; a session is created on demand if needed. */
  const startTaskObjective = async (explicitObjective?: string, explicitGroups?: string[]) => {
    const objective = (explicitObjective ?? draft).trim();
    if (objective.length < 4 || busy) return;
    try {
      if (!sessionRef.current) {
        const created = await api<{ session: CopilotSession }>('/support/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            page_id: active.pageId,
            project_id: active.projectId || null,
            job_id: active.jobId || null,
            attempt_id: active.attemptId || null,
            task_id: active.taskId || null,
            error_code: active.errorCode || null,
            question: '',
          }),
        });
        applySession(created.session);
        window.localStorage.setItem(sessionKey(active.projectId, fault), created.session.id);
      }
      setTaskGroups(explicitGroups && explicitGroups.length ? explicitGroups : null);
      setTaskObjective(objective);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法创建会话。');
    }
  };

  const previewAction = async (action: RecommendedAction) => {
    setBusy(true);
    setError('');
    setReport(null);
    setLastAction(action);
    try {
      const payload = actionPayload(action, capabilities, active.projectId);
      const result = await api<{ action: ActionState; reused: boolean }>('/support/actions/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: action.kind,
          params: payload.params,
          session_id: session?.id || null,
          project_id: payload.scopeProjectId,
        }),
      });
      setPreview(result.action);
      setNotice(result.reused ? '这是一次已预览过的相同请求，已复用原授权单。' : '请先确认变化内容，再决定是否执行。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法生成修复预览。');
    } finally {
      setBusy(false);
    }
  };

  const approveAction = async () => {
    if (!preview || approving) return;
    setApproving(true);
    setError('');
    try {
      const result = await api<{ action: ActionState }>(`/support/actions/${preview.id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preview_hash: preview.preview_hash, confirm: true }),
      });
      setPreview(result.action);
      const settled = TERMINAL_ACTION_STATUSES.has(result.action.status);
      setNotice(
        result.action.status === 'DELEGATED'
          ? `已交给原有执行器（任务 ${result.action.original_task_id || '进行中'}）。完成情况以原环境裁决为准。`
          : settled
            ? `${ACTION_STATUS_LABELS[result.action.status] || result.action.status}${
              result.action.last_error ? `：${result.action.last_error}` : ''
            }`
            : '授权动作已交给原有执行器；结果仍需以权威裁决复核。',
      );
      if (sessionRef.current) {
        try {
          const refreshed = await api<CopilotSession>(`/support/sessions/${sessionRef.current.id}`);
          applySession(refreshed);
        } catch {
          // Receipt reload is best-effort for live backend sessions.
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '授权执行没有完成。');
    } finally {
      setApproving(false);
    }
  };

  const refreshAction = async () => {
    if (!preview) return;
    try {
      const result = await api<{ action: ActionState }>(`/support/actions/${preview.id}`);
      setPreview(result.action);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取执行进度。');
    }
  };

  const makeReport = async (diagnosis: Diagnosis) => {
    if (reporting) return;
    setReporting(true);
    setError('');
    try {
      const result = await api<{ report: ReportMeta; deduplicated: boolean; notice: string }>('/support/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: session?.id || null,
          kind: diagnosis.report_kind || 'BLOCKER',
          title: `${diagnosis.category_label}：${diagnosis.symptom}`.slice(0, 160),
        }),
      });
      setReport(result.report);
      setNotice(result.deduplicated ? '24 小时内已有同一问题的报告，已复用。' : result.notice);
    } catch (err) {
      setError(err instanceof Error ? err.message : '报告没有生成。');
    } finally {
      setReporting(false);
    }
  };

  const downloadReport = async () => {
    if (!report) return;
    try {
      const response = await fetch(`${API}/support/reports/${report.id}/download`);
      if (!response.ok) throw new Error('下载失败');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = report.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('报告下载失败；可在报告目录中查看。');
    }
  };

  const toggleWeb = async () => {
    if (togglingWeb) return;
    setTogglingWeb(true);
    try {
      const result = await api<{ web_search: boolean }>('/support/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ web_search: !webEnabled }),
      });
      setWebEnabled(result.web_search);
      setNotice(result.web_search ? '联网检索已开启：只查官方文档和上游仓库，结果仅作参考。' : '联网检索已关闭。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法保存联网检索开关。');
    } finally {
      setTogglingWeb(false);
    }
  };

  const resetSession = () => {
    if (session) window.localStorage.removeItem(sessionKey(projectId, fault));
    applySession(null);
    setPreview(null);
    setReport(null);
    setTaskObjective(null);
    setTaskGroups(null);
    setRestoredGrant(null);
    setError('');
    setNotice('');
    void loadContext();
  };

  const verdict = context?.snapshot?.setup?.verdict || session?.context?.snapshot?.setup?.verdict;
  const messages = session?.messages || [];
  const llmConfigured = (() => {
    const runtimeLlm = context?.snapshot?.runtime?.llm;
    if (runtimeLlm && typeof runtimeLlm.configured === 'boolean') return runtimeLlm.configured;
    const hcNeedsLlm = (healthCheck?.groups || []).some((group) =>
      (group.items || []).some((item) => item.code === 'COPILOT_LLM_REQUIRED'),
    );
    if (hcNeedsLlm) return false;
    return true;
  })();

  const jumpTo = (hashTarget: string | null | undefined) => {
    if (!hashTarget) return;
    window.location.hash = hashTarget;
  };

  const renderHealthGroups = () => {
    if (!healthCheck) return null;
    return (
      <div className="copilot-health-panel" aria-label="项目体检面板">
        <div className="copilot-health-head">
          <span>
            {healthCheck.project_title ? `项目「${healthCheck.project_title}」 · ` : ''}
            阻断 {healthCheck.summary.blocking} · 警告 {healthCheck.summary.warnings}
          </span>
          <button type="button" className="copilot-link" onClick={() => void loadContext()}>
            <RefreshCw size={12} aria-hidden="true" />重新体检
          </button>
        </div>
        {healthCheck.groups.map((group) => (
          <div key={group.key} className="copilot-health-group">
            <div className="copilot-health-group-head">
              <b>{group.label}</b>
              <span className={`copilot-health-badge status-${String(group.status || 'OK').toLowerCase()}`}>
                {group.status === 'BLOCKED' ? '阻断' : group.status === 'WARNING' ? '提示' : '正常'}
              </span>
            </div>
            <ul className="copilot-health-items">
              {group.items.map((item, idx) => (
                <li key={`${group.key}-${item.code}-${idx}`} className="copilot-health-item">
                  <div>
                    <span>{item.label}</span>
                    {item.detail ? <small>{item.detail}</small> : null}
                  </div>
                  {item.jump ? (
                    <button type="button" className="copilot-link" onClick={() => jumpTo(item.jump)}>
                      前往处理
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    );
  };

  const renderDiagnosis = (message: CopilotMessage) => {
    const diagnosis = message.payload?.diagnosis;
    if (!diagnosis) return null;
    const offline = diagnosis.llm && diagnosis.llm.status !== 'OK';
    return (
      <div className="copilot-diagnosis">
        <div className="copilot-diagnosis-head">
          <span className={`copilot-chip severity-${diagnosis.severity.toLowerCase()}`}>
            {diagnosis.category_label}
          </span>
          <span className="copilot-severity">严重度 {severityLabel(diagnosis.severity)}</span>
        </div>
        <p className="copilot-symptom">{diagnosis.symptom}</p>
        <p className="copilot-next"><b>下一步</b>{diagnosis.next_step}</p>
        {diagnosis.policy_refusal && (
          <div className="copilot-refusal" role="alert">
            <ShieldCheck size={14} aria-hidden="true" />
            <span>已拒绝越权操作：{diagnosis.policy_refusal.blocked.join('、')}。不会执行命令或修改源码。</span>
          </div>
        )}
        <button
          type="button"
          className="copilot-link"
          aria-expanded={Boolean(showEvidence[message.id])}
          onClick={() => setShowEvidence((current) => ({ ...current, [message.id]: !current[message.id] }))}
        >
          <ChevronDown size={13} aria-hidden="true" />
          {showEvidence[message.id] ? '收起证据' : `查看证据（${diagnosis.facts.length}）`}
        </button>
        {showEvidence[message.id] && (
          <dl className="copilot-evidence">
            {diagnosis.facts.map((fact) => (
              <div key={`${fact.key}-${fact.source}`}>
                <dt>{fact.key}</dt>
                <dd>{fact.value}<small>{fact.source}</small></dd>
              </div>
            ))}
          </dl>
        )}
        {offline && (
          <p className="copilot-offline">智能解释暂不可用，以上是本机规则诊断；证据与裁决仍然有效。</p>
        )}
        <div className="copilot-actions">
          {diagnosis.recommended_actions.map((action) => (
            <button key={action.kind} type="button" className="copilot-action" onClick={() => void previewAction(action)}>
              <Wrench size={13} aria-hidden="true" />预览{action.label}
            </button>
          ))}
          <button type="button" className="copilot-action ghost" onClick={() => void makeReport(diagnosis)} disabled={reporting}>
            {reporting ? <Loader2 size={13} className="copilot-spin" /> : <FileText size={13} />}
            生成{diagnosis.report_kind === 'BUG' ? ' BUG ' : diagnosis.report_kind === 'REQUIREMENT' ? '需求' : '阻断'}报告
          </button>
        </div>
      </div>
    );
  };

  const renderReadToolsTrace = (message: CopilotMessage) => {
    const readTools = message.payload?.read_tools || [];
    if (!readTools.length) return null;
    return (
      <details className="copilot-trace" aria-label="AI 查证过程">
        <summary>AI 查证过程（{readTools.length} 步只读工具）</summary>
        <ul className="copilot-trace-list">
          {readTools.map((step, index) => {
            const idEntries = step.ids
              ? Object.entries(step.ids).filter(([, vals]) => Array.isArray(vals) && vals.length > 0)
              : [];
            return (
              <li key={`${message.id}-rt-${index}`}>
                <code>{step.tool}</code>
                <span className={step.status === 'OK' ? 'ok' : 'warn'}>{step.status}</span>
                {typeof step.elapsed_ms === 'number' ? <small>{step.elapsed_ms} ms</small> : null}
                {idEntries.length > 0 ? (
                  <small>
                    {idEntries.map(([k, vals]) => `${k}=${vals.slice(0, 3).join(',')}`).join(' ')}
                  </small>
                ) : null}
              </li>
            );
          })}
        </ul>
      </details>
    );
  };

  const renderProposals = (message: CopilotMessage) => {
    const proposals = message.payload?.proposals || [];
    if (!proposals.length) return null;
    return (
      <div className="copilot-proposals" aria-label="AI 操作建议卡片">
        {proposals.map((card, index) => {
          if (card.type === 'project_grant') {
            return (
              <div key={`${message.id}-pg-${index}`} className="copilot-proposal-card">
                <div className="copilot-proposal-head">
                  <b>项目自主执行授权建议</b>
                  <span className="copilot-chip">多步授权</span>
                </div>
                <p>{card.objective}</p>
                {card.reason ? <small>{card.reason}</small> : null}
                {!card.valid && card.error ? (
                  <small className="danger">校验未通过：{card.error}</small>
                ) : null}
                <div className="copilot-actions">
                  <button
                    type="button"
                    className="copilot-action"
                    disabled={!card.valid || busy}
                    onClick={() => void startTaskObjective(card.objective, card.capability_groups)}
                  >
                    <ShieldCheck size={13} aria-hidden="true" />开启本项目自主执行授权
                  </button>
                </div>
              </div>
            );
          }
          return (
            <div key={`${message.id}-act-${index}`} className="copilot-proposal-card">
              <div className="copilot-proposal-head">
                <b>{card.label || card.action_id}</b>
                <span className="copilot-chip">
                  {card.gpu ? '占用 GPU' : '无 GPU'} · 单步操作
                </span>
              </div>
              {card.summary ? <p>{card.summary}</p> : null}
              {card.reason ? <small>{card.reason}</small> : null}
              {card.params && Object.keys(card.params).length > 0 ? (
                <pre className="copilot-proposal-params">{JSON.stringify(card.params, null, 2)}</pre>
              ) : null}
              {!card.valid && card.error ? (
                <small className="danger">参数校验未通过：{card.error}</small>
              ) : null}
              <div className="copilot-actions">
                <button
                  type="button"
                  className="copilot-action"
                  disabled={!card.valid || busy}
                  onClick={() => void previewAction({
                    kind: card.action_id,
                    label: card.label || card.action_id,
                    reason: card.reason || card.summary || 'AI 建议的单步操作；仍需预览并确认。',
                    requires_authorization: true,
                    params: card.params || {},
                  })}
                >
                  <Wrench size={13} aria-hidden="true" />预览并确认执行
                </button>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  const renderKnowledge = (message: CopilotMessage) => {
    const knowledge = message.payload?.project_knowledge;
    if (!knowledge || !knowledge.citations?.length) return null;
    return (
      <div className="copilot-knowledge" aria-label="项目百科依据">
        {knowledge.citations.map((row) => (
          <div key={row.title} className="copilot-knowledge-row">
            <span className="copilot-chip">{row.status}</span>
            <b>{row.title}</b>
            <span>{row.summary}</span>
            <small>{row.sources?.[0]}{row.verification ? ` · ${row.verification}` : ''}</small>
          </div>
        ))}
        {knowledge.metadata?.state !== 'FRESH' && (
          <p className="copilot-offline">{knowledge.metadata?.notice}</p>
        )}
      </div>
    );
  };

  const nickname = profile.assistant_nickname || 'AI 管家';
  return (
    <>
      <button
        type="button"
        className={`copilot-fab ${open ? 'open' : ''}`}
        aria-label={open ? `收起 ${nickname}` : `打开 ${nickname}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <X size={18} /> : <Sparkles size={18} />}
        <span>{nickname}</span>
        {!open && needsSetup && <i className="copilot-dot" aria-hidden="true" />}
      </button>

      {open && (
        <section
          className="copilot-panel"
          role="dialog"
          aria-modal="false"
          aria-label={`Shine ${nickname}`}
          style={{ width: `${panelSize.width}px`, height: `${panelSize.height}px` }}
        >
          <div
            className="copilot-resize-edge copilot-resize-w"
            data-testid="copilot-resize-w"
            aria-hidden="true"
            onMouseDown={startResize('w')}
          />
          <div
            className="copilot-resize-edge copilot-resize-n"
            data-testid="copilot-resize-n"
            aria-hidden="true"
            onMouseDown={startResize('n')}
          />
          <div
            className="copilot-resize-corner"
            data-testid="copilot-resize-nw"
            title="拖拽调节大小，双击恢复默认尺寸"
            aria-label="拖拽调节浮窗大小"
            onMouseDown={startResize('nw')}
            onDoubleClick={resetPanelSize}
          />
          <header className="copilot-head">
            <div>
              <b><Sparkles size={14} aria-hidden="true" /> SHINE {nickname}</b>
              <span>
                {pageLabel} · {llmConfigured ? '只读查证与受控操作' : '项目体检模式'}
                {llmConfigured && context?.snapshot?.runtime?.llm?.configured
                  ? ` · 已接入 ${context.snapshot.runtime.llm.provider || context.snapshot.runtime.llm.model}`
                  : ''}
                {faultLabel(fault) ? ` · 已绑定 ${faultLabel(fault)}` : ''}
              </span>
            </div>
            <div className="copilot-head-tools">
              <button
                type="button"
                className={`copilot-icon ${webEnabled ? 'active' : ''}`}
                aria-pressed={webEnabled}
                aria-label={webEnabled ? '关闭联网检索' : '开启联网检索（仅官方文档）'}
                title={webEnabled ? '联网检索已开启' : '联网检索已关闭'}
                onClick={() => void toggleWeb()}
                disabled={togglingWeb}
              >
                <Globe size={14} />
              </button>
              <button type="button" className="copilot-icon" aria-label="新会话" title="新会话" onClick={resetSession}>
                <RotateCcw size={14} />
              </button>
              <button type="button" className="copilot-icon" aria-label="关闭 AI 管家" onClick={() => setOpen(false)}>
                <X size={15} />
              </button>
            </div>
          </header>

          <div className="copilot-verdict" aria-live="polite">
            <span className={`copilot-mode mode-${(verdict?.mode || 'unknown').toLowerCase()}`}>
              {verdict?.label || verdict?.mode || '正在读取本机状态'}
            </span>
            <span className="copilot-code">{verdict?.code || '无错误码'}</span>
            {llmConfigured && healthCheck && (
              <button
                type="button"
                className={`copilot-health-badge status-${String(healthCheck.summary.status || 'OK').toLowerCase()}`}
                aria-expanded={healthExpanded}
                onClick={() => setHealthExpanded((value) => !value)}
              >
                {healthCheck.summary.blocking > 0
                  ? `体检阻断 ${healthCheck.summary.blocking}`
                  : healthCheck.summary.warnings > 0
                    ? `体检提示 ${healthCheck.summary.warnings}`
                    : '项目体检健康'}
              </button>
            )}
            <button type="button" className="copilot-icon small" aria-label="刷新本机状态" onClick={() => void loadContext()}>
              <RefreshCw size={12} />
            </button>
          </div>

          <div className="copilot-scroll" ref={scrollRef}>
            {buildInfo && (
              <p className="copilot-build" role="status">
                <ShieldCheck size={13} aria-hidden="true" />
                {buildInfo.user_notice || buildInfo.notice || '无法确认 AI 管家的知识版本。'}
              </p>
            )}
            {!llmConfigured && (
              <>
                <div className="copilot-health-banner" role="status">
                  <span>配置文本模型后开启 AI 管家对话与自主项目操作</span>
                  <button type="button" className="copilot-link" onClick={() => jumpTo('#d/advanced')}>
                    配置文本模型
                  </button>
                </div>
                {renderHealthGroups()}
              </>
            )}
            {llmConfigured && healthExpanded && renderHealthGroups()}
            {llmConfigured && !session && !busy && (
              <div className="copilot-intro">
                <p>我能读懂这台电脑上的真实环境与项目记录，帮你判断下一步。</p>
                <ul>
                  <li>有查证权：自动调用只读工具核对镜头、素材、工作流与裁决。</li>
                  <li>有受控执行权：单步操作卡片或项目授权确认后再执行。</li>
                  <li><b>没有代码修改权</b>：不改源码、不跑命令、不建 Git 补丁。</li>
                </ul>
              </div>
            )}

            {llmConfigured && messages.map((message) => (
              message.role === 'user' ? (
                <div key={message.id} className="copilot-user">{message.content}</div>
              ) : message.kind === 'action_receipt' ? (
                <article
                  key={message.id}
                  className={`copilot-receipt ${message.payload?.status === 'SUCCEEDED' ? 'ok' : 'failed'}`}
                >
                  <b>执行回执 · {ACTION_STATUS_LABELS[String(message.payload?.status || '')] || String(message.payload?.status || '')}</b>
                  <pre>{message.content}</pre>
                  {message.payload?.verification?.authority ? (
                    <small>权威核对：{message.payload.verification.authority}</small>
                  ) : null}
                </article>
              ) : (
                <article key={message.id} className="copilot-answer">
                  <pre>{message.content}</pre>
                  {renderReadToolsTrace(message)}
                  {renderKnowledge(message)}
                  {renderProposals(message)}
                  {renderDiagnosis(message)}
                  {(message.payload?.tool_trace || []).length > 0 && !(message.payload?.read_tools || []).length && (
                    <div className="copilot-tools">
                      {(message.payload?.tool_trace || []).map((tool) => (
                        <span key={tool.tool} className={tool.status === 'OK' ? 'ok' : 'warn'}>
                          {tool.tool === 'read_setup_verdict' ? '唯一裁决'
                            : tool.tool === 'read_comfy_runtime' ? '运行合同'
                              : tool.tool === 'read_task_failure' ? '任务失败事实'
                                : tool.tool === 'read_quality_gates' ? '质检门'
                                  : tool.tool === 'read_workflow_contract' ? '工作流合同'
                                    : tool.tool === 'read_installation_receipts' ? '安装回执'
                                      : tool.tool === 'read_gpu_occupancy' ? 'GPU 占用'
                                        : tool.tool === 'read_resource_inventory' ? '资源清单'
                                          : tool.tool === 'search_local_knowledge' ? '本地知识库'
                                            : tool.tool === 'search_project_knowledge' ? '项目百科'
                                              : tool.tool === 'search_official_docs' ? '官方摘要'
                                                : tool.tool === 'read_project_journey' ? '项目旅程'
                                                  : tool.tool === 'search_official_web' ? '官方参考'
                                                    : '本页面事实'}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              )
            ))}

            {busy && (
              <div className="copilot-loading" role="status">
                <Loader2 size={15} className="copilot-spin" />正在读取本机真实状态…
              </div>
            )}

            {preview && (
              <div className="copilot-preview" role="group" aria-label="配置修复预览">
                <b>{preview.label}</b>
                <p>{preview.preview.summary}</p>
                <ul>
                  {typeof preview.preview.download_bytes === 'number' && (
                    <li>需要下载：{formatBytes(preview.preview.download_bytes)}</li>
                  )}
                  {preview.preview.will_start_gpu && <li>会短暂启动本机服务做运行核验</li>}
                  {typeof preview.preview.will_modify_user_comfyui === 'boolean' && (
                    <li>修改你的 ComfyUI：{preview.preview.will_modify_user_comfyui ? '会' : '不会'}</li>
                  )}
                  {preview.preview.processes && <li>涉及受管服务：{preview.preview.processes.length} 个</li>}
                  {preview.preview.will_create_project && <li>将创建一个空项目；不会覆盖现有项目</li>}
                  {preview.preview.document && (
                    <li>导入原文：{preview.preview.document.filename || '文本'}（{preview.preview.document.text_length || 0} 字）</li>
                  )}
                  {preview.preview.episodes && (
                    <li>涉及集数：{preview.preview.episodes.length}（{preview.preview.episodes.map((row) => row.state_label || row.state).join('、')}）</li>
                  )}
                  {preview.preview.parameter_diff && preview.preview.parameter_diff.length > 0 && (
                    <li>
                      参数变化：{preview.preview.parameter_diff.map((row) => `${row.key} ${String(row.before)} → ${String(row.after)}`).join('；')}
                    </li>
                  )}
                  {preview.preview.blockers && preview.preview.blockers.length > 0 && (
                    <li className="danger">原业务门禁未满足：{preview.preview.blockers.join('、')}</li>
                  )}
                  {preview.preview.blocked_episodes && preview.preview.blocked_episodes.length > 0 && (
                    <li className="danger">
                      这些集还不能开拍：{preview.preview.blocked_episodes.map((row) => row.title || row.episode_id).join('、')}
                    </li>
                  )}
                  {preview.preview.blocked && (
                    <li className="danger">当前有生产任务或 GPU 占用，执行会被拒绝。</li>
                  )}
                  {preview.preview.llm_role && !preview.preview.llm_role.configured && (
                    <li className="danger">「故事大纲」角色还没有配置；这一步会被原门禁拒绝。</li>
                  )}
                </ul>
                <p className="copilot-authorization">{preview.preview.authorization_summary}</p>
                <div className="copilot-actions">
                  <button type="button" className="copilot-action"
                    disabled={approving || preview.preview.blocked || TERMINAL_ACTION_STATUSES.has(preview.status)}
                    onClick={() => void approveAction()}>
                    {approving ? <Loader2 size={13} className="copilot-spin" /> : <CheckCircle2 size={13} />}
                    我确认执行
                  </button>
                  <button type="button" className="copilot-action ghost" onClick={() => setPreview(null)}>取消</button>
                  {preview.status !== 'PREVIEWED' && (
                    <button type="button" className="copilot-action ghost" onClick={() => void refreshAction()}>
                      <RefreshCw size={13} />刷新进度
                    </button>
                  )}
                  {TERMINAL_ACTION_STATUSES.has(preview.status) && lastAction && (
                    <button type="button" className="copilot-action ghost" onClick={() => void previewAction(lastAction)}>
                      <RefreshCw size={13} />重新预览
                    </button>
                  )}
                </div>
                {preview.status !== 'PREVIEWED' && (
                  <p className="copilot-authorization">
                    状态：{ACTION_STATUS_LABELS[preview.status] || preview.status}
                    {preview.original_task_id ? ` · 原执行器任务 ${preview.original_task_id}` : ''}
                    {preview.verification?.outcome ? ` · 复核 ${preview.verification.outcome}` : ''}
                    {preview.verification?.authority ? `（${preview.verification.authority}）` : ''}
                  </p>
                )}
                {preview.last_error && (
                  <p className="copilot-authorization" role="status">{preview.last_error}</p>
                )}
              </div>
            )}

            {report && (
              <div className="copilot-report" role="status">
                <FileText size={14} aria-hidden="true" />
                <div>
                  <b>报告已生成</b>
                  <span>{report.display_path}</span>
                </div>
                <button type="button" className="copilot-icon small" aria-label="下载报告" onClick={() => void downloadReport()}>
                  <Download size={13} />
                </button>
              </div>
            )}

            {error && (
              <div className="copilot-error" role="alert">
                <AlertTriangle size={14} aria-hidden="true" />{error}
              </div>
            )}
            {notice && <p className="copilot-notice" aria-live="polite">{notice}</p>}

            {taskObjective && session ? (
              <TaskGrantCard
                sessionId={session.id}
                projectId={projectId || null}
                objective={taskObjective}
                initialCapabilityGroups={taskGroups}
                initialGrant={restoredGrant}
                onClose={() => { setTaskObjective(null); setTaskGroups(null); }}
              />
            ) : null}
          </div>

          {llmConfigured && (
            <div className="copilot-compose">
              {!session && suggestions.length > 0 && (
                <div className="copilot-suggestions">
                  {suggestions.slice(0, 3).map((question) => (
                    <button key={question} type="button" onClick={() => void ask(question)} disabled={busy}>
                      {question}
                    </button>
                  ))}
                </div>
              )}
              {draft.trim().length >= 4 && !taskObjective && (
                <div className="copilot-suggestions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void startTaskObjective()}
                    title="AI 先展示一次授权范围，你点一次授权后它自动完成后续步骤"
                  >
                    一次授权，让 AI 自动完成这件事
                  </button>
                </div>
              )}
              <div className="copilot-input-row">
                <textarea
                  ref={inputRef}
                  value={draft}
                  maxLength={MAX_INPUT}
                  rows={2}
                  placeholder="向我提问项目问题，或让我核对镜头与素材…"
                  aria-label="向 AI 管家提问"
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      void ask(draft);
                    }
                  }}
                />
                <button type="button" className="copilot-send" aria-label="发送" disabled={busy || !draft.trim()}
                  onClick={() => void ask(draft)}>
                  {busy ? <Loader2 size={15} className="copilot-spin" /> : <Send size={15} />}
                </button>
              </div>
              <p className="copilot-foot"><ShieldCheck size={11} aria-hidden="true" />只读查证 · 改动需确认 · 绝不修改源码</p>
            </div>
          )}
        </section>
      )}
    </>
  );
}

export default ShineCopilot;
