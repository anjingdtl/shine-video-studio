import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, ChevronDown, Download, FileText, Globe, Loader2,
  RefreshCw, RotateCcw, Send, ShieldCheck, Sparkles, Wrench, X,
} from 'lucide-react';
import { API, api } from '../api';
import { currentCopilotFault, faultKey, faultLabel, subscribeCopilotFault, type CopilotFault } from '../copilotContext';

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

type PlanStep = {
  order: number;
  title: string;
  capability_id: string | null;
  state: 'READY' | 'READ_ONLY' | 'BLOCKED' | 'HUMAN_REQUIRED' | 'NOT_SUPPORTED';
  state_label: string;
  evidence?: string[];
  reason?: string;
  action?: { kind: string; params: Record<string, unknown> } | null;
  readback?: string;
};

type CopilotPlan = {
  intent: string;
  summary: string;
  plan_id?: string;
  plan_hash?: string;
  steps: PlanStep[];
  requires_input: string[];
  notice: string;
  bounded: boolean;
};

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
    llm?: { status: string };
    intent?: string | null;
    plan?: CopilotPlan | null;
    project_knowledge?: KnowledgeAnswer | null;
    plan_error?: string | null;
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
  snapshot: { setup?: { verdict?: { mode?: string; label?: string; code?: string | null } } };
  suggested_questions: string[];
  web_search_enabled: boolean;
};

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
  const [planInputs, setPlanInputs] = useState<Record<string, Record<string, string>>>({});
  const [approvedSteps, setApprovedSteps] = useState<Record<string, number[]>>({});
  const [allowGpu, setAllowGpu] = useState<Record<string, boolean>>({});
  const [delegation, setDelegation] = useState<Record<string, unknown> | null>(null);
  const [delegating, setDelegating] = useState(false);
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
    if (!open) return;
    inputRef.current?.focus();
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
      const data = await api<ContextResponse>(`/support/context?${params.toString()}`);
      setContext(data);
      setSuggestions(data.suggested_questions);
      setWebEnabled(data.web_search_enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取本机环境状态。');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ctx is derived from the same props/fault
  }, [pageId, projectId, fault]);

  const restore = useCallback(async (ctx = active) => {
    const key = sessionKey(ctx.projectId, fault);
    const stored = window.localStorage.getItem(key);
    if (!stored) {
      applySession(null);
      await loadContext(ctx);
      return null;
    }
    try {
      const restored = await api<CopilotSession>(`/support/sessions/${stored}`);
      applySession(restored);
      await loadContext(ctx);
      return restored;
    } catch {
      window.localStorage.removeItem(key);
      applySession(null);
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
      if (restored) return;
      const question = `帮我诊断这个失败${fault.label ? `：${fault.label}` : fault.errorCode ? `：${fault.errorCode}` : ''}`;
      await ask(question);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fault, busy]);

  const ask = async (question: string) => {
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
          body: JSON.stringify({ content }),
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
    setError('');
    setNotice('');
    void loadContext();
  };

  const verdict = context?.snapshot?.setup?.verdict || session?.context?.snapshot?.setup?.verdict;
  const messages = session?.messages || [];

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

  const delegatePlan = async (message: CopilotMessage) => {
    if (!session || delegating) return;
    const plan = message.payload?.plan;
    const steps = plan?.steps || [];
    const autoOrders = steps.filter((step) => step.state === 'READY' && step.action).map((step) => step.order);
    const selected = approvedSteps[message.id] ?? autoOrders;
    if (!selected.length) {
      setError('请先勾选要纳入本次授权的步骤。');
      return;
    }
    setDelegating(true);
    setError('');
    try {
      const result = await api<{ delegation: Record<string, unknown> }>('/support/plans/delegate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: session.id,
          plan_id: plan?.plan_id,
          plan_hash: plan?.plan_hash,
          approved_orders: selected,
          budget: { max_episodes: 1, max_gpu_actions: allowGpu[message.id] ? 1 : 0 },
          confirm: true,
        }),
      });
      setDelegation(result.delegation);
      setNotice('已按你勾选的范围委托；每一步仍由原执行器完成。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '委托没有开始。');
    } finally {
      setDelegating(false);
    }
  };

  const refreshDelegation = async () => {
    if (!session) return;
    try {
      const result = await api<{ delegation: Record<string, unknown> }>(
        `/support/sessions/${session.id}/delegation/refresh`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: session.id }),
        },
      );
      setDelegation(result.delegation);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法读取委托进度。');
    }
  };

  const cancelDelegation = async () => {
    if (!session) return;
    try {
      const result = await api<{ delegation: Record<string, unknown> }>(
        `/support/sessions/${session.id}/delegation/cancel`,
        { method: 'POST' },
      );
      setDelegation(result.delegation);
    } catch (err) {
      setError(err instanceof Error ? err.message : '无法取消委托。');
    }
  };

  const renderPlan = (message: CopilotMessage) => {
    const plan = message.payload?.plan;
    if (!plan || !plan.steps?.length) return null;
    const inputs = planInputs[message.id] || {};
    const autoOrders = plan.steps.filter((step) => step.state === 'READY' && step.action).map((step) => step.order);
    const selected = approvedSteps[message.id] ?? autoOrders;
    const editable = (step: PlanStep) =>
      Boolean(step.action) && step.state === 'HUMAN_REQUIRED'
      && ((step.action!.kind === 'document_import_text')
        || (step.action!.kind === 'project_create' && !step.action!.params?.title));
    const mergedParams = (step: PlanStep) => ({
      ...(step.action?.params || {}),
      ...Object.fromEntries(Object.entries(inputs).filter(([, value]) => value.trim())),
    });
    const readyToPreview = (step: PlanStep) => {
      if (step.action?.kind === 'document_import_text') return Boolean((inputs.text || '').trim());
      if (step.action?.kind === 'project_create') {
        return Boolean((inputs.title || String(step.action.params?.title || '')).trim());
      }
      return true;
    };
    const hasGpu = plan.steps.some((step) => step.action?.kind === 'production_start');
    return (
      <div className="copilot-plan" aria-label="操作计划">
        <div className="copilot-plan-head">
          <b>操作计划</b>
          <span>{plan.summary}</span>
        </div>
        <ol className="copilot-plan-steps">
          {plan.steps.map((step) => (
            <li key={`${message.id}-${step.order}`}>
              <span className={`copilot-chip plan-${step.state.toLowerCase()}`}>{step.state_label}</span>
              <b>{step.title}</b>
              {step.reason && <small>{step.reason}</small>}
              {step.evidence && step.evidence.length > 0 && <em>{step.evidence.slice(0, 2).join(' · ')}</em>}
              {step.action?.kind === 'project_create' && (step.state !== 'READY' || !step.action.params?.title) && (
                <label className="copilot-plan-input">
                  项目名称
                  <input
                    type="text"
                    value={inputs.title ?? ''}
                    placeholder="例如：雾港回声"
                    onChange={(event) => setPlanInputs((prev) => ({
                      ...prev,
                      [message.id]: { ...(prev[message.id] || {}), title: event.target.value },
                    }))}
                  />
                </label>
              )}
              {step.action?.kind === 'document_import_text' && (
                <div className="copilot-plan-input">
                  <label>
                    原文文本
                    <textarea
                      value={inputs.text ?? ''}
                      placeholder="粘贴你自己的原文；模型不会重写或补写正文。"
                      onChange={(event) => setPlanInputs((prev) => ({
                        ...prev,
                        [message.id]: { ...(prev[message.id] || {}), text: event.target.value },
                      }))}
                    />
                  </label>
                  <label>
                    或选择本地文件
                    <input
                      type="file"
                      accept=".txt,.md,.docx"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (!file) return;
                        if (file.size > 20 * 1024 * 1024) {
                          setError('文件超过 20MB，请在导演页使用正式上传。');
                          return;
                        }
                        const reader = new FileReader();
                        reader.onload = () => {
                          setPlanInputs((prev) => ({
                            ...prev,
                            [message.id]: {
                              ...(prev[message.id] || {}),
                              text: String(reader.result || ''),
                              filename: file.name,
                            },
                          }));
                        };
                        reader.readAsText(file);
                      }}
                    />
                  </label>
                </div>
              )}
              {step.action && (step.state === 'READY' || editable(step)) && (
                <div className="copilot-plan-actions">
                  {step.state === 'READY' && (
                    <label>
                      <input
                        type="checkbox"
                        checked={selected.includes(step.order)}
                        onChange={(event) => setApprovedSteps((prev) => ({
                          ...prev,
                          [message.id]: event.target.checked
                            ? [...new Set([...(prev[message.id] ?? autoOrders), step.order])]
                            : (prev[message.id] ?? autoOrders).filter((order) => order !== step.order),
                        }))}
                      />
                      纳入本次授权
                    </label>
                  )}
                  <button
                    type="button"
                    className="copilot-link"
                    disabled={!readyToPreview(step)}
                    onClick={() => void previewAction({
                      kind: step.action!.kind,
                      label: step.title,
                      reason: step.reason || '来自只读计划；仍需预览并确认。',
                      requires_authorization: true,
                      params: mergedParams(step),
                    })}
                  >
                    预览这一步
                  </button>
                </div>
              )}
            </li>
          ))}
        </ol>
        {hasGpu && (
          <label className="copilot-plan-actions">
            <input
              type="checkbox"
              checked={Boolean(allowGpu[message.id])}
              onChange={(event) => setAllowGpu((prev) => ({ ...prev, [message.id]: event.target.checked }))}
            />
            允许本次委托执行会占用 GPU 的步骤（仍受原生产门禁约束）
          </label>
        )}
        <div className="copilot-plan-actions">
          <button type="button" className="copilot-action" disabled={delegating} onClick={() => void delegatePlan(message)}>
            {delegating ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />}
            授权并推进已勾选步骤
          </button>
        </div>
        {delegation && (
          <div className="copilot-delegation" aria-label="委托进度">
            <b>委托状态：{String(delegation.status || '')}</b>
            {Array.isArray(delegation.steps) && (delegation.steps as { title?: string; state?: string; message?: string }[]).map((step, index) => (
              <small key={index}>{step.title || `步骤 ${index + 1}`}：{step.state}{step.message ? `（${step.message}）` : ''}</small>
            ))}
            {delegation.stop_reason ? <small>停止原因：{String(delegation.stop_reason)}</small> : null}
            <div className="copilot-plan-actions">
              <button type="button" className="copilot-link" onClick={() => void refreshDelegation()}>刷新进度</button>
              <button type="button" className="copilot-link" onClick={() => void cancelDelegation()}>取消未提交步骤</button>
            </div>
          </div>
        )}
        <p className="copilot-authorization">{plan.notice}</p>
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

  return (
    <>
      <button
        type="button"
        className={`copilot-fab ${open ? 'open' : ''}`}
        aria-label={open ? '收起 AI 管家' : '打开 AI 管家'}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <X size={18} /> : <Sparkles size={18} />}
        <span>AI 管家</span>
        {!open && needsSetup && <i className="copilot-dot" aria-hidden="true" />}
      </button>

      {open && (
        <section className="copilot-panel" role="dialog" aria-modal="false" aria-label="Shine AI 管家">
          <header className="copilot-head">
            <div>
              <b><Sparkles size={14} aria-hidden="true" /> SHINE AI 管家</b>
              <span>{pageLabel} · 只读诊断{faultLabel(fault) ? ` · 已绑定 ${faultLabel(fault)}` : ''}</span>
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
            {!session && !busy && (
              <div className="copilot-intro">
                <p>我能读懂这台电脑上的真实环境与任务记录，帮你判断下一步。</p>
                <ul>
                  <li>有诊断权：只读取既有的裁决、日志与资源回执。</li>
                  <li>有受控修复权：先预览，再由你明确授权。</li>
                  <li><b>没有代码修改权</b>：不改源码、不跑命令、不建 Git 补丁。</li>
                </ul>
              </div>
            )}

            {messages.map((message) => (
              message.role === 'user' ? (
                <div key={message.id} className="copilot-user">{message.content}</div>
              ) : (
                <article key={message.id} className="copilot-answer">
                  <pre>{message.content}</pre>
                  {renderKnowledge(message)}
                  {renderPlan(message)}
                  {renderDiagnosis(message)}
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
          </div>

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
            <div className="copilot-input-row">
              <textarea
                ref={inputRef}
                value={draft}
                maxLength={MAX_INPUT}
                rows={2}
                placeholder="问我这台电脑出了什么问题…"
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
            <p className="copilot-foot"><ShieldCheck size={11} aria-hidden="true" />只读诊断 · 修复需预览并授权 · 绝不修改源码</p>
          </div>
        </section>
      )}
    </>
  );
}

export default ShineCopilot;
