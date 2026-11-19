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
import { api } from '../api';

export type GrantLimits = Record<string, number>;

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
  limits: GrantLimits;
  risks: string[];
  installation?: {
    selected?: Record<string, unknown> | null;
    choice_required?: boolean;
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
  sub_actions?: GrantSubAction[];
  recent_tool_calls?: Array<Record<string, unknown>>;
};

const GROUP_LABELS: Record<string, string> = {
  inspect: '只读检查本机已有资源',
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
];

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
  onClose?: () => void;
  onFinished?: (grant: TaskGrant | null) => void;
};

export function TaskGrantCard({ sessionId, projectId, objective, onClose, onFinished }: Props) {
  const [preview, setPreview] = useState<GrantPreview | null>(null);
  const [grant, setGrant] = useState<TaskGrant | null>(null);
  const [execution, setExecution] = useState<GrantExecution | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const loadPreview = useCallback(async () => {
    setPreviewing(true);
    setError(null);
    try {
      const body = await api<GrantPreview>('/support/task-grants/preview', {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          objective,
          project_id: projectId || null,
          capability_groups: ['inspect', 'resources', 'nodes', 'workflows', 'runtime', 'test'],
        }),
      });
      setPreview(body);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '无法生成授权预览');
    } finally {
      setPreviewing(false);
    }
  }, [sessionId, projectId, objective]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const authorize = useCallback(async () => {
    if (!preview) return;
    setAuthorizing(true);
    setError(null);
    try {
      // The click ends here: the grant is created first, then the bounded run
      // is requested.  Splitting the two keeps the authorization durable even
      // if the run request itself times out - the person can simply resume.
      const created = await api<{ grant_id: string }>('/support/task-grants/authorize', {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          preview_id: preview.preview_id,
          grant_digest: preview.grant_digest,
          confirm: true,
          run: false,
        }),
      });
      const early = await api<TaskGrant>(`/support/task-grants/${created.grant_id}`);
      setGrant(early);
      const body = await api<GrantExecution>(`/support/task-grants/${created.grant_id}/run`, {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, max_steps: 12 }),
      });
      setExecution(body);
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

  const stage = useMemo(
    () =>
      stageOf({
        previewing,
        authorized: Boolean(grant),
        status: grant?.status,
        steps: execution?.steps,
      }),
    [previewing, grant, execution],
  );

  const limits = preview?.limits || grant?.limits || {};
  const limitRows = LIMIT_LABELS.filter(([key]) => typeof limits[key] === 'number');

  return (
    <section className="copilot-grant" data-stage={stage.key} aria-label="本次任务授权">
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

          {limitRows.length ? (
            <>
              <h4>资源上限</h4>
              <ul>
                {limitRows.map(([key, label, format]) => (
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
            disabled={!preview.can_authorize || authorizing}
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
            {grant.paused_reason ? `（${grant.paused_reason}）` : ''}
          </p>

          {execution?.final ? <p className="copilot-grant-final">{execution.final}</p> : null}

          {execution?.steps?.length ? (
            <ol className="copilot-grant-steps">
              {execution.steps.map((step, index) => (
                <li key={`${step.capability_id}-${index}`} data-satisfied={step.satisfied ? 'true' : 'false'}>
                  <span>{step.capability_id}</span>
                  <em>{step.satisfied ? '已取得原任务回执' : step.error_code || step.status}</em>
                </li>
              ))}
            </ol>
          ) : null}

          <div className="copilot-grant-actions">
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
                <dd>{grant.validity?.expires_at || '—'}</dd>
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
