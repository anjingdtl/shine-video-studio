import { Sparkles } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { LLMSetupPanel } from './LLMSetupPanel';
import { VariantRecommendationCard } from './VariantRecommendation';
import {
  approveAndExecuteSetupPlan,
  cancelInstallationAttempt,
  fetchInstallationAttempt,
  isQueuedInstallation,
  latestInstallationAttempt,
  pauseInstallationAttempt,
  rememberInstallationAttempt,
  resumeInstallationAttempt,
  storedInstallationAttempt,
  waitForInstallationAttempt,
} from '../setupExecution';
import type { InstallationAttemptView, SetupExecutionResult } from '../setupExecution';
import { label, prepRowLabels, providerLabels, serviceStatusLabels } from '../constants';
import type { DeploymentStorage } from './StorageSettings';

type ToolInfo = { present: boolean; version: string; at_floor: boolean };
type ExistingScan = { root: string; snapshot: string; candidate_bytes: number; scanned_files: number;
  matches: Array<{ component_id: string; relative_path: string; size: number; status?: string }>;
  missing: Array<{ component_id: string; relative_path: string; size: number }>; skipped: string[];
  software?: { installed: boolean; version_status: string; actual_revision?: string | null;
    runtime?: { status: string; python_executable?: string | null }; node_revisions_verified?: boolean } };
type ExistingVerification = { software: NonNullable<ExistingScan['software']>;
  verified: Array<{ component_id: string; relative_path: string; size: number; status: string }>;
  rejected: Array<{ component_id: string; relative_path: string; size: number; status: string }>;
  verified_bytes: number; copied_bytes: number; verification_reused?: boolean };
type ExistingModelImport = { imported: Array<{ component_id: string; relative_path: string; size: number; status: string }>;
  rejected: Array<{ component_id: string; relative_path: string }>; reused_bytes: number };
type EnvReport = {
  platform: Record<string, string>;
  tools: Record<string, ToolInfo>;
  gpu: Record<string, unknown>;
  ram: { status: string; ram_mb?: number; at_floor?: boolean };
  disk: { status: string; free_gb: number; at_floor: boolean };
  port: { status: string; port: number };
  data_dir: { path: string; writable: boolean };
  deployment_dir?: { path: string; writable: boolean };
  blockers: Array<{ code: string; tool?: string; detail?: string }>;
  bootstrap: string;
};
type CatalogComponent = {
  component_id: string; kind: string; revision: string; license_id: string; usage: string;
  license_url: string; destination_family: string | null; role: string | null;
  files: number; bytes: number; status: string; blocked_reason: string;
};
type CatalogSummary = {
  catalog_id: string; catalog_version: string; generated: string;
  platform: Record<string, unknown>; total_bytes: number; components: CatalogComponent[];
  // The complete catalog, the default production components and the optional
  // advisory components are three different numbers; none of them is a
  // hard-coded total.
  download_breakdown?: {
    complete_catalog_bytes: number; default_production_bytes: number; optional_review_bytes: number;
    optional_review_components: string[]; model_bytes: number; runtime_bytes: number; selected_bytes?: number;
  };
};
type PlanRef = { plan_id: string; sha256: string; kind: string; action?: string; tier?: string; status: string };
type UpstreamRow = { id: string; label: string; host: string; status: string;
  http_status: number | null; error: string | null; needed_by: string };
type Connectivity = { status: string; checked_at: string; blocked: string[]; degraded: string[];
  upstreams: UpstreamRow[]; guidance: string[] };
type Readiness = { catalog: { id: string; version: string }; layers: Array<Record<string, unknown>>; production_gates: Record<string, boolean>; human_steps: string[] };
type RuntimeState = { status: string; render_provider: string; error: string | null;
  candidate: { project_id: string; recipe_sha256: string } | null;
  services_verified?: boolean;
  service_validation?: { status: string; blockers?: string[]; validated_at?: string | null;
    node_count?: number | null; workflow_profiles?: string[]; review_models?: Record<string, string> } | null;
};
type VoiceReadiness = { status: string; voice_master_capable: boolean; voice_clone_capable?: boolean;
  roles: Array<{ role: string; package_id: string | null; status: string | null; code: string | null;
    capability?: string; files_present?: boolean | null; runtime_configured?: boolean }> };
type ManagedProcess = { kind: 'renderer' | 'review'; id: string | null; stage: string | null;
  state: string; port: number | null; pid: number | null; lease_status: string | null;
  job_status: string | null; in_flight?: boolean };
type ReleaseReport = { stopped: Array<ManagedProcess & { outcome?: string; reason?: string }>;
  refused: Array<ManagedProcess & { outcome?: string; reason?: string }> };

const INSTALL_STAGE_LABELS: Record<string, string> = {
  QUEUED: '已排队',
  DOWNLOADING: '下载中',
  RETRY_WAIT: '等待重试',
  VERIFYING: '校验文件',
  INSTALLING: '安装中',
  FILES_VERIFIED_PENDING_RUNTIME: '文件已校验（运行时尚未验证）',
  VALIDATING_RUNTIME_CONTRACT: '校验运行契约',
  RUNTIME_CONTRACT_READY: '运行契约已就绪',
  PAUSED: '已暂停',
  CANCELLED: '已取消',
  FAILED_RETRYABLE: '失败（可重试）',
  NEEDS_REVIEW: '需要对账',
  MOCK_VERIFIED: '演练完成（未改动系统）',
};
const MANAGED_STATE_LABELS: Record<string, string> = {
  RUNNING: '运行中', GONE: '进程已不在', UNVERIFIED: '无法确认归属', STOPPED: '已停止',
};

const TOOL_LABELS: Record<string, string> = {
  uv: 'uv', node: 'Node.js', npm: 'npm', pwsh: 'PowerShell 7', ffmpeg: 'FFmpeg', ffprobe: 'FFprobe',
};
const RUNTIME_LABELS: Record<string, string> = {
  ACTIVE: '已激活', NOT_ACTIVATED: '待确认', INVALID_REQUIRES_REVIEW: '配置已变更，需重新核验',
};

const VOICE_ROLE_LABELS: Record<string, string> = {
  design: '角色声音设计', base: '声音克隆', asr: '语音转写', align: '强制对齐',
};

const VOICE_CODE_LABELS: Record<string, string> = {
  VOICE_PACKAGE_UNROUTED: '未指派模型包',
  VOICE_PACKAGE_MISSING: '指派的模型包不存在',
  VOICE_CAPABILITY_MISSING: '模型包缺少该能力',
  VOICE_PACKAGE_NOT_VERIFIED: '模型文件尚未通过哈希校验',
  VOICE_ROLE_RUNTIME_UNCONFIGURED: '未配置本机运行环境',
  VOICE_RUNTIME_PYTHON_NOT_ABSOLUTE: '运行解释器不是绝对路径',
  VOICE_RUNTIME_PYTHON_MISSING: '运行解释器不存在',
  VOICE_RUNTIME_PYTHON_CHANGED: '运行解释器哈希已变化',
  VOICE_RUNTIME_PYTHON_UNREADABLE: '运行解释器无法读取',
  VOICE_RUNTIME_PACKAGES_DIR_NOT_ABSOLUTE: '依赖目录不是绝对路径',
  VOICE_RUNTIME_PACKAGES_DIR_MISSING: '依赖目录不存在',
  VOICE_RUNTIME_VERSIONS_INCOMPLETE: '依赖版本未锁定完整',
  VOICE_RUNTIME_PACKAGE_MISMATCH: '依赖版本与模型包不匹配',
  VOICE_PACKAGE_RUNTIME_VERSIONS_UNPINNED: '模型包未锁定运行版本',
};

/** One honest sentence per blocked voice role: what is missing and what to do. */
function voiceRoleText(role: { role: string; code: string | null; files_present?: boolean | null }): string {
  const name = VOICE_ROLE_LABELS[role.role] ?? role.role;
  if (!role.code) return `${name}：就绪`;
  const reason = VOICE_CODE_LABELS[role.code] ?? role.code;
  if (role.code === 'VOICE_PACKAGE_NOT_VERIFIED' && role.files_present === true) {
    return `${name}：${reason}（文件已在磁盘，需重新核对安装记录以推进状态）`;
  }
  return `${name}：${reason}`;
}

