import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Check, ChevronRight, Copy, Download, FolderOpen, Loader2, RefreshCw, X,
} from 'lucide-react';
import { api, json, SETUP_CAPABILITY_LABEL } from '../api';
import type { SetupCapabilityState, SetupVerdict } from '../api';
import { describeBlocker } from '../errorActions';

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

export type CapabilityKey = 'h3' | 'image' | 'vlm' | 'asr' | 'relayvsr' | 'image_loras' | 'character_sheet';

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
  state: SetupCapabilityState;
  /** 审片等可选增强：不占机器级「下一步」，卡片如实标注。 */
  optional?: boolean;
  facts: CapabilityFacts;
  summary: string;
  next_step: SetupNextStep | null;
  missing_files: MissingFile[];
  missing_bytes?: number;
  required_files?: number;
  advisories?: Array<{ code?: string; message?: string }>;
  /** 用户-facing 两步归一：①文件齐不齐 ②跑不跑得通。 */
  two_step?: {
    files: { state: string; label: string; missing_count?: number; missing_bytes?: number };
    run: { state: string; label: string; reason?: string };
  };
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

export type OptionalCapabilityStatus = {
  key: string;
  label: string;
  optional: boolean;
  available: boolean;
  status: 'READY' | 'OPTIONAL_UNCONFIGURED' | 'UNVERIFIED' | string;
  blockers?: Array<{ code?: string; detail?: string; message?: string }>;
  impact?: string;
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
  optional_capabilities?: Record<string, OptionalCapabilityStatus>;
  optional_advisories?: string[];
  progress: Array<{ id: string; label: string; state: 'DONE' | 'ACTIVE' | 'PENDING' }>;
  primary_capability: CapabilityKey;
  action_required: (SetupNextStep & { options?: InstallationCandidate[] }) | null;
  blockers: string[];
  preparation: {
    status: string; task_id: string | null; current: string | null;
    steps: PreparationStep[]; done?: number; total?: number;
    message?: string | null; error?: string | null;
  };
  /** 与 /setup/verdict 同一份裁决（summary 内计算一次，各处只读）。 */
  verdict?: SetupVerdict;
  machine_progress?: MachineProgress;
  manual_download_root: string;
};

/** 机器级唯一进度投影：服务端汇总既有台账，前端只渲染。 */
export type MachineProgress = {
  active: boolean; percent: number; label: string; detail: string; source: string;
};

export const CAPABILITY_ORDER: CapabilityKey[] = [
  'h3', 'image', 'vlm', 'asr', 'relayvsr', 'image_loras', 'character_sheet',
];

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
  onGoSettings?: () => void;
  onSummary?: (summary: SetupSummary | null) => void;
};

