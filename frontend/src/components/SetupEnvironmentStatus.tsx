import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Check, ChevronRight, Copy, FolderOpen, Loader2, RefreshCw, X,
} from 'lucide-react';
import { api, json } from '../api';

/**
 * 「制作环境」的唯一事实视图。
 *
 * 这里渲染的每一个结论都来自服务端的 `/setup/summary`：本机找到的安装、每项
 * 能力已经走到哪一层、下一步是什么。界面不推断、不缓存判定、不维护第二套状态
 * 机——它只把服务器已经证明的事实翻译成一句人话和一个按钮。
 *
 * 两个硬约束写进了类型里：
 * - `facts.FILES_FOUND` 不等于 `FILES_VERIFIED`，更不等于 `RENDER_VERIFIED`；
 * - `PENDING_RECIPE` 不是「已就绪」，页面不能给它绿灯。
 */

export type CapabilityKey = 'h3' | 'image' | 'vlm' | 'asr';

export type CapabilityFacts = {
  FILES_FOUND?: boolean;
  FILES_VERIFIED?: boolean;
  RESOURCE_REGISTERED?: boolean;
  INSTANCE_BOUND?: boolean;
  WORKFLOW_VALIDATED?: boolean;
  RENDER_VERIFIED?: boolean;
  PRODUCTION_READY?: boolean;
  RECIPE_READY?: boolean;
  BINDING_STATE?: string;
};

export type SetupNextStep = {
  code: string; label: string; reason: string; action: string;
  capability?: CapabilityKey;
};

export type SetupCapability = {
  key: CapabilityKey;
  label: string;
  catalog?: string;
  state: string;
  facts: CapabilityFacts;
  summary: string;
  next_step: SetupNextStep | null;
  missing_files: MissingFile[];
  missing_bytes?: number;
  required_files?: number;
  advisories?: Array<{ code?: string }>;
};

export type MissingFile = {
  component_id: string;
  relative_path: string;
  filename?: string;
  size: number;
  sha256?: string;
  url?: string;
  mirrors?: string[];
  save_to?: string;
};

export type InstallationCandidate = {
  root: string;
  instance_id?: string | null;
  port?: number | null;
  managed?: boolean;
  adopted?: boolean;
  source: string;
  reason: string;
  exists?: boolean;
};

export type PreparationStep = {
  id: string; label: string; status: string; detail?: string | null; code?: string | null;
};

export type SetupSummary = {
  snapshot_version: number;
  computed_at: string;
  project_id: string | null;
  /** 这份快照描述的是哪套 H3 变体；null = 默认已验收那套。 */
  variant_id?: string | null;
  installation: {
    selected: InstallationCandidate | null;
    candidates: InstallationCandidate[];
    choice_required: boolean;
  };
  capabilities: Record<CapabilityKey, SetupCapability>;
  progress: Array<{ id: string; label: string; state: 'DONE' | 'ACTIVE' | 'PENDING' }>;
  primary_capability: CapabilityKey;
  action_required: (SetupNextStep & { options?: InstallationCandidate[] }) | null;
  blockers: string[];
  preparation: {
    status: string; task_id: string | null; current: string | null;
    steps: PreparationStep[]; done?: number; total?: number;
    message?: string | null; error?: string | null;
  };
  manual_download_root: string;
};

const CAPABILITY_ORDER: CapabilityKey[] = ['h3', 'image', 'vlm', 'asr'];

const STATE_LABEL: Record<string, string> = {
  MISSING: '未准备',
  FOUND: '已找到',
  VERIFIED: '已核验',
  BOUND: '已就绪',
  READY: '可试镜',
  VERIFIED_RENDER: '已真实验证',
  UNKNOWN: '未知',
};

function gib(bytes?: number | null): string {
  if (!bytes) return '0 GiB';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function factChips(capability: SetupCapability): Array<{ label: string; ok: boolean }> {
  const facts = capability.facts ?? {};
  const chips = [
    { label: '文件', ok: !!facts.FILES_FOUND },
    { label: '核验', ok: !!facts.FILES_VERIFIED },
    { label: '绑定', ok: !!facts.INSTANCE_BOUND },
    { label: '运行核对', ok: !!facts.WORKFLOW_VALIDATED },
    { label: '真实出片', ok: !!facts.RENDER_VERIFIED },
  ];
  return chips;
}

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    /* a browser may refuse; the value is visible next to the button */
  }
}