function voiceSummary(voice: VoiceReadiness | null): string {
  if (!voice) return '读取中';
  const blocked = voice.roles.filter((row) => row.code !== null);
  if (!blocked.length) return '本地语音模型就绪（生成/克隆/转写/对齐）';
  if (voice.voice_master_capable) {
    return `角色声音可生成（${blocked.map(voiceRoleText).join('；')}）`;
  }
  return blocked.map(voiceRoleText).join('；');
}

function blockerText(blocker: EnvReport['blockers'][number]): string {
  const tool = TOOL_LABELS[blocker.tool ?? ''] ?? blocker.tool ?? '';
  const messages: Record<string, string> = {
    TOOL_MISSING_INSTALLABLE: `${tool} 尚未安装`, TOOL_STALE: `${tool} 版本需要更新`,
    GPU_VRAM_BELOW_FLOOR: '显卡可用显存不足', RAM_BELOW_FLOOR: '本机内存不足或无法读取',
    DISK_BELOW_FLOOR: '安装目录空间不足', PORT_OCCUPIED: '服务端口被占用',
    DATA_DIR_NOT_WRITABLE: '项目数据目录无法写入', DEPLOYMENT_DIR_NOT_WRITABLE: '安装目录无法写入',
    NVIDIA_SMI_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
    NVIDIA_DRIVER_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
    NVIDIA_SMI_OUTPUT_UNPARSEABLE: '显卡检测结果无法读取，请检查显卡驱动',
  };
  return messages[blocker.code] ?? `环境检测未通过（${blocker.code}），请查看检测详情`;
}

function fmtBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let result = value;
  let index = 0;
  while (result >= 1024 && index < 3) { result /= 1024; index += 1; }
  return `${result.toFixed(result >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function installedVersionText(status: string): string {
  const labels: Record<string, string> = {
    INSTALLED_VERSION_MATCH: '主程序版本与固定清单一致',
    INSTALLED_VERSION_MISMATCH: '主程序版本与固定清单不同',
    INSTALLED_VERSION_MODIFIED: '固定版本存在本地修改',
    INSTALLED_VERSION_UNKNOWN: '已安装，版本无法核验',
  };
  return labels[status] ?? '已安装，版本待核验';
}

/** Plain-language model cards: the fixed catalog grouped by purpose, with an
 *  honest state per group (docs/UI_SIMPLIFICATION_PLAN_20261102.md section 5.2).
 *  Never claims "installed" from a filename match: candidates stay pending
 *  until hash verification, and "not scanned yet" never reads as "absent". */
const PURPOSE_LABELS: Record<string, string> = {
  video: '视频生成 · MiniMax H3',
  vision: '画面检查 · Qwen3-VL',
  audio: '语音检查 · Qwen3-ASR',
  program: '程序与节点 · ComfyUI',
};

function purposeOf(component: CatalogComponent): 'video' | 'vision' | 'audio' | 'program' {
  const id = component.component_id.toLowerCase();
  if (component.kind === 'node' || component.kind === 'runtime' || id.startsWith('comfyui')) return 'program';
  if (id.includes('vl')) return 'vision';
  if (id.includes('asr')) return 'audio';
  return 'video';
}

function productionCatalogComponents(components: CatalogComponent[]): CatalogComponent[] {
  return components.filter((component) => {
    const role = (component.role ?? '').toUpperCase();
    const purpose = purposeOf(component);
    return role !== 'VLM' && role !== 'ASR' && purpose !== 'vision' && purpose !== 'audio';
  });
}

type ModelCard = {
  key: string;
  label: string;
  components: CatalogComponent[];
  state: { text: string; tone: string };
  detail: string;
};

function buildModelCards(
  checked: boolean,
  catalog: CatalogSummary | null,
  scan: ExistingScan | null,
  resolved: boolean,
  importedResult: string,
  resolvedMode: 'none' | 'installed' | 'cached',
): ModelCard[] {
  if (!checked || !catalog) return [];
  const groups = new Map<string, CatalogComponent[]>();
  for (const component of catalog.components) {
    const purpose = purposeOf(component);
    const bucket = groups.get(purpose) ?? [];
    bucket.push(component);
    groups.set(purpose, bucket);
  }
  const cards: ModelCard[] = [];
  for (const [purpose, components] of groups) {
    const componentIds = new Set(components.map((component) => component.component_id));
    const matches = scan?.matches.filter((row) => componentIds.has(row.component_id)) ?? [];
    const missing = scan?.missing.filter((row) => componentIds.has(row.component_id)) ?? [];
    const candidateCount = matches.length;
    const verifiedCount = matches.filter((row) => row.status === 'INSTALLED_HASH_MATCH' || row.status === 'CACHED').length;
    const expectedCount = components.reduce((sum, component) => sum + (component.files || 0), 0)
      || matches.length + missing.length;
    const incompleteCount = Math.max(0, expectedCount - verifiedCount);
    const optionalReview = purpose === 'vision' || purpose === 'audio';
    // A reviewer is ONE model package; the catalog splits it into files
    // (weight shards, tokenizer, configs). Say so instead of "13 models".
    const packageCount = Math.max(1, components.length);
    let resolvedState: { text: string; tone: string } | undefined;
    if (resolved && importedResult) {
      if (purpose === 'program' && scan?.software?.installed) {
        resolvedState = { text: 'ComfyUI 已安装，运行兼容性待核验', tone: 'candidate' };
      } else if (expectedCount > 0 && incompleteCount === 0) {
        resolvedState = { text: optionalReview ? '模型文件已安装并核验（可选建议项）' : '固定版本文件已安装并哈希核验', tone: 'verified' };
      } else if (optionalReview) {
        resolvedState = { text: `建议审片模型未就绪（${packageCount} 个模型包 · 已核验 ${verifiedCount}/${expectedCount} 个文件，不阻断 H3 出片）`, tone: verifiedCount ? 'candidate' : 'absent' };
      } else {
        resolvedState = { text: `仅有部分固定版本文件已核验（${verifiedCount}/${expectedCount} 项）`, tone: 'candidate' };
      }
    }
    cards.push({
      key: purpose,
      label: PURPOSE_LABELS[purpose] ?? purpose,
      components,
      state: scan
        ? (resolvedState
          ? resolvedMode === 'installed' && resolvedState.tone === 'verified'
            ? { ...resolvedState, text: `${resolvedState.text}（原目录，未复制）` }
            : resolvedMode === 'cached' && resolvedState.tone === 'verified'
              ? { ...resolvedState, text: '已校验并缓存（尚未激活）' }
              : resolvedState
          : candidateCount > 0 ? { text: `发现文件，待校验（${candidateCount} 项）`, tone: 'candidate' }
            : optionalReview
              ? { text: '建议审片模型未发现（不阻断 H3 出片）', tone: 'absent' }
              : { text: '未在已检查位置发现（可选择其他目录）', tone: 'absent' })
        : { text: '待检查已有目录', tone: 'muted' },
      detail: scan
        ? resolved
          ? `已核验 ${verifiedCount} / ${expectedCount} 个固定版本文件${incompleteCount ? ` · 仍缺或未匹配 ${incompleteCount} 项` : ''}${optionalReview ? `（同属 ${packageCount} 个模型包，含权重分片、分词器与配置）` : ''}`
          : `发现候选 ${candidateCount} · 未找到固定版本文件 ${missing.length}`
        : '',
    });
  }
  return cards;
}

export function SetupPanel({ projectId, onRefresh, refreshEpoch = 0, includeImageCapability = false }: {
  projectId: string; onRefresh: () => Promise<void>; refreshEpoch?: number;
  /** The combination chosen on the recommendation card; it is sent with the
   *  plan so the approved plan really is the configuration the person saw. */
  includeImageCapability?: boolean;
}) {
  const [checked, setChecked] = useState(false);
  const [progress, setProgress] = useState('');
  const [existingRoots, setExistingRoots] = useState<string[]>([]);
  const [existingPath, setExistingPath] = useState('');
  const [existingScan, setExistingScan] = useState<ExistingScan | null>(null);
  const [existingResolved, setExistingResolved] = useState(false);
  const [resolvedMode, setResolvedMode] = useState<'none' | 'installed' | 'cached'>('none');
  const [existingChoice, setExistingChoice] = useState<'reuse' | 'new'>('reuse');
  const [importResult, setImportResult] = useState('');
  const [env, setEnv] = useState<EnvReport | null>(null);
  const [connectivity, setConnectivity] = useState<Connectivity | null>(null);
  const [storage, setStorage] = useState<DeploymentStorage | null>(null);
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [plans, setPlans] = useState<PlanRef[]>([]);
  const [attempt, setAttempt] = useState<InstallationAttemptView | null>(null);
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [voice, setVoice] = useState<VoiceReadiness | null>(null);
  const [managed, setManaged] = useState<ManagedProcess[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'bad'; text: string } | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const withJson = useCallback((method: string, body: unknown): RequestInit => {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }, []);

  const say = useCallback((text: string, kind: 'ok' | 'bad' = 'ok') => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice(null), 6000);
  }, []);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    try { say(await action()); } catch (error) { say((error as Error).message, 'bad'); }
    finally { setBusy(false); }
  }

  const loadEnvironment = useCallback(async () => {
    try { setEnv(await api<EnvReport>('/setup/environment')); } catch { /* loopback probe stays silent */ }
  }, []);

  function checkConnectivity() {
    void run(async () => {
      const result = await api<Connectivity>('/setup/connectivity');
      setConnectivity(result);
      if (result.blocked.length > 0) return `上游不可达：${result.blocked.join('、')}。先解决代理/VPN 再下载。`;
      return `上游检查通过：${result.status} · ${result.upstreams.map((row) => row.host).join(' / ')}`;
    });
  }
  const loadCatalog = useCallback(async () => {
    try { setCatalog(await api<CatalogSummary>('/setup/catalog')); } catch { /* keep prior state */ }
  }, []);
  const loadReadiness = useCallback(async () => {
    try { setReadiness(await api<Readiness>('/setup/readiness')); } catch { /* keep prior state */ }
  }, []);
  const loadRuntime = useCallback(async () => {
    try {
      setRuntime(await api<RuntimeState>('/setup/runtime'));
      setNotice((current) => current?.kind === 'bad' ? null : current);
    } catch { say('本机环境状态暂时不可用。确认工作台已启动后，点“刷新全部数据”重试。', 'bad'); }
  }, [say]);
  const loadVoice = useCallback(async () => {
    try { setVoice(await api<VoiceReadiness>('/setup/voice-readiness')); } catch { /* keep prior state */ }
  }, []);
  const loadManaged = useCallback(async () => {
    try {
      const rows = await api<{ processes: ManagedProcess[] }>('/setup/managed-processes');
      setManaged(rows.processes ?? []);
    } catch { /* keep prior state */ }
  }, []);  const loadStorage = useCallback(async () => {
    try {
      setStorage(await api<DeploymentStorage>('/setup/storage'));
      setNotice((current) => current?.kind === 'bad' ? null : current);
    } catch { say('本机存储信息暂时不可用。确认工作台已启动后，点“刷新全部数据”重试。', 'bad'); }
  }, [say]);
  const loadPlans = useCallback(async () => {
    const selected = projectId;
    if (!selected) { setPlans([]); return; }
    const rows = await api<Array<{ id: string; sha256: string; status: string;
      payload: { kind?: string; deployment?: unknown; operation?: { action?: string } } }>>(
      `/comfyops/install-plans?project_id=${selected}`);
    if (projectRef.current !== selected) return;
    const next = rows.filter(row => row.payload.deployment || row.payload.kind === 'INSTANCE_LIFECYCLE')
      .map(row => ({ plan_id: row.id, sha256: row.sha256, status: row.status,
        kind: row.payload.kind ?? 'INSTALL', action: row.payload.operation?.action }));
    setPlans(next);
    // A reopened page re-attaches to the attempt it remembers instead of
    // starting a second installation.  When the browser has no memory (new
    // tab, cleared cache) the server's target-scoped history is the receipt.
    for (const plan of next) {
      const stored = storedInstallationAttempt(plan.plan_id);
      if (stored) {
        try {
          const view = await fetchInstallationAttempt(stored);
          if (projectRef.current !== selected) return;
          setAttempt(view);
          continue;
        } catch {
          /* fall through to the server's own receipt */
        }
      }
      try {
        const view = await latestInstallationAttempt(plan.plan_id);
        if (projectRef.current !== selected) return;
        if (view) {
          rememberInstallationAttempt(plan.plan_id, view.attempt_id);
          setAttempt(view);
        }
      } catch {
        /* a plan with no attempt is a plan that was never executed */
      }
    }
  }, [projectId]);
  const attemptId = attempt?.attempt_id ?? '';
  const attemptFinished = attempt?.terminal ?? true;
  useEffect(() => {
    if (!attemptId || attemptFinished) return;
    const timer = window.setInterval(() => {
      void fetchInstallationAttempt(attemptId).then(setAttempt).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [attemptId, attemptFinished]);
  useEffect(() => { setPlans([]); void loadPlans().catch(error => say(String(error), 'bad')); }, [loadPlans, say]);

  useEffect(() => { void loadEnvironment(); void loadCatalog(); void loadReadiness(); void loadRuntime(); void loadVoice(); void loadManaged(); },
    [loadEnvironment, loadCatalog, loadReadiness, loadRuntime, loadVoice, loadManaged, refreshEpoch]);
  useEffect(() => {
    void loadStorage();
    const refresh = () => {
      void loadStorage(); void loadEnvironment(); void loadReadiness();
      void loadPlans().catch(error => say(String(error), 'bad'));
    };
    window.addEventListener('shine-storage-changed', refresh);
    return () => window.removeEventListener('shine-storage-changed', refresh);
  }, [loadStorage, loadPlans, loadEnvironment, loadReadiness, say, refreshEpoch]);

  async function approveAndExecute(plan: PlanRef, label: string): Promise<SetupExecutionResult> {
    const selected = projectRef.current;
    const result = await approveAndExecuteSetupPlan(plan, () => {
      if (!mounted.current || projectRef.current !== selected) throw new Error('作品已切换，操作已停止。已提交的操作请查看尝试记录。');
    }, { wait: false });
    if (!isQueuedInstallation(result)) {
      say(`${label} 执行完成：${result.status}`);
      return result;
    }
    const queued = result;
    rememberInstallationAttempt(plan.plan_id, queued.attempt_id);
    setAttempt({
      attempt_id: queued.attempt_id,
      plan_id: queued.plan_id ?? plan.plan_id,
      plan_hash: queued.plan_hash,
      stage: queued.stage,
      status: queued.status,
      confirmed_bytes: 0,
      total_bytes: 0,
      speed_bps: 0,
      retry_count: 0,
      cancel_requested: false,
      pause_requested: false,
      terminal: false,
      resumable: true,
    });
    say(`${label} 已排队（尝试 ${queued.attempt_id.slice(0, 8)}…）。进度、暂停和取消在下方「安装进度」里。`);
    return queued;
  }

  async function attemptAction(action: 'pause' | 'resume' | 'cancel'): Promise<void> {
    if (!attempt) return;
    setBusy(true);
    try {
      const view = action === 'pause' ? await pauseInstallationAttempt(attempt.attempt_id)
        : action === 'resume' ? await resumeInstallationAttempt(attempt.attempt_id)
          : await cancelInstallationAttempt(attempt.attempt_id);
      setAttempt(view);
      say(action === 'cancel'
        ? '已请求取消：服务器会在安全点停下。已下载的字节和已校验的模型都保留，不会重下。'
        : action === 'pause'
          ? '已请求暂停：服务器会在安全点停下，已确认的字节保留。'
          : '已继续：从已确认的字节接着下载。');
    } catch (error) {
      say((error as Error).message, 'bad');
    } finally {
      setBusy(false);
    }
  }

  function createInstallPlan() {
    if (existingChoice === 'reuse' && existingScan?.software?.installed) {
      say('已发现本机 ComfyUI 已安装。先完成运行兼容性核验；当前不会创建另一套安装计划。', 'bad');
      return;
    }
    void run(async () => {
      const core = productionCatalogComponents(catalog?.components ?? []).map((component) => component.component_id);
      const result = await api<{ plan_id: string; sha256: string; download_bytes: number; disk_required: number }>(
        '/setup/install-plan', withJson('POST', { components: core, project_id: projectRef.current, disk_free: storage?.free_bytes ?? 0 }));
      setPlans(previous => [...previous, { ...result, kind: 'INSTALL', status: 'AWAITING_APPROVAL' }]);
      return `安装计划 ${result.plan_id} 已创建（下载 ${fmtBytes(result.download_bytes)}，预留 ${fmtBytes(result.disk_required)}）`;
    });
  }

  function createInstanceChain() {
    void run(async () => {
      const result = await api<PlanRef & { complete: boolean }>('/setup/next-instance-plan', withJson('POST', {
        project_id: projectRef.current, instance_name: 'shine-h3', port: 8188, mode: 'local',
      }));
      if (result.complete) return '实例准备已完成并保持停止，请生成受管配置。生产时由 Worker 启动渲染器。';
      setPlans(previous => [...previous.filter(p => p.plan_id !== result.plan_id), { ...result, kind: 'INSTANCE_LIFECYCLE' }]);
      return `下一步：${result.action}，请审核后批准执行`;
    });
  }

  async function runPlan(plan: PlanRef) {
    setBusy(true);
    try { await approveAndExecute(plan, plan.action || plan.kind); }
    catch (error) { say((error as Error).message, 'bad'); }
    finally { setBusy(false); void loadReadiness(); void loadPlans().catch(error => say(String(error), 'bad')); }
  }

  /** Close an install record that stopped (network failure, killed process).
   *  Recovery only rolls back temporary staging bytes; it never re-downloads. */
  function reconcileInstall(plan: PlanRef) {
    const selected = projectRef.current;
    if (!window.confirm('整理这次未完成的安装？\n只核对并回滚中断的临时文件，不会重新下载，也不会删除已经核验的模型。整理后可以重新发起安装。')) return;
    void run(async () => {
      const attempts = await api<Array<{ id: string; status: string }>>(`/comfyops/install-plans/${plan.plan_id}/attempts`);
      const attempt = [...attempts].reverse().find(row => ['FAILED_RECONCILE_REQUIRED', 'SUBMIT_INTENT'].includes(row.status));
      if (!attempt) throw new Error('没有找到可整理的安装尝试。请刷新后再查看安装详情。');
      const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', withJson('POST', {
        action: 'install_reconcile', target_id: attempt.id,
      }));
      if (projectRef.current !== selected) throw new Error('作品已切换，操作已停止。');
      await api(`/comfyops/install-attempts/${attempt.id}/reconcile`, withJson('POST', {
        grant_id: grant.grant_id, snapshot: grant.snapshot,
        evidence: '本机用户整理中断的安装；未重投下载',
      }));
      await loadPlans();
      return '这次安装已整理关闭：临时文件已回滚，没有重新下载。可以重新发起安装。';
    });
  }

  function generateConfiguration() {
    void run(async () => {
      const result = await api<Record<string, unknown>>('/setup/configuration',
        withJson('POST', { project_id: projectRef.current }));
      await loadRuntime();
      await onRefresh();
      return `受管配置已生成：${String((result as { recipe?: string }).recipe ?? '')}`;
    });
  }

  function releaseManagedProcesses() {
    void run(async () => {
      // ComfyUI and the local reviewers belong to this workbench: the operator
      // closes them here and the GPU memory comes back immediately.
      const report = await api<ReleaseReport>('/setup/managed-processes/release', withJson('POST', {}));
      await loadManaged();
      await onRefresh();
      const stopped = report.stopped?.length ?? 0;
      const refused = report.refused?.length ?? 0;
      const busyOnes = (report.refused ?? []).filter(item => item.outcome === 'busy').length;
      if (busyOnes > 0) {
        return `已释放 ${stopped} 个后台进程；${busyOnes} 个正在出片，未停止。`;
      }
      if (refused > 0) {
        return `已释放 ${stopped} 个后台进程；${refused} 个无法确认归属，未停止（见下方列表）。`;
      }
      return stopped > 0 ? `已关闭 ${stopped} 个后台进程，显存已释放。` : '当前没有需要关闭的本机后台进程。';
    });
  }

function activateRuntime() {    const candidate = runtime?.candidate;
    if (!candidate || candidate.project_id !== projectRef.current) return;
    void run(async () => {
      const result = await api<RuntimeState>('/setup/runtime/activate', withJson('POST', {
        project_id: candidate.project_id, recipe_sha256: candidate.recipe_sha256,
      }));
      setRuntime(result); await loadReadiness(); await onRefresh();
      return '运行配置已激活，API 和 Worker 将使用同一份配置。服务与真实推理仍需独立核验。';
    });
  }

  function validateServices() {
    const candidate = runtime?.candidate;
    const project = projectRef.current;
    if (!candidate || !project) return;
    void run(async () => {
      // The recipe is machine-wide; the workflows being validated belong to the
      // project currently open, even when the recipe was created elsewhere.
      const result = await api<{ status: string; node_count: number; workflow_profiles: string[];
        review_models: Record<string, string> }>('/setup/services/validate', withJson('POST', {
        project_id: project, recipe_sha256: candidate.recipe_sha256,
      }));
      await loadRuntime(); await loadReadiness();
      return `服务与工作流验证完成：${result.status} · ${result.workflow_profiles.length} 个工作流 · ${result.node_count} 个节点 · 审片 ${Object.values(result.review_models).join(' / ')}`;
    });
  }

  function registerProfiles() {
    void run(async () => {
      const result = await api<{ profiles: Array<{ name: string; status: string }> }>('/setup/profiles',
        withJson('POST', { project_id: projectRef.current }));
      // Refresh the shared workflow-profile state so the queue/series template
      // pickups on this page update immediately, without a manual reload.
      await onRefresh();
      return result.profiles.map((p) => `${p.name}: ${p.status}`).join('、');
    });
  }

  function validateProfiles() {
    void run(async () => {
      // Registration alone proves nothing: only the real renderer can say
      // whether the installed nodes and inputs match this project's graph.
      const result = await api<{ profiles: Array<{ name: string; status: string; validated: boolean;
        code: string | null; validated_against: { missing_nodes?: string[]; missing_input_count?: number } | null }> }>(
        '/setup/profiles/validate', withJson('POST', { project_id: projectRef.current }));
      await onRefresh(); await loadVoice();
      const lines = result.profiles.map((p) => {
        if (!p.validated) return `${p.name}: 验证未通过（${p.code ?? '未知原因'}）`;
        const missing = p.validated_against?.missing_nodes ?? [];
        const inputs = p.validated_against?.missing_input_count ?? 0;
        return `${p.name}: 验证通过（缺节点 ${missing.length} · 缺输入 ${inputs}）`;
      });
      return lines.join('、');
    });
  }

  function detectEnvironment() {
    void run(async () => {
      setChecked(false);
      setExistingResolved(false); setExistingScan(null); setImportResult('');
      setResolvedMode('none');
      const [environment, ready, state, disk, existing] = await Promise.all([
        api<EnvReport>('/setup/environment'), api<Readiness>('/setup/readiness'),
        api<RuntimeState>('/setup/runtime'), api<DeploymentStorage>('/setup/storage'),
        api<{ installations: string[] }>('/setup/existing-installations'),
      ]);
      setEnv(environment); setReadiness(ready); setRuntime(state); setStorage(disk);
      setExistingRoots(existing.installations);
      setExistingChoice(existing.installations.length ? 'reuse' : 'new');
      setExistingPath(existing.installations[0] ?? '');
      setExistingResolved(existing.installations.length === 0);
      setChecked(true);
      return environment.blockers.length
        ? '检测完成，请先处理下方提示的硬件或基础工具问题。'
        : existing.installations.length ? '发现已有 ComfyUI，请先检查可复用的模型。'
          : '常见目录检测完成。如已有 ComfyUI，可手动指定目录；未发现不代表未安装。';
    });
  }

  function inspectExisting() {
    void run(async () => {
      setExistingScan(null); setExistingResolved(false); setImportResult('');
      setResolvedMode('none');
      const result = await api<ExistingScan>('/setup/existing-installations/inspect',
        withJson('POST', { path: existingPath }));
      setExistingScan(result);
      return result.software?.installed
        ? `ComfyUI 已安装（${installedVersionText(result.software.version_status)}）；发现 ${result.matches.length} 项权重候选，运行兼容性与节点版本仍待核验。`
        : `目录已识别，发现 ${result.matches.length} 项待哈希校验的模型文件。`;
    });
  }

  function verifyExistingInstallation() {
    if (!existingScan) return;
    setProgress('正在原目录只读核验程序版本和模型哈希；文件不会复制或下载…');
    void run(async () => {
      try {
        const result = await api<ExistingVerification>('/setup/existing-installations/verify', {
          ...withJson('POST', { path: existingScan.root, snapshot: existingScan.snapshot }),
          signal: AbortSignal.timeout(3_600_000),
        });
        const verifiedSet = new Set(result.verified.map(row => `${row.component_id}:${row.relative_path}`));
        const resultRows = [...result.verified, ...result.rejected];
        setExistingScan(previous => previous ? ({
          ...previous,
          matches: previous.matches.map(row => {
            const key = `${row.component_id}:${row.relative_path}`;
            return { ...row, status: verifiedSet.has(key)
              ? 'INSTALLED_HASH_MATCH'
              : resultRows.find(item => `${item.component_id}:${item.relative_path}` === key)?.status ?? 'HASH_MISMATCH_OR_CHANGED' };
          }),
        }) : previous);
        const recovered = result.verification_reused ? '已恢复此前完成的核验结果，未重复读取权重。' : '';
        const message = `${recovered}已确认 ComfyUI 已安装（${installedVersionText(result.software.version_status)}）；${fmtBytes(result.verified_bytes)} 权重哈希匹配，${result.rejected.length} 项未通过。原目录未修改、未复制、未下载。当前还需核验运行环境和节点版本。`;
        setExistingResolved(true); setResolvedMode('installed'); setImportResult(message);
        return message;
      } finally {
        setProgress('');
      }
    });
  }

  function reuseExisting() {
    if (!existingScan || !storage) return;
    if (!window.confirm(`校验并复用已有模型？\n来源：${existingScan.root}\n目标：${storage.root}\n最多复制 ${fmtBytes(existingScan.candidate_bytes)}；仅哈希与固定清单一致的文件进入缓存。原安装保持不变，不会下载或启动服务。`)) return;
    void run(async () => {
      const result = await api<ExistingModelImport>(
        '/setup/existing-installations/import-models', { ...withJson('POST', {
          path: existingScan.root, snapshot: existingScan.snapshot,
        }), signal: AbortSignal.timeout(3_600_000) });
      const importedSet = new Set(result.imported.map(row => `${row.component_id}:${row.relative_path}`));
      setExistingScan(previous => previous ? ({ ...previous,
        matches: previous.matches.map(row => ({ ...row, status: importedSet.has(`${row.component_id}:${row.relative_path}`)
          ? 'CACHED' : 'HASH_MISMATCH_OR_CHANGED' })),
      }) : previous);
      const message = `已校验并缓存 ${fmtBytes(result.reused_bytes)}，${result.rejected.length} 项哈希不匹配，不能按固定版本复用。${result.rejected.map(row => `${row.component_id}/${row.relative_path}`).join('、')} 后续只下载缓存中缺少的组件。`;
      setImportResult(message); setExistingResolved(true);
      setResolvedMode('cached');
      return message;
    });
  }

  function installOptionalReviewers() {
    const selected = projectRef.current;
    const reviewers = (catalog?.components ?? []).filter(component => component.kind === 'review-model');
    if (!selected || !storage || !reviewers.length) return;
    const bytes = reviewers.reduce((sum, component) => sum + component.bytes, 0);
    if (!window.confirm(`补装建议审片模型？\n每类只安装 1 个模型包（画面检查、语音检查），约 ${fmtBytes(bytes)}；不会替换 H3 权重，也不会改动已冻结的作品设置。它们只提供建议，不阻断出片。`)) return;
    void run(async () => {
      const plan = await api<PlanRef>('/setup/install-plan', withJson('POST', {
        components: reviewers.map(component => component.component_id), project_id: selected,
        disk_free: storage.free_bytes,
      }));
      await approveAndExecute({ ...plan, kind: 'INSTALL' }, '补装建议审片模型');
      await Promise.allSettled([loadRuntime(), loadReadiness(), loadPlans()]);
      return '建议审片模型的安装计划已提交。装好后可在「验证服务与工作流」里确认它们是否就绪；缺失仍不阻断 H3 出片。';
    });
  }

  function prepareAutomatically() {
    if (!projectId || !checked || !storage || storage.requires_selection || !existingResolved) return;
    if (existingChoice === 'reuse' && existingScan?.software?.installed) {
      say('已确认现有 ComfyUI 与权重安装。当前向导尚不能安全接管这套外部运行环境，因此不会创建新安装计划或重复下载；请先在专业设置核验 Python、节点修订版本和服务兼容性。', 'bad');
      return;
    }
    const selected = projectId;
    const productionComponents = productionCatalogComponents(catalog?.components ?? []);
    const productionBytes = productionComponents.reduce((sum, component) => sum + component.bytes, 0);
    if (!window.confirm(`准备本机 H3 生产环境？\n目录：${storage.root}\n本次只包含 H3 出片所需程序和权重，约 ${fmtBytes(productionBytes)}；ASR/VLM 仅用于建议审片，不会自动下载。${includeImageCapability ? '\n图片素材生成（Qwen Image）是独立能力与独立安装计划，会在计划回执里单列，不并入这次 H3 下载。' : '\n不安装图片生成能力：素材全部由你上传，正式视频生产不受影响。'}\n已校验复用的模型与缓存不会重复下载。\n将独立安装缺少的固定版本组件，保留原 ComfyUI，自动登记工作流、生成并激活配置，短暂启动服务检测后停止。不会生成视频。`)) return;
    void run(async () => {
      const ensureCurrent = () => {
        if (!mounted.current || projectRef.current !== selected) throw new Error('页面或项目已切换，自动准备已停止；已执行记录保留，请重新检测。');
      };
      // Text readiness is a parallel branch, not a precondition of the H3
      // download (J03).  Whatever is missing is reported once the environment
      // is ready, and only the action that really calls a role asks for it.
      let textNote = '';
      try {
        const llm = await api<{ status: string }>('/setup/llm-readiness');
        ensureCurrent();
        if (llm.status !== 'READY') {
          textNote = '；文本模型还有角色未配置：不影响 H3 安装与已可拍镜头的开拍，生成大纲/分镜时会按实际角色提示配置';
        }
      } catch {
        textNote = '；文本模型就绪状态暂时读不到：不影响 H3 安装';
      }
      try {
        setProgress('检查下载网络…');
        const network = await api<Connectivity>('/setup/connectivity');
        setConnectivity(network);
        if (network.blocked.length) throw new Error(`下载源无法连接：${network.blocked.join('、')}。请检查网络后重新检测。`);
        ensureCurrent();
        const pending = await api<Array<{ id: string; sha256: string; status: string; payload: { deployment?: unknown; kind?: string } }>>(
          `/comfyops/install-plans?project_id=${selected}`);
        if (pending.some(p => (p.payload.deployment || p.payload.kind === 'INSTANCE_LIFECYCLE')
          && !['FILES_VERIFIED_PENDING_RUNTIME', 'STOPPED', 'MOCK_VERIFIED', 'CANCELLED', 'RECONCILED_NO_RETRY'].includes(p.status))) {
          throw new Error('已有未完成的安装记录，请展开安装详情继续原计划或对账；不会新建重复安装。');
        }
        const ready = await api<Readiness>('/setup/readiness');
        if (!ready.layers.some(layer => layer.layer === 'L2_files_verified' && layer.status === 'READY')) {
          setProgress('准备 H3 出片所需程序与权重；ASR/VLM 建议审片模型不在本次下载内…');
          const plan = await api<PlanRef>('/setup/install-plan', withJson('POST', {
            components: productionComponents.map(component => component.component_id), project_id: selected,
            disk_free: storage.free_bytes, include_image_capability: includeImageCapability,
          }));
          ensureCurrent();
          const queued = await approveAndExecute({ ...plan, kind: 'INSTALL' }, '下载安装');
          ensureCurrent();
          if (isQueuedInstallation(queued)) {
            // The install is a persistent task now: the automatic chain waits
            // for its real stage instead of assuming it already finished.
            await waitForInstallationAttempt(queued.attempt_id, view => setAttempt(view));
            ensureCurrent();
          }
        }
        let complete = false;
        for (let step = 0; step < 8; step += 1) {
          ensureCurrent();
          setProgress('自动准备运行环境…');
          const plan = await api<PlanRef & { complete: boolean }>('/setup/next-instance-plan', withJson('POST', {
            project_id: selected, instance_name: 'shine-h3', port: 8188, mode: 'local',
          }));
          if (plan.complete) { complete = true; break; }
          ensureCurrent();
          await approveAndExecute({ ...plan, kind: 'INSTANCE_LIFECYCLE' }, '准备运行环境');
        }
        if (!complete) throw new Error('环境准备步骤超过预期，已停止，请检查安装详情。');
        ensureCurrent();
        setProgress('登记工作流并生成配置…');
        await api('/setup/profiles', withJson('POST', { project_id: selected }));
        ensureCurrent();
        await api('/setup/configuration', withJson('POST', { project_id: selected }));
        const state = await api<RuntimeState>('/setup/runtime');
        setRuntime(state);
        if (state.candidate?.project_id !== selected) throw new Error('没有匹配的运行配置，请检查安装详情。');
        const body = { project_id: selected, recipe_sha256: state.candidate.recipe_sha256 };
        ensureCurrent();
        setProgress('启动服务并检测工作流，完成后自动停止…');
        const validation = await api<{ status: string }>('/setup/services/validate', {
          ...withJson('POST', body), signal: AbortSignal.timeout(3_600_000),
        });
        if (validation.status !== 'READY') throw new Error(`服务检测未通过：${validation.status}。配置尚未激活。`);
        ensureCurrent();
        await api('/setup/runtime/activate', withJson('POST', body));
        await onRefresh();
        return '环境已配置并通过服务检测，可以回到制片。真实 H3 生成效果将在实际任务中验证' + textNote + '。';
      } finally {
        setProgress(''); setChecked(false);
        await Promise.allSettled([loadRuntime(), loadReadiness(), loadPlans()]);
      }
    });
  }

  const modelCards = buildModelCards(checked, catalog, existingScan, existingResolved, importResult, resolvedMode);
  const existingComfyInstalled = existingChoice === 'reuse' && !!existingScan?.software?.installed;

  return (
    <section className="card section-card" id="setup">
      <div className="section-heading">
        <div>
          <div className="section-kicker">00 / 制作环境</div>
          <h2>制作环境</h2>
          <p>先检测本机：不下载、不启动，只需要看结论和下一步。</p>
        </div>
        <div className="section-icon"><Sparkles size={17} /></div>
      </div>

      <LLMSetupPanel onChanged={onRefresh} />
      {notice && <div className={notice.kind === 'ok' ? 'ok-line' : 'error-line'}>{notice.text}</div>}

      <div className="settings-block">
        <button className="btn-secondary" disabled={busy} onClick={detectEnvironment}>{checked ? '重新检测' : '检测本机'}</button>
        {!checked && <p role="status">还没有检测结果。检测是只读的：不会下载、复制或启动任何东西，也不需要项目。</p>}
        {checked && <>
          <div className="env-summary-grid">
            <div className="env-summary-item"><b>基础环境</b><span>{env?.blockers.length ? '有阻断项（见下方提示）' : '满足制作要求'}</span></div>
            <div className="env-summary-item"><b>安装空间</b><span>{storage ? `${storage.root} · 可用 ${storage.free_gb} GB` : '读取中'}</span></div>
            <div className="env-summary-item"><b>生成服务</b><span>{runtime?.status === 'ACTIVE' && runtime.services_verified ? '服务验证通过' : '待配置/验证'}</span></div>
            <div className="env-summary-item"><b>文本模型</b><span>在本页上方配置；不需要先安装 H3</span></div>
            <div className="env-summary-item"><b>语音能力</b><span>{voiceSummary(voice)}</span></div>
            <div className="env-summary-item"><b>真实出片</b><span>只能由真实生产证据更新；Mock/演示不改变它</span></div>
          </div>
          {env?.blockers.length ? <p role="status">{env.blockers.map(blockerText).join('；')}。基础工具可重新运行「一键安装并启动.cmd」补齐；显存、内存或磁盘不足需要先处理。</p> :
          <p role="status">{runtime?.status === 'ACTIVE' && runtime.services_verified ? '当前配置已激活，服务已核验。' : '工作台尚未配置就绪；这不代表本机没有安装 ComfyUI 或模型。'}</p>}
          <VariantRecommendationCard />
          <h3>本机已有模型</h3>
          <p className="env-model-note">画面检查和语音转写只提供建议；缺失时留待人工复核，不阻断 H3 出片。默认安装只准备 H3 所需组件，不会自动下载 ASR/VLM 审片模型。</p>
          <div className="env-model-grid">
            {modelCards.map((card) => (
              <div className="env-model-card" key={card.key}>
                <b>{card.label}</b>
                <span>{card.components.map((component) => component.component_id).join('、')}</span>
                <em className={card.state.tone}>{card.state.text}</em>
                {card.detail && <small>{card.detail}</small>}
              </div>
            ))}
  </div>
          <button className="btn-secondary" disabled={busy} onClick={installOptionalReviewers}>补装建议审片模型（可选）</button>
          <fieldset className="setup-existing" disabled={busy}>
            <legend>已有 ComfyUI 与模型复用</legend>
            <p>{existingRoots.length ? `发现 ${existingRoots.length} 个已有安装。` : '常见目录未发现安装，可手动指定；不会遍历整个磁盘。'}</p>
            {existingRoots.map(root => <p key={root}><button className="btn-secondary" onClick={() => {
              setExistingPath(root); setExistingChoice('reuse'); setExistingResolved(false);
              setExistingScan(null); setImportResult(''); setResolvedMode('none');
            }}>{root}</button></p>)}
            <label><input type="radio" name="existing-comfy" checked={existingChoice === 'reuse'} onChange={() => {
              setExistingChoice('reuse'); setExistingResolved(false);
            }} />检查并复用已有模型</label>
            <label><input type="radio" name="existing-comfy" checked={existingChoice === 'new'} onChange={() => {
              setExistingChoice('new'); setExistingResolved(true);
            }} />新建独立运行环境（会安装缺少的程序和模型）</label>
            {existingChoice === 'reuse' && <>
              <label className="existing-path">已有 ComfyUI 目录<input value={existingPath} onChange={e => {
                setExistingPath(e.target.value); setExistingScan(null); setExistingResolved(false); setImportResult('');
              setResolvedMode('none');
              }} placeholder="包含 main.py 的目录或整合包目录" /></label>
              <button className="btn-secondary" disabled={!existingPath.trim()} onClick={inspectExisting}>检查已有目录</button>
              {existingScan && <>
                <p>已识别：{existingScan.root} · 扫描 {existingScan.scanned_files} 个文件。</p>
                {existingScan.software?.installed && <p role="status">
                  ComfyUI 已安装：{installedVersionText(existingScan.software.version_status)}。
                  运行环境 {existingScan.software.runtime?.python_executable ? '检测到 Python 程序，版本兼容性待核验' : '未检测到随包 Python'}；
                  节点修订版本{existingScan.software.node_revisions_verified ? '已核验' : '待核验'}。
                </p>}
                <p>候选可复用 {existingScan.matches.length} 项（{fmtBytes(existingScan.candidate_bytes)}，{existingResolved ? '哈希结果见下方' : '待哈希校验'}）；未找到固定版本文件 {existingScan.missing.length} 项。</p>
                <ul>{existingScan.matches.map(row => <li key={`${row.component_id}:${row.relative_path}`}>{row.component_id} / {row.relative_path}：{row.status === 'INSTALLED_HASH_MATCH' ? '已安装，哈希匹配' : row.status === 'CACHED' ? '已校验并缓存' : row.status === 'HASH_MISMATCH' || row.status === 'HASH_MISMATCH_OR_CHANGED' ? '哈希不匹配或文件已变化' : existingResolved ? '哈希结果待确认' : '待哈希校验'}</li>)}</ul>
                <details><summary>查看未找到的固定版本模型</summary><ul>{existingScan.missing.map(row =>
                  <li key={`${row.component_id}:${row.relative_path}`}>{row.component_id} / {row.relative_path} · {fmtBytes(row.size)}</li>)}</ul></details>
                <button className="btn-secondary" disabled={existingResolved}
                  onClick={existingScan.software?.installed ? verifyExistingInstallation : reuseExisting}>
                  {existingScan.software?.installed ? '确认已安装并核验' : '校验并复用匹配模型'}
                </button>
              </>}
              {importResult && <p role="status">{importResult}</p>}
            </>}
            <p>发现已安装的 ComfyUI 时，会在原目录只读核验版本和模型哈希，不复制、覆盖或下载已安装文件。若现有 Python 或节点兼容性没有核验通过，工作台会停止准备，避免重复安装。外部链接与 extra_model_paths 指向的模型目录暂不自动读取。</p>
          </fieldset>
          <p className="env-next-note">下一步只有一个主动作：检测 → 确认已安装的软件和权重 → 核验兼容性 → 验证服务 → 进入制作。只有明确选择新建独立环境时才会准备另一套程序。</p>
          <p>检测不会下载模型或提交视频。下载前会显示目录、体积与安装范围，由你确认。</p>
          {existingComfyInstalled && <p role="status">{existingResolved
            ? '已确认本机软件与权重安装；当前版本尚不能直接接管外部目录，因此自动安装已停用，不会重复下载。请先在专业设置完成运行兼容性检查。'
            : '已发现本机 ComfyUI。先核验原目录的软件版本与模型哈希；确认之前不会另行下载或复制。'}</p>}
          <button className="btn-secondary" disabled={busy || !projectId || !catalog?.components.length || !!env?.blockers.length || !storage || storage.requires_selection
            || !existingResolved
            || existingComfyInstalled
            || (runtime?.status === 'ACTIVE' && runtime.services_verified && runtime.candidate?.project_id === projectId)}
            onClick={prepareAutomatically}>{existingComfyInstalled ? '已有安装等待兼容性验证' : runtime?.status === 'ACTIVE' && runtime.services_verified && runtime.candidate?.project_id === projectId ? '本项目环境已配置' : '下载缺项并自动配置'}</button>
          {!projectId && <p>先在下方给项目起个名字，即可开始自动配置。</p>}
          {storage?.requires_selection && <p>请选择下载安装目录：<a href="#deployment-storage">选择目录</a></p>}
        </>}
        {progress && <p role="status">{progress}</p>}
      </div>

      <details><summary>查看检测与安装详情（高级）</summary>
      <div className="settings-grid">
        <div className="settings-block">
          <h3>1 · 环境检查</h3>
          {!env ? <div className="empty-inline">读取中…</div> : <>
            <div className="worker-row"><div><b>GPU</b><span>{String(env.gpu?.name ?? '—')} · {String(env.gpu?.vram_mb ?? '—')} MiB · 驱动 {String(env.gpu?.driver_version ?? '—')}</span></div></div>
            <div className="worker-row"><div><b>内存/磁盘</b><span>{env.ram?.ram_mb ?? '—'} MB · 磁盘可用 {env.disk?.free_gb ?? '—'} GB（安装目录 {env.deployment_dir?.path ?? env.data_dir?.path ?? '—'}）</span></div></div>
            <div className="worker-row"><div><b>端口 {env.port?.port ?? '—'}</b><span>{env.port?.status === 'OK' ? '可用' : '被占用'}</span></div></div>
            <table className="setup-tools">
              <tbody>
                {Object.entries(env.tools ?? {}).map(([name, info]) => (
                  <tr key={name}>
                    <td>{TOOL_LABELS[name] ?? name}</td>
                    <td>{info.present ? info.version : '缺失'}</td>
                    <td className={info.present && info.at_floor ? 'ok-cell' : 'bad-cell'}>
                      {info.present ? (info.at_floor ? '达标' : '版本过低') : '可由引导安装'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {env.blockers.length > 0 && <div className="guardrail-note">
              本机阻断项：{env.blockers.map((b) => b.code + (b.tool ? `(${b.tool})` : '')).join('、')}。
              可安装项运行：<code>pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1</code>
            </div>}
          </>}
        </div>

        <div className="settings-block">
          <h3>1b · 上游可达性（下载前必查）</h3>
          {!connectivity ? <div className="empty-inline">尚未检查。下载固定物料之前请先检查一次。</div> : <>
            <p className="guardrail-note">
              {connectivity.checked_at} · 结论 <b>{label(serviceStatusLabels, connectivity.status)}</b>
            </p>
            <table className="setup-tools">
              <tbody>
                {connectivity.upstreams.map((row) => (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td>{row.host}</td>
                    <td className={row.status === 'OK' ? 'ok-cell' : 'bad-cell'}>
                      {row.status === 'OK'
                        ? (row.http_status === 404 ? '可达（根路径 404 属正常）' : `可达 ${row.http_status}`)
                        : row.status === 'DEGRADED' ? `服务异常 ${row.http_status}` : `不可达（${row.error}）`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {connectivity.blocked.length > 0 && <div className="guardrail-note">
              阻断：{connectivity.blocked.join('、')}。先解决代理/VPN/防火墙，再回到这里复检；否则下载会在中途失败。
            </div>}
          </>}
          <div className="inline-form">
            <button className="btn-secondary" disabled={busy} onClick={checkConnectivity}>检查上游可达性</button>
          </div>
        </div>

        <div className="settings-block">
          <h3>2 · 部署清单（固定版本）</h3>
          {!catalog ? <div className="empty-inline">读取中…</div> : <>
            <p className="route-row">
              {catalog.catalog_id} v{catalog.catalog_version}（{catalog.generated}）· 总量 {fmtBytes(catalog.total_bytes)}
            </p>
            {catalog.download_breakdown && <p className="guardrail-note">
              完整目录 {fmtBytes(catalog.download_breakdown.complete_catalog_bytes)}
              {' '}· 默认生产组件 {fmtBytes(catalog.download_breakdown.default_production_bytes)}
              {' '}· 可选审片组件 {fmtBytes(catalog.download_breakdown.optional_review_bytes)}
              {catalog.download_breakdown.optional_review_components.length > 0
                && '（' + catalog.download_breakdown.optional_review_components.join('、') + '，不自动下载）'}
            </p>}
            <div className="setup-components">
              {catalog.components.map((component) => (
                <div className="worker-row" key={component.component_id}>
                  <div>
                    <b>{component.component_id}</b>
                    <span>{component.kind} · {component.revision.slice(0, 12)} · {fmtBytes(component.bytes)} · {component.license_id}</span>
                  </div>
                  <small>{component.destination_family ?? component.role ?? ''}</small>
                </div>
              ))}
            </div>
          </>}
          <p className="guardrail-note">安装仍走既有审批链：每张计划需要人工一次性授权，哈希不符即拒绝。</p>
        </div>

        <div className="settings-block">
          <h3>3 · 计划与批准</h3>
          <p className="guardrail-note">下载与安装到：{storage?.root ?? '读取中…'} · 可用 {storage?.free_gb ?? '—'} GB。
            <a href="#deployment-storage">修改下载安装目录</a>
            {storage?.requires_selection && ' 请先选择非系统盘目录，才能自动安装。'}
          </p>
          <div className="inline-form">
            <button className="btn-secondary" disabled={busy || !projectId || !storage || storage.requires_selection} onClick={createInstallPlan}>生成安装计划</button>
            <button className="btn-secondary" disabled={busy || !projectId} onClick={createInstanceChain}>生成实例链计划</button>
            <button className="btn-secondary" disabled={busy || !projectId} onClick={registerProfiles}>登记工作流 Profile</button>
            <button className="btn-secondary" disabled={busy || !projectId} onClick={validateProfiles}>验证本项目工作流</button>
            <button className="btn-secondary" disabled={busy || !projectId} onClick={generateConfiguration}>生成受管配置</button>
            <button className="btn-secondary" disabled={busy || runtime?.candidate?.project_id !== projectId}
              onClick={validateServices}>验证服务与工作流</button>
            <button className="btn-secondary" disabled={busy || runtime?.candidate?.project_id !== projectId}
              onClick={activateRuntime}>确认并激活运行配置</button>
          </div>
          {!projectId && <p className="guardrail-note">计划生成、Profile 登记、配置生成与激活需要先创建一个项目（本页下方即可创建）；环境检查、上游可达性与部署清单无需项目。</p>}
          <p className="guardrail-note">验证会短暂启动已安装的 ComfyUI、VLM 和 ASR，读取真实节点/工作流契约并探测审片协议，完成后停止；不加载 H3 权重，也不提交镜头。</p>
          <p className="guardrail-note">「验证本项目工作流」只登记并核验本项目自己的 H3 Ref2VA Profile，并把结果写回该 Profile；核验只比对真实节点与输入，不代表已经生成过画面。</p>
          {runtime && <p className="guardrail-note setup-runtime-state">运行配置：{RUNTIME_LABELS[runtime.status] ?? runtime.status} · 当前模式：{label(providerLabels, runtime.render_provider)}
            {runtime.candidate?.project_id === projectId && <> · 待确认配方：<code>{runtime.candidate.recipe_sha256}</code></>}
            {runtime.service_validation && <> · 服务契约：{label(serviceStatusLabels, runtime.service_validation.status)}
              {runtime.service_validation.validated_at ? ` · ${runtime.service_validation.validated_at}` : ''}
              {runtime.service_validation.blockers?.length ? ` · ${runtime.service_validation.blockers.join('、')}` : ''}</>}
            {runtime.error && <> · {runtime.error}</>}
          </p>}
          <div className="settings-block managed-processes">
            <h3>本机后台进程（ComfyUI / 审片服务）</h3>
            <p className="module-guide">这些后台由本工作台启动，模型会一直占着显存。不用时可以在这里一键关闭并释放内存；正在出片的进程不会被强行中断。</p>
            {managed.length === 0 ? <div className="empty-inline">没有记录到本机后台进程。</div> : <div className="worker-row-list">
              {managed.map(item => (
                <div className="worker-row" key={`${item.kind}-${item.id ?? item.pid}`}>
                  <div>
                    <b>{item.kind === 'renderer' ? 'ComfyUI 生成服务' : '本地审片服务'}</b>
                    <span>编号 {String(item.id ?? '—').slice(0, 8)}… · {MANAGED_STATE_LABELS[item.state] ?? item.state}
                      {item.port ? ` · 端口 ${item.port}` : ''}{item.pid ? ` · pid ${item.pid}` : ''}
                      {item.lease_status ? ` · 占用 ${item.lease_status}` : ''}
                      {item.job_status ? ` · 镜头 ${item.job_status}` : ''}</span>
                  </div>
                </div>
              ))}
            </div>}
            <button className="btn-primary" disabled={busy} onClick={releaseManagedProcesses}>
              关闭本机后台并释放显存
            </button>
          </div>
          {plans.length > 0 && <div className="setup-plans">
            {plans.map((plan) => (
              <div className="worker-row" key={plan.plan_id}>
                <div><b>{plan.action ?? plan.kind}</b><span>编号 {plan.plan_id.slice(0, 8)}… · {label(prepRowLabels, plan.status)} · 层级 {plan.tier ?? '—'}</span></div>
                {['NEEDS_REVIEW', 'INSTALLING'].includes(plan.status)
                  ? <button className="btn-text" disabled={busy} onClick={() => reconcileInstall(plan)}>整理中断的安装</button>
                  : ['AWAITING_APPROVAL', 'APPROVED'].includes(plan.status)
                    ? <button className="btn-text" disabled={busy} onClick={() => void runPlan(plan)}>
                      {plan.status === 'APPROVED' ? '执行已批准计划' : '批准并执行'}</button>
                    : null}
              </div>
            ))}
          </div>}
          {attempt && <div className="setup-plans">
            <div className="worker-row">
              <div>
                <b>安装进度 · {INSTALL_STAGE_LABELS[attempt.stage] ?? attempt.stage}</b>
                <span>尝试 {attempt.attempt_id.slice(0, 8)}… · {fmtBytes(attempt.confirmed_bytes)} / {fmtBytes(attempt.total_bytes)}
                  {attempt.speed_bps ? ' · ' + fmtBytes(attempt.speed_bps) + '/秒' : ''}
                  {attempt.current_component ? ' · 组件 ' + attempt.current_component : ''}
                  {attempt.current_file ? ' · 文件 ' + attempt.current_file : ''}
                  {attempt.retry_count ? ' · 重试 ' + attempt.retry_count : ''}
                  {attempt.heartbeat_at ? ' · 心跳 ' + attempt.heartbeat_at : ''}</span>
                {attempt.error_code
                  ? <span>错误 {attempt.error_code}：{attempt.error_message ?? ''}</span>
                  : <span className="guardrail-note">字节数来自服务器实际落盘的下载文件；关闭页面只会停止观察，不会停止下载。</span>}
              </div>
              <div className="worker-row-actions">
                {!attempt.terminal && !attempt.pause_requested
                  ? <button className="btn-text" disabled={busy} onClick={() => void attemptAction('pause')}>暂停</button>
                  : null}
                {(attempt.pause_requested || attempt.stage === 'PAUSED' || attempt.stage === 'FAILED_RETRYABLE')
                  ? <button className="btn-text" disabled={busy} onClick={() => void attemptAction('resume')}>继续</button>
                  : null}
                {!attempt.terminal
                  ? <button className="btn-text" disabled={busy} onClick={() => void attemptAction('cancel')}>取消</button>
                  : null}
              </div>
            </div>
          </div>}
          <p className="guardrail-note">安装计划与实例链计划共用既有端点；未批准或哈希变化会直接拒绝。</p>
        </div>

        <div className="settings-block">
          <h3>4 · 就绪检查</h3>
          {!readiness ? <div className="empty-inline">读取中…</div> : <>
            {readiness.layers.map((layer) => {
              const status = String(layer.status);
              const blockers = Array.isArray(layer.blockers) ? layer.blockers : [];
              return (
                <div className="worker-row" key={String(layer.layer)}>
                  <span className={`status-led ${status === 'READY' ? 'mock' : 'offline'}`} />
                  <div>
                    <b>{String(layer.layer)}</b>
                    <span>{label(serviceStatusLabels, status)}{blockers.length ? ` · 待解决：${blockers.map((item) => typeof item === 'string' ? item : JSON.stringify(item)).join('；').slice(0, 160)}` : ''}</span>
                  </div>
                </div>
              );
            })}
            <div className="guardrail-note">
              真实推理层只由真实生产证据更新；Mock/Fake 不会把它标成 READY。
              人工步骤：{readiness.human_steps.join('；')}。
            </div>
          </>}
        </div>
      </div>
      </details>
    </section>
  );
}
