import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, ChevronDown, Download, FileText, Globe, Loader2,
  RefreshCw, RotateCcw, Send, ShieldCheck, Sparkles, Wrench, X,
} from 'lucide-react';
import { API, api } from '../api';
import { currentCopilotFault, faultKey, faultLabel, subscribeCopilotFault, type CopilotFault } from '../copilotContext';

type Fact = { key: string; value: string; source: string };

type RecommendedAction = {
  kind: 'setup_repair' | 'setup_reconcile' | 'managed_release';
  label: string;
  reason: string;
  requires_authorization: boolean;
  params: Record<string, unknown>;
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
  payload: { diagnosis?: Diagnosis; tool_trace?: { tool: string; status: string }[]; llm?: { status: string } };
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
      const result = await api<{ action: ActionState; reused: boolean }>('/support/actions/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: action.kind,
          params: { ...action.params, project_id: active.projectId || undefined },
          session_id: session?.id || null,
          project_id: active.projectId || null,
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
                  {preview.preview.blocked && (
                    <li className="danger">当前有生产任务或 GPU 占用，执行会被拒绝。</li>
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