type Props = {
  projectId: string;
  /** The director page shows the same facts but performs nothing. */
  readOnly?: boolean;
  refreshEpoch?: number;
  /** 用户选定的 H3 变体：h3 能力的事实必须跟着它走，而不是默认那套。 */
  variantId?: string | null;
  onPrepare?: (capability: CapabilityKey, summary: SetupSummary) => void | Promise<void>;
  onTrial?: (capability: CapabilityKey) => void | Promise<void>;
  onReconcile?: (capability: CapabilityKey) => void | Promise<void>;
  onGoSettings?: () => void;
  onSummary?: (summary: SetupSummary | null) => void;
};

export function SetupEnvironmentStatus({
  projectId, readOnly = false, refreshEpoch = 0, variantId = null, onPrepare, onTrial, onReconcile,
  onGoSettings, onSummary,
}: Props) {
  const [summary, setSummary] = useState<SetupSummary | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [openFiles, setOpenFiles] = useState<CapabilityKey | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (projectId) params.set('project_id', projectId);
      if (variantId) params.set('variant_id', variantId);
      const query = params.toString() ? `?${params.toString()}` : '';
      const next = await api<SetupSummary>(`/setup/summary${query}`);
      if (!mounted.current) return;
      setSummary(next);
      setError('');
      onSummary?.(next);
    } catch (problem) {
      if (!mounted.current) return;
      setError(problem instanceof Error ? problem.message : String(problem));
      onSummary?.(null);
    }
  }, [projectId, variantId, onSummary]);

  // 打开页面就检测：不需要先点「检测本机」才看到环境。
  useEffect(() => { void load(); }, [load, refreshEpoch]);

  // 后台在推进时跟着刷新；准备完成后自动停。
  useEffect(() => {
    const status = summary?.preparation?.status;
    if (status !== 'QUEUED' && status !== 'RUNNING') return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [summary?.preparation?.status, load]);

  const chosen = summary?.installation?.selected ?? null;

  async function handleAction() {
    const action = summary?.action_required;
    if (!action) return;
    if (action.action === 'CHOOSE_INSTALLATION') {
      // 选哪一套必须是人点的：这里不替人挑列表第一项。
      return;
    }
    if (action.action === 'RECONCILE') {
      if (onReconcile && action.capability) { await onReconcile(action.capability); await load(); }
      return;
    }
    if (action.action === 'TRIAL') {
      if (onTrial && action.capability) await onTrial(action.capability);
      return;
    }
    if (action.action === 'PREPARE_ENVIRONMENT') {
      if (onPrepare && action.capability) await onPrepare(action.capability, summary as SetupSummary);
      return;
    }
    if (action.action === 'SELECT_PROJECT') {
      onGoSettings?.();
    }
  }

  async function chooseInstallation(row: InstallationCandidate) {
    setBusy('正在记住这套安装…');
    try {
      await api('/setup/installation-choice', json('POST', {
        path: row.root, port: row.port ?? null, project_id: projectId || null,
      }));
      await load();
    } finally { setBusy(''); }
  }

  const preparation = summary?.preparation;
  const verdict = summary
    ? (summary.action_required
      ? summary.action_required.reason
      : '这台电脑的制作环境已经准备好，可以继续制作。')
    : (error ? `读取本机环境失败：${error}` : '正在读取这台电脑的环境…');

  return <div className="setup-env" data-testid="setup-environment">
    <div className="setup-env-head">
      <div>
        <div className="section-kicker">00 / 制作环境</div>
        {readOnly && <h2>制作环境</h2>}
        <p className="setup-verdict"><b>{verdict}</b></p>
      </div>
      <button type="button" className="btn-secondary" disabled={!!busy}
        onClick={() => void load()}>
        {busy ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
        重新检查
      </button>
    </div>

    {summary && <>
      <ol className="setup-phases" aria-label="制作环境准备进度">
        {summary.progress.map((phase) => <li key={phase.id} className={`setup-phase-${phase.state.toLowerCase()}`}>
          {phase.state === 'DONE' ? <Check size={11} /> : <span className="setup-phase-dot" />}
          {phase.label}
        </li>)}
      </ol>

      {chosen && <p className="run-hint" data-testid="setup-installation">
        使用的 ComfyUI：{chosen.root}
        {chosen.port ? ` · 端口 ${chosen.port}` : ''}
        {chosen.adopted ? ' · 你自己的安装（只读复用）' : ''}
      </p>}
      {summary.installation.choice_required && <div className="setup-choice" role="group" aria-label="选择本机安装">
        <b>这台电脑上找到不止一套 ComfyUI</b>
        <span>选一次就好，之后会记住；只有使用权会变的那次才需要你确认。</span>
        {summary.installation.candidates.map((row) => <button key={row.root} type="button"
          className="btn-secondary setup-choice-row" onClick={() => void chooseInstallation(row)}>
          <span>{row.root}</span><em>{row.reason}</em>
        </button>)}
      </div>}

      <div className="setup-caps-grid">
        {CAPABILITY_ORDER.map((key) => {
          const cap = summary.capabilities[key];
          if (!cap) return null;
          const chips = factChips(cap);
          return <article key={key} className={`setup-cap-card setup-cap-${cap.state.toLowerCase()}`}>
            <header>
              <b>{cap.label}</b>
              <span className="setup-cap-state">{STATE_LABEL[cap.state] ?? cap.state}</span>
            </header>
            <p>{cap.summary}</p>
            <div className="setup-cap-facts">
              {chips.map((chip) => <span key={chip.label} className={chip.ok ? 'ok' : ''}>
                {chip.ok ? <Check size={10} /> : <X size={10} />}{chip.label}
              </span>)}
            </div>
            {cap.missing_files.length > 0 && <button type="button" className="btn-text"
              onClick={() => setOpenFiles(openFiles === key ? null : key)}>
              <ChevronRight size={11} />
              缺少 {cap.missing_files.length} 个文件（{gib(cap.missing_bytes)}）
            </button>}
            {openFiles === key && <div className="setup-missing">
              <p>把文件按原文件名放进下面这个目录即可；系统会自己发现、核验并继续安装。</p>
              <div className="setup-missing-row">
                <code>{summary.manual_download_root}</code>
                <button type="button" className="btn-text" onClick={() => void copyText(summary.manual_download_root)}>
                  <Copy size={11} />复制目录
                </button>
              </div>
              <ul>
                {cap.missing_files.slice(0, 40).map((row) => <li key={`${row.component_id}:${row.relative_path}`}>
                  <span>{row.filename ?? row.relative_path}</span>
                  <em>{gib(row.size)}</em>
                  {row.url && <a href={row.url} target="_blank" rel="noreferrer">打开下载链接</a>}
                  <button type="button" className="btn-text" onClick={() => void copyText(row.url ?? '')}>
                    <Copy size={10} />复制链接
                  </button>
                  <button type="button" className="btn-text" onClick={() => void copyText(row.save_to ?? '')}>
                    <FolderOpen size={10} />复制保存位置
                  </button>
                </li>)}
              </ul>
            </div>}
          </article>;
        })}
      </div>

      {preparation && preparation.status !== 'IDLE' && <div className="setup-preparation" role="status">
        <b>{preparation.status === 'DONE' || preparation.status === 'DONE_WITH_WARNINGS'
          ? '制作环境准备结束'
          : preparation.status === 'FAILED' ? '准备在中途停下'
            : '正在后台准备（关闭页面也会继续）'}</b>
        <span>{preparation.message ?? ''}</span>
        <ol>
          {preparation.steps.map((step) => <li key={step.id} className={`setup-step-${step.status.toLowerCase()}`}>
            {step.status === 'DONE' ? <Check size={10} />
              : step.status === 'FAILED' ? <AlertTriangle size={10} /> : <span className="setup-phase-dot" />}
            {step.label}{step.detail ? ` · ${step.detail}` : ''}
          </li>)}
        </ol>
        {preparation.status === 'FAILED' && <p className="form-error">
          {preparation.error ?? '准备没有完成'}：已经在前面完成的步骤会保留，不需要从头再来。
        </p>}
      </div>}

      {!readOnly && summary.action_required && summary.action_required.action !== 'SELECT_PROJECT'
        && !summary.installation.choice_required
        && <div className="setup-primary-action">
          <button type="button" className="btn-primary" disabled={!!busy} onClick={() => void handleAction()}>
            {busy ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
            {summary.action_required.label}
          </button>
          <span className="run-hint">{summary.action_required.reason}</span>
        </div>}
      {readOnly && <p className="run-hint">
        这里是只读摘要。完整整备在「设置 → 制作环境」一处完成。
        {onGoSettings && <button type="button" className="btn-text" onClick={onGoSettings}>去设置处理</button>}
      </p>}

      {summary.blockers.length > 0 && <details className="setup-details">
        <summary><ChevronRight size={12} /> 诊断与审计（{summary.blockers.length}）</summary>
        <div className="setup-detail-body">
          <p>{summary.blockers.join('、')}</p>
          <p className="run-hint">快照版本 {summary.snapshot_version} · 计算于 {new Date(summary.computed_at).toLocaleString()}</p>
        </div>
      </details>}
    </>}

    {!summary && error && <p className="form-error" role="alert">读取本机环境失败：{error}</p>}
  </div>;
}

export default SetupEnvironmentStatus;
