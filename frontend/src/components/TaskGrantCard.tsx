/** One task-level authorization card for the AI copilot (V3.1).
 *
 *  A person states an objective, sees the exact boundary once, clicks once, and
 *  the copilot keeps working inside it.  Everything engineering-shaped - the
 *  grant digest, the sub-actions, the original task ids - stays behind one
 *  "技术细节" disclosure so the main surface stays readable.
 *
 *  The card never claims a business outcome itself: the status it shows is the
 *  server's, and a missing receipt is shown as a blocker rather than a success.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, Loader2, ShieldCheck, Square } from 'lucide-react';
import { API, api } from '../api';

export type GrantLimits = Record<string, number>;
export type BudgetPreset = 'standard' | 'full_production';

type MaterialReview = {
  digest: string;
  generating: boolean;
  candidates: Array<{
    candidate_id: string;
    family: string;
    name: string;
    generation_revision: number;
  }>;
};

export type GrantPreview = {
  preview_id: string;
  schema: string;
  grant_digest: string;
  purpose: string;
  can_authorize: boolean;
  summary: string;
  capability_groups: string[];
  capabilities: string[];
  blocked_capabilities: Array<{ capability_id: string; state: string; notice?: string }>;
  budget_preset?: BudgetPreset;
  limits: GrantLimits;
  pause_before_production?: boolean;
  text_consent?: {
    document_name: string;
    document_sha256: string;
    roles: Array<{ provider: string; model: string }>;
  } | null;
  risks: string[];
  selection_required?: string[];
  installation?: {
    selected?: Record<string, unknown> | null;
    choice_required?: boolean;
    candidates?: Array<Record<string, unknown>>;
    error?: string;
  };
  model?: {
    inherited?: boolean;
    source_role?: string;
    profile_name?: string;
    model?: string;
    error?: string;
  };
  expires_at: string;
  preview_expires_at: string;
  not_allowed: string[];
};

export type GrantSubAction = {
  action_id?: string | null;
  tool_call_id?: string | null;
  capability_id?: string | null;
  original_task_id?: string | null;
};

export type GrantStep = {
  capability_id?: string | null;
  status?: string | null;
  outcome?: string | null;
  error_code?: string | null;
  message?: string | null;
  action_id?: string | null;
  original_task_id?: string | null;
  satisfied?: boolean;
};

export type GrantExecution = {
  status?: string;
  stop_reason?: string;
  final?: string;
  steps?: GrantStep[];
  llm?: Record<string, unknown>;
  usage?: Record<string, unknown>;
};

export type TaskGrant = {
  schema?: string;
  grant_id: string;
  objective: string;
  status: string;
  status_label?: string;
  scope?: Record<string, unknown>;
  limits?: GrantLimits;
  remaining?: GrantLimits;
  validity?: { expires_at?: string };
  grant_digest?: string;
  paused_reason?: string | null;
  budget_preset?: BudgetPreset;
  pause_before_production?: boolean;
  material_review?: MaterialReview | null;
  steps?: GrantStep[];
  sub_actions?: GrantSubAction[];
  recent_tool_calls?: Array<Record<string, unknown>>;
};

const GROUP_LABELS: Record<string, string> = {
  inspect: '只读检查本机已有资源',
  literary: '文戏与分镜操作',
  materials: '素材准备与换图',
  production: '导演包、制作控制与交付',
  resources: '下载缺失的模型权重',
  nodes: '安装已审查、固定版本的节点包',
  workflows: '创建并验证用户级工作流',
  runtime: '配置受管运行环境',
  test: '在预算内跑必要的小样测试',
};

const RISK_LABELS: Record<string, string> = {
  DISK_WRITE: '会写入磁盘',
  LARGE_DOWNLOAD: '会下载较大的模型文件',
  THIRD_PARTY_CODE: '会引入第三方节点代码（仅限已审查的固定版本）',
  USER_DATA_WRITE: '会写入你的用户级工作流配置',
  GPU_TEST: '会短时占用 GPU',
  MANAGED_PROCESS_CONTROL: '会启停本工作台拥有的渲染服务',
};

const LIMIT_LABELS: Array<[string, string, (value: number) => string]> = [
  ['max_download_bytes', '最大下载体积', formatBytes],
  ['max_disk_write_bytes', '最大磁盘写入', formatBytes],
  ['max_installed_node_packages', '最多安装节点包', (v) => `${v} 个`],
  ['max_new_workflow_profiles', '最多新建工作流', (v) => `${v} 个`],
  ['max_test_inference_jobs', '最多小样任务', (v) => `${v} 个`],
  ['max_gpu_minutes', '最长 GPU 时间', (v) => `${v} 分钟`],
  ['max_llm_tool_calls', '最多工具调用', (v) => `${v} 次`],
  ['max_total_runtime_seconds', '总运行时限', (v) => `${Math.round(v / 36) / 100} 小时`],
];

const BUDGET_LABELS: Record<BudgetPreset, string> = {
  standard: '标准',
  full_production: '全链路拍片',
};

const MATERIAL_FAMILIES: Record<string, string> = { character: '角色', scene: '场景', prop: '道具' };

function formatExpiry(value: string | undefined): string {
  if (!value) return '—';
  const time = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN', { hour12: false });
}

export function formatBytes(value: number | undefined): string {
  if (!value || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const rounded = Math.round(size * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} ${units[index]}`;
}

/** The six user-facing stages of one authorized task (spec §11). */
export function stageOf(input: {
  previewing?: boolean;
  authorized?: boolean;
  status?: string;
  steps?: GrantStep[];
}): { key: string; label: string; tone: 'idle' | 'busy' | 'good' | 'warn' | 'bad' } {
  if (input.previewing) return { key: 'UNDERSTANDING', label: '正在理解目标', tone: 'busy' };
  const status = String(input.status || '');
  if (!input.authorized) return { key: 'AWAITING_GRANT', label: '等待一次授权', tone: 'idle' };
  if (status === 'AUTHORIZED') return { key: 'INVESTIGATING', label: '正在调查环境', tone: 'busy' };
  if (status === 'RUNNING' || status === 'WAITING_EXECUTOR' || status === 'VERIFYING') {
    return { key: 'RUNNING', label: '自动执行中', tone: 'busy' };
  }
  if (status === 'SUCCEEDED') {
    const satisfied = (input.steps || []).some((step) => step.satisfied);
    return satisfied
      ? { key: 'DONE', label: '已成功', tone: 'good' }
      : { key: 'REVIEW', label: '正在复核', tone: 'busy' };
  }
  if (status.startsWith('PAUSED')) return { key: 'BLOCKED', label: '存在阻滞', tone: 'warn' };
  if (status === 'FAILED') return { key: 'FAILED', label: '存在阻滞', tone: 'bad' };
  if (status === 'REVOKED' || status === 'CANCELLED') {
    return { key: 'STOPPED', label: '已停止后续 AI 操作', tone: 'warn' };
  }
  if (status === 'EXPIRED') return { key: 'EXPIRED', label: '授权已过期', tone: 'warn' };
  return { key: 'RUNNING', label: '自动执行中', tone: 'busy' };
}