export function SetupEnvironmentStatus({
  projectId, readOnly = false, refreshEpoch = 0, variantId = null, onPrepare,
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
    if (action.action === 'PREPARE_ENVIRONMENT' || action.action === 'RECONCILE'
        || action.action === 'SCAN'
        || (action.action === 'TRIAL' && action.capability === 'image')) {
      // 只有一个执行入口：核验、登记、下载、绑定、运行核验都是同一个「一键准备」
      // 后台任务的步骤。H3 真实出片属于作品「⑥ 视频生产」，不在基础配置伪造试镜按钮。
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
      {chosen && <p className="run-hint" data-testid="setup-installation">
        使用的 ComfyUI：{chosen.root}
        {chosen.port ? ` · 端口 ${chosen.port}` : ''}
        {chosen.adopted ? ' · 复用本机 ComfyUI 的模型与节点包（受管启动与显存保护）' : ''}
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
              {cap.optional && <span className="setup-cap-optional">可选增强</span>}
              <span className="setup-cap-state">{SETUP_CAPABILITY_LABEL[cap.state] ?? cap.state}</span>
            </header>
            <p>{cap.summary}</p>
            {(cap.advisories ?? []).filter((row) => row.message).length > 0 && <ul className="setup-cap-advisories">
              {(cap.advisories ?? []).filter((row) => row.message).map((row, index) => <li key={row.code ?? index}>
                <AlertTriangle size={11} /> {row.message}
              </li>)}
            </ul>}
            {cap.two_step ? <div className="setup-two-step">
              <div className={`setup-step-row files-${cap.two_step.files.state.toLowerCase()}`}>
                <span className="setup-step-key">① 文件</span>
                {cap.two_step.files.state === 'READY' ? <Check size={11} /> : <X size={11} />}
                <span>{cap.two_step.files.label}</span>
              </div>
              <div className={`setup-step-row run-${cap.two_step.run.state.toLowerCase()}`}>
                <span className="setup-step-key">② 运行</span>
                {cap.two_step.run.state === 'READY' ? <Check size={11} />
                  : cap.two_step.run.state === 'PREPARING' ? <span className="setup-phase-dot" />
                    : <X size={11} />}
                <span>{cap.two_step.run.label}{cap.two_step.run.reason ? ` · ${cap.two_step.run.reason}` : ''}</span>
              </div>
            </div> : <div className="setup-cap-facts">
              {chips.map((chip) => <span key={chip.label} className={chip.ok ? 'ok' : ''}>
                {chip.ok ? <Check size={10} /> : <X size={10} />}{chip.label}
              </span>)}
            </div>}
            {cap.two_step && !readOnly && (() => {
              const run = cap.two_step.run.state;
              if (run === 'READY') return null;
              // 整备任务正在推进这项能力：进度面板是唯一事实视图，
              // 不再挂一个只会重排同一任务的「跑不通」按钮。
              if (run === 'PREPARING') return null;
              const action = cap.next_step;
              if (action?.action === 'TRIAL' && key === 'h3') {
                return <p className="run-hint">运行合同已核对，可在作品的「⑥ 视频生产」开始真实试镜。</p>;
              }
              if (cap.two_step.files.state === 'MISSING') {
                return <button type="button" className="btn-secondary setup-cap-fix" disabled={!!busy}
                  onClick={() => onPrepare?.(key, summary as SetupSummary)}>
                  <Download size={12} /> 下载安装缺文件
                </button>;
              }
              if (key === 'relayvsr') {
                if (!action || action.action === 'SCAN') return null;
                return <button type="button" className="btn-primary setup-cap-fix" disabled={!!busy}
                  onClick={() => onPrepare?.(key, summary as SetupSummary)}>
                  <Download size={12} /> 一键整备 RelayVSR
                </button>;
              }
              if (key === 'image_loras') {
                if (!action) return null;
                return <button type="button" className="btn-primary setup-cap-fix" disabled={!!busy}
                  onClick={() => onPrepare?.(key, summary as SetupSummary)}>
                  <Download size={12} /> 整备生图风格 LoRA
                </button>;
              }
              if (key === 'character_sheet') {
                if (!action) return null;
                return <button type="button" className="btn-primary setup-cap-fix" disabled={!!busy}
                  onClick={() => onPrepare?.(key, summary as SetupSummary)}>
                  <Download size={12} /> 整备角色五视图定妆照
                </button>;
              }
              // 文件已齐但运行未核验：交给同一个「一键准备」后台任务核对运行
              // 合同 / 真实出图小样。首次真实 H3 运行走「开拍」，不在这里空点试镜。
              return <button type="button" className="btn-primary setup-cap-fix" disabled={!!busy}
                onClick={() => onPrepare?.(key, summary as SetupSummary)}>
                <Check size={12} /> 一键绑定渲染器并核对运行合同
              </button>;
            })()}
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

      {preparation && preparation.status !== 'IDLE' && (() => {
        const FILE_STEPS = ['SCAN', 'RECONCILE', 'DOWNLOAD', 'INSTANCE', 'TARGET'];
        const phase = (ids: string[]) => {
          const rows = preparation.steps.filter((s) => ids.includes(s.id));
          if (!rows.length) return 'SKIP';
          if (rows.some((s) => s.status === 'FAILED')) return 'FAILED';
          if (rows.every((s) => s.status === 'DONE' || s.status === 'SKIPPED')) return 'DONE';
          if (rows.some((s) => s.status === 'RUNNING' || s.id === preparation.current)) return 'RUNNING';
          return 'PENDING';
        };
        const filePhase = phase(FILE_STEPS);
        const runPhase = phase(preparation.steps.map((s) => s.id).filter((id) => !FILE_STEPS.includes(id)));
        const phaseRow = (key: string, label: string, st: string) => (
          <div className={`setup-step-row files-${st.toLowerCase()}`}>
            <span className="setup-step-key">{key}</span>
            {st === 'DONE' ? <Check size={11} /> : st === 'FAILED' ? <AlertTriangle size={11} /> : <span className="setup-phase-dot" />}
            <span>{label}{st === 'RUNNING' ? '（进行中）' : st === 'FAILED' ? '（停下，见明细）' : ''}</span>
          </div>
        );
        return <div className="setup-preparation" role="status">
          <b>{preparation.status === 'DONE' || preparation.status === 'DONE_WITH_WARNINGS'
            ? '制作环境准备结束'
            : preparation.status === 'FAILED' ? '准备在中途停下'
              : '正在后台准备（关闭页面也会继续）'}</b>
          <span>{preparation.message ?? ''}</span>
          <div className="setup-two-step">
            {phaseRow('①', '文件整备：检索复用 · 补齐缺文件', filePhase)}
            {phaseRow('②', '运行验证：配置绑定 · 真实试跑', runPhase)}
          </div>
          <details className="setup-details">
            <summary><ChevronRight size={12} /> 工程明细（{preparation.steps.length} 步）</summary>
            <ol>
              {preparation.steps.map((step) => <li key={step.id} className={`setup-step-${step.status.toLowerCase()}`}>
                {step.status === 'DONE' ? <Check size={10} />
                  : step.status === 'FAILED' ? <AlertTriangle size={10} /> : <span className="setup-phase-dot" />}
                {step.label}{step.detail ? ` · ${step.detail}` : ''}
              </li>)}
            </ol>
          </details>
          {preparation.status === 'FAILED' && (() => {
            // 失败原因挂同一张错误码映射表：裸类名（ValueError）不是原因。
            const fault = describeBlocker(preparation.error ?? undefined, preparation.message ?? '');
            return <p className="form-error">
              {fault.reason}{fault.why ? ` ${fault.why}` : ''}
              已经在前面完成的步骤会保留，不需要从头再来。
            </p>;
          })()}
        </div>;
      })()}

      {!readOnly && summary.action_required && summary.action_required.action !== 'SELECT_PROJECT'
        && !(summary.action_required.action === 'TRIAL' && summary.action_required.capability === 'h3')
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

      {summary.optional_capabilities && Object.keys(summary.optional_capabilities).length > 0 && (
        <div className="setup-optional-capabilities" data-testid="setup-optional-capabilities" style={{ marginTop: 10 }}>
          <div className="run-hint" style={{ marginBottom: 4 }}>
            按需可选增强（仅在启用对应加速、二采精修、音视频参考或超分时生效，不影响基础 1 图 H3 拍摄）：
          </div>
          <div className="setup-cap-facts">
            {Object.values(summary.optional_capabilities).map((opt) => (
              <span
                key={opt.key}
                className={opt.available ? 'ok' : ''}
                title={opt.impact ?? ''}
              >
                {opt.available ? <Check size={10} /> : <X size={10} />}
                {opt.label}：{opt.available ? '已就绪' : opt.status === 'UNVERIFIED' ? '待核验（可选）' : '可选未配置'}
              </span>
            ))}
          </div>
        </div>
      )}

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