type Props = {
  sessionId: string;
  projectId?: string | null;
  objective: string;
  /** Optional explicit groups proposed by AI (or narrowed by caller). */
  initialCapabilityGroups?: string[] | null;
  initialBudgetPreset?: BudgetPreset;
  initialPauseBeforeProduction?: boolean;
  scriptText?: string;
  /** A grant already authorized for this session: the card reconnects to it
   *  (same task, same budget) instead of previewing a second authorization. */
  initialGrant?: TaskGrant | null;
  onClose?: () => void;
  onFinished?: (grant: TaskGrant | null) => void;
};

const ACTIVE_STATUSES = new Set(['AUTHORIZED', 'RUNNING', 'WAITING_EXECUTOR', 'VERIFYING']);
/** A pause that asked for the user's decision is resumable by an explicit click. */
const RESUMABLE_STATUSES = new Set([...ACTIVE_STATUSES, 'PAUSED_USER_DECISION']);

export function TaskGrantCard({
  sessionId, projectId, objective, initialCapabilityGroups, initialGrant,
  initialBudgetPreset = 'standard', initialPauseBeforeProduction = true, scriptText, onClose, onFinished,
}: Props) {
  const [preview, setPreview] = useState<GrantPreview | null>(null);
  const [grant, setGrant] = useState<TaskGrant | null>(initialGrant || null);
  const [execution, setExecution] = useState<GrantExecution | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [selectedInstallation, setSelectedInstallation] = useState<string | null>(null);
  const [allowGpu, setAllowGpu] = useState(true);
  const [allowDownload, setAllowDownload] = useState(true);
  const [budgetPreset, setBudgetPreset] = useState(initialBudgetPreset);
  const [pauseBeforeProduction, setPauseBeforeProduction] = useState(initialPauseBeforeProduction);
  const [allowRemoteText, setAllowRemoteText] = useState(false);

  const requestedGroups = useMemo(
    () => (
      initialCapabilityGroups && initialCapabilityGroups.length > 0
        ? initialCapabilityGroups
        : projectId
          ? ['inspect', 'literary', 'materials', 'production', 'resources', 'nodes', 'workflows', 'runtime', 'test']
          : ['inspect', 'resources', 'nodes', 'workflows', 'runtime', 'test']
    ),
    [initialCapabilityGroups, projectId],
  );

  const loadPreview = useCallback(async () => {
    if (initialGrant) return;  // reconnecting, never a second authorization
    setPreviewing(true);
    setError(null);
    try {
      const body = await api<GrantPreview>('/support/task-grants/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          objective,
          project_id: projectId || null,
          installation_id: selectedInstallation || undefined,
          capability_groups: requestedGroups,
          budget_preset: budgetPreset,
          pause_before_production: pauseBeforeProduction,
          script_text: scriptText,
          allow_remote_text: allowRemoteText,
          allow_gpu: allowGpu,
          allow_download: allowDownload,
        }),
      });
      setPreview(body);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '无法生成授权预览');
    } finally {
      setPreviewing(false);
    }
  }, [sessionId, projectId, objective, selectedInstallation, initialGrant, requestedGroups,
    allowGpu, allowDownload, budgetPreset, pauseBeforeProduction, scriptText, allowRemoteText]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  // A live grant is polled from the SAME row: closing the panel or restarting
  // the API never forks the task, and progress made by the Worker is visible.
  const grantId = grant?.grant_id;
  const grantStatus = grant?.status;
  const materialReview = grant?.material_review;
  const awaitingMaterialReview = grant?.paused_reason === 'MATERIAL_REVIEW_REQUIRED';
  useEffect(() => {
    if (!grantId || !grantStatus || (!ACTIVE_STATUSES.has(grantStatus) && !awaitingMaterialReview)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const detail = await api<TaskGrant>(`/support/task-grants/${grantId}`);
        setGrant(detail);
        onFinished?.(detail);
      } catch {
        // A transient read failure keeps the last real projection on screen.
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [grantId, grantStatus, awaitingMaterialReview, onFinished]);

  const continueRun = useCallback(async () => {
    if (!grant) return;
    setContinuing(true);
    setError(null);
    try {
      const body = await api<GrantExecution>(`/support/task-grants/${grant.grant_id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          max_steps: 12,
          ...(materialReview ? { material_review_digest: materialReview.digest, run_in_background: true } : {}),
        }),
      });
      setExecution(materialReview ? null : body);
      const detail = await api<TaskGrant>(`/support/task-grants/${grant.grant_id}`);
      setGrant(detail);
      onFinished?.(detail);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '无法继续推进');
    } finally {
      setContinuing(false);
    }
  }, [grant, sessionId, onFinished, materialReview]);

  const authorize = useCallback(async () => {
    if (!preview) return;
    setAuthorizing(true);
    setError(null);
    try {
      // The resident Worker picks up the durable grant as soon as it exists.
      const created = await api<{ grant_id: string }>('/support/task-grants/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          preview_id: preview.preview_id,
          grant_digest: preview.grant_digest,
          confirm: true,
          run: false,
        }),
      });
      const detail = await api<TaskGrant>(`/support/task-grants/${created.grant_id}`);
      setGrant(detail);
      onFinished?.(detail);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '授权未完成');
    } finally {
      setAuthorizing(false);
    }
  }, [preview, sessionId, onFinished]);

  const refresh = useCallback(async () => {
    if (!grant) return;
    try {
      const detail = await api<TaskGrant>(`/support/task-grants/${grant.grant_id}`);
      setGrant(detail);
      onFinished?.(detail);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '无法读取任务状态');
    }
  }, [grant, onFinished]);

  const stop = useCallback(async () => {
    if (!grant) return;
    try {
      await api(`/support/task-grants/${grant.grant_id}/revoke`, { method: 'POST' });
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '无法停止后续操作');
    }
  }, [grant, refresh]);

  const steps = grant?.steps || execution?.steps;
  const stage = useMemo(
    () =>
      stageOf({
        previewing,
        authorized: Boolean(grant),
        status: grant?.status,
        steps,
      }),
    [previewing, grant, steps],
  );

  const limits = preview?.limits || grant?.limits || {};
  const limitRows = LIMIT_LABELS.filter(([key]) => typeof limits[key] === 'number');

  return (
    <section className="copilot-grant" data-stage={stage.key} data-grant-status={grantStatus} aria-label="本次任务授权">
      <header className="copilot-grant-head">
        <span className={`copilot-grant-stage tone-${stage.tone}`}>
          {stage.tone === 'busy' ? <Loader2 size={14} className="spin" aria-hidden /> : null}
          {stage.tone === 'good' ? <CheckCircle2 size={14} aria-hidden /> : null}
          {stage.tone === 'warn' || stage.tone === 'bad' ? <AlertTriangle size={14} aria-hidden /> : null}
          {stage.label}
        </span>
        {onClose ? (
          <button type="button" className="link" onClick={onClose}>
            收起
          </button>
        ) : null}
      </header>

      <p className="copilot-grant-objective">{preview?.purpose || grant?.objective || objective}</p>

      {error ? (
        <p className="copilot-grant-error" role="alert">
          {error}
        </p>
      ) : null}

      {!grant && preview ? (
        <div className="copilot-grant-body">
          <label className="copilot-grant-budget">
            <span>预算档位</span>
            <select value={budgetPreset} disabled={previewing || authorizing}
              onChange={(event) => setBudgetPreset(event.target.value as BudgetPreset)}>
              {Object.entries(BUDGET_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          {limitRows.length ? (
            <ul className="copilot-grant-primary-limits">
              <li>授权有效期至 {formatExpiry(preview.expires_at)}</li>
              {limitRows.filter(([key]) => key === 'max_gpu_minutes' || key === 'max_llm_tool_calls').map(([key, label, format]) => (
                <li key={key}>{label}：{format(Number(limits[key]))}</li>
              ))}
              {limits.max_gpu_minutes > 0 ? <li>GPU 按排队任务的计划用量估算</li> : null}
            </ul>
          ) : null}
          <h4>安全开关</h4>
          <div className="copilot-grant-toggles">
            <label>
              <input
                type="checkbox"
                checked={allowGpu}
                disabled={previewing || authorizing}
                onChange={(event) => setAllowGpu(event.target.checked)}
              />
              <span>允许调用本机 GPU 动作（生图 / 试拍 / 重拍）</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={allowDownload}
                disabled={previewing || authorizing}
                onChange={(event) => setAllowDownload(event.target.checked)}
              />
              <span>允许下载模型 / 安装受控节点包</span>
            </label>
            {requestedGroups.includes('production') ? (
              <label>
                <input type="checkbox" checked={pauseBeforeProduction} disabled={previewing || authorizing}
                  onChange={(event) => setPauseBeforeProduction(event.target.checked)} />
                <span>素材审片检查点</span>
              </label>
            ) : null}
          </div>
          {scriptText ? (
            <details>
              <summary>查看本次导入的剧本</summary>
              <pre className="copilot-proposal-params">{scriptText}</pre>
            </details>
          ) : null}
          {preview.text_consent ? (
            <label className="copilot-grant-text-consent">
              <input type="checkbox" checked={allowRemoteText} disabled={previewing || authorizing}
                onChange={(event) => setAllowRemoteText(event.target.checked)} />
              <span>允许将「{preview.text_consent.document_name}」发送给 {Array.from(new Set(
                preview.text_consent.roles.map(({ provider, model }) => `${provider} / ${model}`),
              )).join('、')}，用于剧本和分镜处理</span>
            </label>
          ) : null}
          <h4>授权后 AI 会自动做的事</h4>
          <ul>
            {preview.capability_groups.map((group) => (
              <li key={group}>{GROUP_LABELS[group] || group}</li>
            ))}
          </ul>

          <h4>适用范围</h4>
          <ul>
            <li>{projectId ? '仅限当前项目与本机受管 ComfyUI' : '仅限本机受管环境'}</li>
            {preview.installation?.selected ? (
              <li>
                安装位置：
                {String(
                  (preview.installation.selected as Record<string, unknown>).root ||
                    (preview.installation.selected as Record<string, unknown>).instance_id ||
                    '已识别的本机实例',
                )}
              </li>
            ) : (
              <li>尚未识别到 ComfyUI 实例；整备阶段会先扫描</li>
            )}
          </ul>

          {preview.installation?.choice_required && (preview.installation.candidates?.length || 0) > 0 ? (
            <div className="copilot-grant-choice" role="radiogroup" aria-label="选择本次任务使用的 ComfyUI 安装">
              <h4>发现多套安装，请先选定一套（只选一次，随后冻结进授权）</h4>
              {(preview.installation.candidates || []).map((candidate) => {
                const id = String(candidate.instance_id || '');
                return (
                  <label key={id} className="copilot-grant-choice-row">
                    <input
                      type="radio"
                      name="grant-installation"
                      value={id}
                      checked={selectedInstallation === id}
                      onChange={() => setSelectedInstallation(id)}
                    />
                    <span>{String(candidate.root || id)}</span>
                    <em>{candidate.managed ? '工作台受管' : candidate.adopted ? '已收编（外部只读保护）' : '外部'}</em>
                  </label>
                );
              })}
            </div>
          ) : null}

          {limitRows.length ? (
            <>
              <h4>资源上限</h4>
              <ul>
                {limitRows.filter(([key]) => key !== 'max_gpu_minutes' && key !== 'max_llm_tool_calls').map(([key, label, format]) => (
                  <li key={key}>
                    {label}：{format(Number(limits[key]))}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {preview.risks.length ? (
            <p className="copilot-grant-risks">
              风险提示：{preview.risks.map((risk) => RISK_LABELS[risk] || risk).join('；')}
            </p>
          ) : null}

          <h4>绝不会执行</h4>
          <ul>
            {preview.not_allowed.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>

          <p className="copilot-grant-model">
            {preview.model?.inherited
              ? `使用你已配置的文本模型（继承自「${preview.model.source_role}」），无需重新填 API Key。`
              : preview.model?.error
                ? '尚未配置可用的文本模型；请先在设置里选择一个文本模型。'
                : `使用模型：${preview.model?.model || '已配置模型'}`}
          </p>

          {preview.blocked_capabilities.length ? (
            <p className="copilot-grant-error">
              以下能力当前不可由 AI 执行：
              {preview.blocked_capabilities.map((row) => row.capability_id).join('、')}
            </p>
          ) : null}

          <button
            type="button"
            className="primary copilot-grant-authorize"
            disabled={!preview.can_authorize || previewing || authorizing || Boolean(error)}
            onClick={() => void authorize()}
          >
            {authorizing ? <Loader2 size={16} className="spin" aria-hidden /> : <ShieldCheck size={16} aria-hidden />}
            授权 AI 管家完成本次任务
          </button>
        </div>
      ) : null}

      {grant ? (
        <div className="copilot-grant-body">
          <p className="copilot-grant-status">
            {grant.status_label || grant.status}
            {grant.paused_reason && !awaitingMaterialReview ? `（${grant.paused_reason}）` : ''}
          </p>

          {materialReview ? (
            <div className="copilot-grant-material-review">
              <h4>素材待审片</h4>
              <p>{materialReview.generating ? '新版素材正在生成。' : '确认角色和场景后，继续推进拍片。'}</p>
              <ul>
                {materialReview.candidates.map((item) => (
                  <li key={item.candidate_id}>
                    <a href={`${API}/candidate-assets/${encodeURIComponent(item.candidate_id)}/media`}
                      target="_blank" rel="noreferrer">
                      <img src={`${API}/candidate-assets/${encodeURIComponent(item.candidate_id)}/thumbnail`}
                        alt="" loading="lazy" />
                      <span>{MATERIAL_FAMILIES[item.family] || item.family} · {item.name} · v{item.generation_revision}</span>
                    </a>
                  </li>
                ))}
              </ul>
              <a href="#d/materials">前往素材准备</a>
            </div>
          ) : null}

          {execution?.final ? <p className="copilot-grant-final">{execution.final}</p> : null}

          {steps?.length ? (
            <ol className="copilot-grant-steps">
              {steps.slice(-6).map((step, index) => (
                <li key={`${step.capability_id}-${index}`} data-satisfied={step.satisfied ? 'true' : 'false'}>
                  <span>{step.capability_id}</span>
                  <em>{step.satisfied ? '已取得原任务回执' : step.error_code || step.status}</em>
                </li>
              ))}
            </ol>
          ) : null}

          <div className="copilot-grant-actions">
            {grant && RESUMABLE_STATUSES.has(grant.status) ? (
              <button type="button" className={materialReview ? 'primary' : 'link'}
                disabled={continuing || materialReview?.generating} onClick={() => void continueRun()}>
                {continuing ? <Loader2 size={12} className="spin" aria-hidden /> : null}
                {materialReview ? '继续推进拍片' : '继续推进'}
              </button>
            ) : null}
            <button type="button" className="link" onClick={() => void refresh()}>
              刷新进度
            </button>
            <button type="button" className="link" onClick={() => void stop()}>
              <Square size={12} aria-hidden /> 停止后续 AI 操作
            </button>
            <button type="button" className="link" onClick={() => setShowDetails((value) => !value)}>
              <ChevronDown size={12} aria-hidden /> 技术细节
            </button>
          </div>

          {showDetails ? (
            <div className="copilot-grant-details">
              <dl>
                <dt>授权编号</dt>
                <dd>{grant.grant_id}</dd>
                <dt>授权摘要</dt>
                <dd>{grant.grant_digest}</dd>
                <dt>有效期至</dt>
                <dd>{formatExpiry(grant.validity?.expires_at)}</dd>
              </dl>
              {grant.sub_actions?.length ? (
                <>
                  <h4>已绑定的原任务</h4>
                  <ul>
                    {grant.sub_actions.map((row) => (
                      <li key={String(row.action_id)}>
                        {row.capability_id} · 原任务 {row.original_task_id || '（同步执行）'} · 动作{' '}
                        {row.action_id}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {previewing && !preview ? (
        <p className="copilot-grant-loading">
          <Loader2 size={14} className="spin" aria-hidden /> 正在生成本次授权范围…
        </p>
      ) : null}
    </section>
  );
}
