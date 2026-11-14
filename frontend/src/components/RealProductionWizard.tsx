import { CheckCircle2, CircleDashed, Loader2, RefreshCw, TriangleAlert, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api as request, json, timeoutSignal, type SystemStatus } from '../api';
import { LLMSetupPanel } from './LLMSetupPanel';
import {
  approveAndExecuteSetupPlan,
  latestInstallationAttempt,
  waitForInstallationAttempt,
  type SetupPlan,
} from '../setupExecution';

/** 「启用真实制作」单一路径向导。检查顺序和每个接口调用都与专业版
 *  制作环境面板共用同一后端合同；这里只做普通用户能读懂的进度呈现。
 *  已有 ComfyUI 优先识别与复用：只有确认缺少的组件才会下载，绝不默认
 *  重新下载几十 GB 模型。底层八个安装步骤由程序按顺序协调，用户只看
 *  到进度、缺失项和确需授权的操作。 */

type StageId = 'project' | 'llm' | 'hardware' | 'existing' | 'models' | 'workflows' | 'runtime' | 'done';
type StageStatus = 'pending' | 'running' | 'ok' | 'warn' | 'blocked';
type Stage = { id: StageId; title: string; status: StageStatus; note: string };

const STAGE_TITLES: Array<{ id: StageId; title: string }> = [
  { id: 'project', title: '检查当前作品' },
  { id: 'llm', title: '配置核心 LLM 与文本工种' },
  { id: 'hardware', title: '检查本机硬件与基础程序' },
  { id: 'existing', title: '检测已有 ComfyUI 与模型' },
  { id: 'models', title: '核对模型版本与固定配置' },
  { id: 'workflows', title: '登记生成方案并验证本机服务' },
  { id: 'runtime', title: '启用真实制作环境' },
];

const INITIAL_STAGES: Stage[] = STAGE_TITLES.map(({ id, title }) => ({ id, title, status: 'pending', note: '' }));

type EnvFinding = {
  code: string;
  repairability: 'AUTO_REPAIRABLE' | 'USER_ACTION_REQUIRED' | 'HARDWARE_INCOMPATIBLE'
    | 'DETECTION_UNKNOWN' | 'ALREADY_AVAILABLE';
  scopes?: string[];
  scope?: string[];
  message?: string;
  next_action?: string;
  package_id?: string;
  tools?: string[];
  selected_port?: number;
};

type RepairPackage = {
  package_id: string; label: string; download_bytes: number;
  tools: Array<{ tool: string; label: string; version: string; min_version?: string }>;
};

type RepairPlan = {
  plan_id: string; status: 'COMPLETE' | 'NOTHING_TO_REPAIR' | string;
  packages: RepairPackage[]; download_bytes: number;
  authorization: { status: string }; execution: { status: string };
};

type VariantOption = {
  variant_id: string; title: string; acceptance_status: string; status: string;
  usable_in_production: boolean; auto_download_allowed: boolean;
  catalog_id?: string; total_bytes?: number; summary?: string;
};

type VariantRecommendation = {
  tier: string;
  recommended: VariantOption[];
  candidates: VariantOption[];
  blocked: VariantOption[];
};

type EnvReport = {
  blockers: Array<{ code: string; tool?: string; detail?: string; message?: string }>;
  findings?: EnvFinding[];
  repair_plan?: RepairPlan;
  tools?: Record<string, { present: boolean; version: string; at_floor: boolean }>;
  port?: {
    status: string; port: number; selected?: number; identity?: string;
    reusable?: boolean; reason?: string | null;
  };
  tool_packages?: RepairPackage[];
  ram: { status: string; ram_mb?: number; installed_mb?: number | null; note?: string };
  disk: { status: string; free_gb: number }; gpu: Record<string, unknown>;
  advisory_reviews?: Record<string, { status: string; model_id?: string; model_files_present?: number;
    model_files_expected?: number; runtime_ready?: boolean }>;
};
type ExistingScan = {
  root: string; snapshot: string; candidate_bytes: number;
  matches: Array<{ component_id: string; size: number; relative_path?: string }>;
  software?: { installed: boolean; version_status: string; actual_revision?: string | null;
    runtime?: { status: string }; node_revisions_verified?: boolean };
};
type ExistingVerification = { status: string; software: NonNullable<ExistingScan['software']>;
  verified: Array<{ component_id: string; size: number; relative_path?: string }>;
  rejected: Array<{ component_id: string; size: number; relative_path?: string }>;
  missing?: Array<{ component_id: string; relative_path: string }>;
  verified_bytes: number; copied_bytes: number; verification_reused?: boolean; retry_after_ms?: number };
type RuntimeState = {
  status: string; error: string | null; render_provider: string;
  candidate: { project_id: string; recipe_sha256: string } | null;
  service_validation?: { status: string; blockers?: string[]; advisory_reviews?: Record<string, { status: string }> } | null;
};
type Readiness = { layers: Array<{ layer?: string; status?: string }> };
type StorageState = { root: string; requires_selection: boolean; free_bytes: number };
type Catalog = { total_bytes: number; components: Array<{ component_id: string; files: number; role?: string | null }> };

const HARDWARE_BLOCKER_ZH: Record<string, string> = {
  TOOL_MISSING_INSTALLABLE: '缺少必需的基础程序', TOOL_STALE: '基础程序版本过旧',
  TOOL_PACKAGE_UNREPAIRABLE: '基础程序缺少固定下载源',
  GPU_VRAM_BELOW_FLOOR: '显卡可用显存不足', RAM_BELOW_FLOOR: '本机内存不足',
  RAM_BELOW_FLOOR_UNCONFIRMED: '内存容量需要复核（读不到实装容量）',
  RAM_UNREADABLE: '读不到本机物理内存',
  DISK_BELOW_FLOOR: '安装目录空间不足', PORT_OCCUPIED: '服务端口被占用',
  PORT_OCCUPIED_SELECTED_ALTERNATIVE: '服务端口被占用，已改用其他端口',
  DATA_DIR_NOT_WRITABLE: '项目数据目录无法写入', DEPLOYMENT_DIR_NOT_WRITABLE: '安装目录无法写入',
  NVIDIA_SMI_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
  NVIDIA_DRIVER_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
  NVIDIA_SMI_OUTPUT_UNPARSEABLE: '显卡检测结果无法读取，请检查显卡驱动',
  COMMIT_BELOW_FLOOR: '提交内存上限不足（页面文件过小）',
  COMMIT_UNREADABLE: '读不到提交内存上限',
};

const TOOL_NAME_ZH: Record<string, string> = {
  ffmpeg: 'FFmpeg', ffprobe: 'FFprobe', pwsh: 'PowerShell 7', node: 'Node.js', npm: 'npm', uv: 'uv',
};

function findingText(finding: EnvFinding): string {
  if (finding.message) return finding.message;
  const names = (finding.tools ?? []).map((tool) => TOOL_NAME_ZH[tool] ?? tool).join('、');
  const base = HARDWARE_BLOCKER_ZH[finding.code] ?? `环境检测未通过（${finding.code}）`;
  return names ? `${base}：${names}` : base;
}

function blockingScope(finding: EnvFinding, scope: string): boolean {
  return (finding.scopes ?? finding.scope ?? []).includes(scope);
}

const REPAIR_IN_PROGRESS_TEXT: Record<string, string> = {
  SUCCEEDED: '补装完成，正在复查工具版本…',
  PARTIAL: '部分工具补装完成，正在复查…',
  FAILED: '补装没有完成',
  ALREADY_AVAILABLE: '工具已经达标，无需补装',
  PLATFORM_UNSUPPORTED: '当前环境不是 Windows，未执行本机补装',
};

function fmtBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let result = value; let index = 0;
  while (result >= 1024 && index < units.length - 1) { result /= 1024; index += 1; }
  return `${result.toFixed(result >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

const RUNNING_TEXT: Record<string, string> = {
  project: '正在读取当前作品…', hardware: '正在检查显卡、内存、磁盘与基础程序…',
  existing: '正在检测本机已有 ComfyUI 安装…', models: '正在核对模型与固定清单…',
  workflows: '正在登记生成方案并启动服务验证（完成后自动停止）…', runtime: '正在启用真实制作环境…',
};

export function RealProductionWizard({
  mode, projectId, onClose, onFinished, onOpenExpertSetup, onRefresh,
}: {
  mode: 'enable' | 'diagnose';
  projectId: string;
  onClose: () => void;
  onFinished: () => void;
  onOpenExpertSetup: () => void;
  onRefresh: () => Promise<void> | void;
}) {
  const [stages, setStages] = useState<Stage[]>(INITIAL_STAGES);
  const [finished, setFinished] = useState<null | { ok: boolean; summary: string }>(null);
  const [reuseOffer, setReuseOffer] = useState<null | { scans: ExistingScan[]; totalBytes: number }>(null);
  const [repairOffer, setRepairOffer] = useState<null | RepairPlan>(null);
  const [variantOffer, setVariantOffer] = useState<null | VariantOption[]>(null);
  const [reusedBytes, setReusedBytes] = useState(0);
  const [llmRequired, setLlmRequired] = useState(false);
  const [needProject, setNeedProject] = useState(false);
  const cancelled = useRef(false);
  const mounted = useRef(true);
  const operation = useRef(false);
  const currentStage = useRef<StageId>('project');
  const selectedProject = useRef(projectId);
  selectedProject.current = projectId;
  const originalProject = useRef(projectId);
  /** The one H3 variant the person chose; it survives every re-run. */
  const variantChoice = useRef<VariantOption | null>(null);
  /** The port the server selected; never re-hardcoded to 8188. */
  const servicePort = useRef<number | null>(null);
  /** One automatic repair per wizard run: a still-missing tool stops instead of looping. */
  const repairAttempts = useRef(0);
  function check() {
    if (!mounted.current || cancelled.current || selectedProject.current !== originalProject.current) throw new Error('WIZARD_CANCELLED');
  }
  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    check();
    const result = await request<T>(path, { ...init, signal: init?.signal ?? timeoutSignal(60_000) });
    check();
    return result;
  }
  async function safely(action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    try { check(); await action(); }
    catch (error) {
      if (mounted.current && !cancelled.current && selectedProject.current === originalProject.current) {
        stopWith(currentStage.current, `操作未完成：${String(error)}。已提交的操作可能仍在服务端进行；请在专业设置查看记录。重新检查会先读取实际状态。`);
      }
    } finally { operation.current = false; }
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelled.current = true; }; }, []);

  function setStage(id: StageId, patch: Partial<Stage>) {
    if (!mounted.current || cancelled.current || selectedProject.current !== originalProject.current) return;
    currentStage.current = id;
    setStages((current) => current.map((stage) => stage.id === id ? { ...stage, ...patch } : stage));
  }

  function stopWith(id: StageId, note: string) {
    setStage(id, { status: 'blocked', note });
    if (mounted.current && !cancelled.current && selectedProject.current === originalProject.current) setFinished({ ok: false, summary: note });
  }

  async function importReuse(scans: ExistingScan[]): Promise<number> {
    let total = 0;
    for (const scan of scans) {
      const result = await api<{ reused_bytes: number; rejected: Array<{ component_id: string; relative_path: string }> }>(
        '/setup/existing-installations/import-models', {
          ...json('POST', { path: scan.root, snapshot: scan.snapshot }),
          signal: timeoutSignal(3_600_000),
        });
      total += result.reused_bytes;
    }
    return total;
  }

  /** The status endpoint corroborates the activation; it is not the proof.
   *  An unreachable status interface must not undo an installation the server
   *  already committed, and a mode that is really not ready is still named. */
  async function verifyProductionReady(): Promise<{ ok: boolean; note: string }> {
    try {
      const status = await api<SystemStatus>('/system/status');
      if (!['READY', 'RUNNING'].includes(status.production_mode.mode)) {
        return {
          ok: false,
          note: `真实制作暂未就绪（${status.production_mode.code ?? status.production_mode.mode}）：${status.production_mode.detail}`,
        };
      }
      return { ok: true, note: '' };
    } catch (error) {
      return {
        ok: true,
        note: `状态接口暂时读不到（${(error as Error).message}）：安装与启用已经完成，可以稍后重新检查。`,
      };
    }
  }

  async function run() {
    cancelled.current = false;
    setStages(INITIAL_STAGES.map((stage) => ({ ...stage, status: 'pending', note: '' })));
    setFinished(null); setLlmRequired(false); setReuseOffer(null); setRepairOffer(null); setVariantOffer(null);
    setReusedBytes(0); setNeedProject(false);
    const alive = () => !cancelled.current && mounted.current;
    const onlyDiagnose = mode === 'diagnose';

    // 1. 当前作品
    setStage('project', { status: 'running', note: RUNNING_TEXT.project });
    if (!projectId) {
      setNeedProject(true);
      stopWith('project', '真实制作需要一个作品来承载。请先创建作品，再回来启用真实制作。');
      return;
    }
    setStage('project', { status: 'ok', note: '当前作品已就绪。' });

    // Text models and the H3 environment are two independent preparations.
    // A missing text role must never hold the download, the weight check or a
    // single real shot hostage (J03); the role is checked at the action that
    // actually calls it (outline, storyboard, creative script).
    setStage('llm', { status: 'running', note: '检查文本模型与文本工种路由（与 H3 环境并行，不阻断安装）…' });
    try {
      const llm = await api<{ status: string }>('/setup/llm-readiness');
      if (llm.status === 'READY') {
        setStage('llm', { status: 'ok', note: '核心 LLM 配置与连接检查通过；文本生成质量独立验收。' });
      } else {
        setLlmRequired(true);
        setStage('llm', { status: 'warn', note: '文本模型还有角色未配置：不影响本机 H3 安装与已可拍镜头的开拍；生成大纲或分镜时会按实际使用的那一个角色提示配置。' });
      }
    } catch {
      setStage('llm', { status: 'warn', note: '文本模型就绪状态暂时读不到：不影响本机 H3 安装；需要生成文本时再到基础配置检查。' });
    }

    // Read durable state before creating any further intent. Never replay ambiguous work.
    const plans = await api<Array<{ id: string; status: string }>>(`/comfyops/install-plans?project_id=${projectId}`);
    for (const plan of plans) {
      const key = `shine.setupUnconfirmed.${plan.id}`;
      if (localStorage.getItem(key)) {
        if (['FILES_VERIFIED_PENDING_RUNTIME', 'STOPPED', 'RECONCILED_NO_RETRY'].includes(plan.status)) localStorage.removeItem(key);
        else { stopWith('project', '有执行结果未确认（EXECUTION_UNCONFIRMED），请到专业设置对账。'); return; }
      }
    }
    // The plan row is not the truth about the bytes: the attempt that writes
    // this physical target is.  A known running attempt is re-attached here
    // with its own progress, a finished one lets the preparation continue at
    // its own step, and only an attempt that really waits for a person is
    // handed to the expert page (R06).
    const blocked = plans.find(plan => ['INSTALLING', 'SUBMIT_INTENT', 'NEEDS_REVIEW'].includes(plan.status));
    if (blocked) {
      const attempt = await latestInstallationAttempt(blocked.id).catch(() => null);
      if (attempt && !attempt.terminal) {
        if (attempt.stage === 'PAUSED') {
          // A paused attempt waits for a person: it is not a failure and it
          // is not a silent restart either.
          stopWith('project', '这次安装已暂停（PAUSED）：已下载的字节还在，可以继续下载，不会重新执行。到专业设置继续，或回到这里重新检查。');
          return;
        }
        setStage('project', { status: 'running', note: `这次安装还在进行（${attempt.stage}）：${attempt.confirmed_bytes}/${attempt.total_bytes} 字节。继续等待，不会重复执行。` });
        try {
          const view = await waitForInstallationAttempt(attempt.attempt_id, (update) => {
            if (!alive()) return;
            setStage('project', { status: 'running', note: `这次安装还在进行（${update.stage}）：${update.confirmed_bytes}/${update.total_bytes} 字节。继续等待，不会重复执行。` });
          }, { maxTicks: 90 });
          if (!alive()) return;
          setStage('project', { status: 'ok', note: `这次安装已完成（${view.stage}），继续后面的准备。` });
        } catch (error) {
          stopWith('project', `这次安装没有完成：${(error as Error).message}`);
          return;
        }
      } else if (attempt && attempt.terminal && ['NEEDS_REVIEW', 'CANCELLED'].includes(attempt.stage)) {
        stopWith('project', `这次安装在 ${attempt.stage} 停下（${attempt.error_code ?? ''}）：${attempt.error_message ?? '结果未知'}。请到专业设置对账，不能重复执行。`);
        return;
      } else if (attempt && attempt.terminal) {
        setStage('project', { status: 'ok', note: `这次安装已经完成（${attempt.stage}），继续后面的准备。` });
      } else {
        stopWith('project', '存在执行中或需要对账的计划（RECONCILE_REQUIRED）。请到专业设置查看尝试记录，不能重新执行。');
        return;
      }
    }
    const active = await api<RuntimeState>('/setup/runtime');
    if (active.status === 'INVALID_REQUIRES_REVIEW') {
      stopWith('project', `配置需要检查（INVALID_REQUIRES_REVIEW）：${String(active.error)}。请到专业设置处理。`); return;
    }
    if (active.status === 'ACTIVE' && active.candidate?.project_id === projectId && active.service_validation?.status === 'READY') {
      const confirmed = await verifyProductionReady();
      if (!confirmed.ok) { stopWith('project', confirmed.note); return; }
      for (const stage of STAGE_TITLES) setStage(stage.id, { status: 'ok', note: '已有配置与服务验证有效。' });
      setFinished({ ok: true, summary: confirmed.note || '真实制作已就绪；渲染器空闲停止时也无需重新安装。' });
      return;
    }

    // 2. 硬件与基础程序：只读检测 → 分类 → 可自动修复的进入补装计划
    setStage('hardware', { status: 'running', note: RUNNING_TEXT.hardware });
    let env: EnvReport;
    try {
      env = await api<EnvReport>('/setup/environment');
      if (!alive()) return;
      const findings = env.findings;
      if (findings) {
        const bootstrapFindings = findings.filter((finding) => blockingScope(finding, 'BOOTSTRAP'));
        const hardBlockers = bootstrapFindings.filter(
          (finding) => !['AUTO_REPAIRABLE', 'ALREADY_AVAILABLE'].includes(finding.repairability));
        const repairable = bootstrapFindings.filter((finding) => finding.repairability === 'AUTO_REPAIRABLE');
        if (hardBlockers.length) {
          stopWith('hardware', `本机还有需要你处理的问题：${hardBlockers.map(findingText).join('；')}。处理后再重新检查。`);
          return;
        }
        if (repairable.length) {
          const labels = repairable.map((finding) => (finding.tools ?? [])
            .map((tool) => TOOL_NAME_ZH[tool] ?? tool).join(' + ')).filter(Boolean).join('、');
          if (onlyDiagnose) {
            setStage('hardware', { status: 'warn', note: `检测到可自动补装：${labels}。这里只做检查，没有下载或安装；启用真实制作时确认一次即可自动补装。` });
            setFinished({ ok: false, summary: `检查完成：本机缺少 ${labels}，可以在「启用真实制作」里一次确认后自动补装并继续。` });
            return;
          }
          if (repairAttempts.current > 0) {
            stopWith('hardware', `已尝试自动补装，但复查仍显示缺少 ${labels}。已下载的内容会保留，不会重复循环；请到专业设置的「制作环境」查看补装记录并手动处理。`);
            return;
          }
          const plan = env.repair_plan ?? await api<RepairPlan>('/setup/tool-repair-plan');
          if (!alive()) return;
          if (plan.status !== 'COMPLETE' || !plan.packages.length) {
            stopWith('hardware', `缺少 ${labels}，但没有可用的固定下载计划。请手动安装后重新检查。`);
            return;
          }
          setStage('hardware', { status: 'warn', note: `发现可自动补装：${labels}，预计下载 ${fmtBytes(plan.download_bytes)}。确认后使用 Windows 自带 PowerShell 5.1 补装，并自动复查版本。` });
          setRepairOffer(plan);
          return; // 用户确认后由 repairTools() 继续
        }
      } else if (env.blockers.length) {
        // Older servers do not classify findings: name the tools instead of
        // repeating "missing prerequisite" three times.
        const names = env.blockers.map((blocker) => {
          if (blocker.code.startsWith('TOOL_') && blocker.tool) return TOOL_NAME_ZH[blocker.tool] ?? blocker.tool;
          return HARDWARE_BLOCKER_ZH[blocker.code] ?? `环境检测未通过（${blocker.code}）`;
        });
        stopWith('hardware', `本机还缺少必要条件：${names.join('；')}。处理后再重新检查。`);
        return;
      }
      const port = env.port?.selected ?? env.port?.port;
      if (!port) {
        stopWith('hardware', '服务端口状态读取不到，不能安全地准备 ComfyUI。请重新检查。');
        return;
      }
      servicePort.current = port;
      const portNote = env.port?.identity && env.port.identity !== 'FREE'
        ? `端口 ${env.port.port} 当前为 ${env.port.identity}，本次使用 ${port}（不会结束占用端口的程序）。`
        : `服务端口 ${port} 可用。`;
      const ramNote = env.ram?.note === 'HARDWARE_RESERVED_MEMORY'
        ? `系统报告可用 ${env.ram.ram_mb} MB，实装 ${env.ram.installed_mb} MB（差额为硬件保留），按实装容量判定达标。`
        : '';
      const reviewStatus = Object.entries(env.advisory_reviews ?? {}).map(([role, item]) =>
        `${role === 'VLM' ? '画面建议审阅' : role === 'ASR' ? '对白识别建议' : role}：${item.status === 'READY' ? '资源已就绪' : '暂未就绪'}`,
      );
      const advisoryNote = reviewStatus.length
        ? `建议审片检查：${reviewStatus.join('；')}。它们只提供参考，缺失不影响真实制作，未覆盖内容会留待人工复核。`
        : '建议审片与对白识别为可选参考，不会阻止真实制作。';
      setStage('hardware', { status: 'ok', note: `显卡、内存、磁盘与基础程序已通过检查。${ramNote}${portNote}${advisoryNote}` });
    } catch (error) {
      stopWith('hardware', `环境检查失败：${(error as Error).message}`);
      return;
    }

    // 2b. 模型方案：用户选定的变体贯穿安装、实例、方案登记与激活。
    if (!variantChoice.current) {
      const recommendation = await api<VariantRecommendation>('/setup/variant-recommendations').catch(() => null);
      if (!alive()) return;
      const preferred = recommendation?.recommended?.[0];
      if (preferred) {
        variantChoice.current = preferred;
      } else if (recommendation?.candidates?.length) {
        setVariantOffer(recommendation.candidates);
        setStage('models', {
          status: 'warn',
          note: '这台电脑没有可直接投产的标准方案；请先选一套候选方案，再继续准备资源（下载不等于验收）。',
        });
        return; // 用户选择后由 chooseVariant() 继续
      }
    }

    // 3. 已有 ComfyUI 与模型：优先复用，禁止默认重下
    setStage('existing', { status: 'running', note: RUNNING_TEXT.existing });
    const storage = await api<StorageState>('/setup/storage');
    if (!alive()) return;
    if (storage.requires_selection) {
      stopWith('existing', '还没有选择安装目录。请到专业设置的「制作环境」选择一个空间足够的非系统盘目录后重试。');
      return;
    }
    // Search the person's chosen storage root as well as the declared probe
    // roots: a ComfyUI download nested inside it must not be invisible.
    const found = await api<{
      installations: string[]; scan?: { truncated?: boolean; directories_visited?: number };
    }>(`/setup/existing-installations?root=${encodeURIComponent(storage.root)}`);
    if (!alive()) return;
    const scans: ExistingScan[] = [];
    for (const root of found.installations.slice(0, 3)) {
      try {
        const scan = await api<ExistingScan>('/setup/existing-installations/inspect', json('POST', { path: root }));
        if (!alive()) return;
        if (scan.matches.length || scan.software?.installed) scans.push(scan);
      } catch (error) {
        stopWith('existing', `已有目录检查失败：${String(error)}。请到专业设置确认目录，避免漏掉可复用模型。`);
        return;
      }
    }
    const candidateBytes = scans.reduce((sum, scan) => sum + scan.candidate_bytes, 0);
    const installedSoftware = scans.some(scan => scan.software?.installed);
    if (candidateBytes > 0 || installedSoftware) {
      setStage('existing', {
        status: 'warn',
        note: installedSoftware
          ? `确认本机已有 ComfyUI 软件${candidateBytes ? `，并发现约 ${fmtBytes(candidateBytes)} 的权重文件` : ''}。先核验原文件；不能确认运行兼容性前不会复制或下载替代文件。`
          : `发现约 ${fmtBytes(candidateBytes)} 的已有权重文件。下面可校验后复用，避免重复下载。`,
      });
      if (!onlyDiagnose) {
        setReuseOffer({ scans, totalBytes: candidateBytes });
        return; // 用户确认复用后由 continueAfterReuse() 接着跑
      }
    } else {
      setStage('existing', {
        status: 'ok',
        note: found.installations.length
          ? '检测到已有 ComfyUI，但固定清单中的模型不在其中；缺少的组件将在确认后按需下载。'
          : found.scan?.truncated
            ? '在已达到搜索上限的范围内没有找到已有 ComfyUI；这不代表整盘没有。可到专业设置直接指定下载目录。'
            : '未检测到已有 ComfyUI；将在确认后按固定清单下载缺少的组件。',
      });
    }

    await runTail(alive, onlyDiagnose, reusedBytes, storage);
  }

  /** 4–6：模型核对 → 生成方案与服务验证 → 激活。 */
  async function runTail(alive: () => boolean, onlyDiagnose: boolean, reused: number, storage: StorageState,
    adoptedExisting = false) {
    // 4. 模型与固定清单（readiness L2 证据）
    setStage('models', { status: 'running', note: RUNNING_TEXT.models });
    let ready: Readiness;
    try {
      ready = await api<Readiness>('/setup/readiness');
    } catch (error) {
      stopWith('models', `模型清单核对失败：${(error as Error).message}`);
      return;
    }
    if (!alive()) return;
    const l2 = ready.layers.find(layer => layer.layer === 'L2_files_verified');
    if (!l2 || l2.status !== 'READY') {
      if (adoptedExisting) {
        const missing = (l2 as { missing_components?: string[] } | undefined)?.missing_components ?? [];
        stopWith('models', `已确认复用本机 ComfyUI 与 H3 模型，但仍有本地文件需要核验${missing.length ? `：${missing.join('、')}` : '。'}不会因为复用而自动下载新模型。请到专业设置查看缺项；确认要补齐其他组件时再单独审核下载。`);
        return;
      }
      if (onlyDiagnose) {
        stopWith('models', '模型与固定清单尚未核对完成（未校验的文件不会用于真实生成）。请到专业设置的「制作环境」执行下载与校验。');
        return;
      }
      const catalog = await api<Catalog>('/setup/catalog');
      const variant = variantChoice.current;
      const previous = await api<Array<{ id: string; sha256: string; status: string; payload: { deployment?: unknown } }>>(`/comfyops/install-plans?project_id=${projectId}`);
      const pending = previous.filter(p => p.payload.deployment && ['AWAITING_APPROVAL', 'APPROVED'].includes(p.status));
      if (pending.length > 1) throw new Error('MULTIPLE_PENDING_PLANS：请到专业设置选择并核对计划。');
      const plan = pending.length ? { plan_id: pending[0].id, sha256: pending[0].sha256, status: pending[0].status }
        : await api<SetupPlan & { download_bytes: number }>('/setup/install-plan', json('POST', {
          components: catalog.components.filter(c => !['VLM', 'ASR'].includes(c.role ?? '')).map(c => c.component_id),
          project_id: projectId, disk_free: storage.free_bytes,
          ...(variant ? {
            variant_id: variant.variant_id,
            catalog: variant.catalog_id,
            candidate_download_acknowledged: !variant.auto_download_allowed,
          } : {}),
        }));
      if (!alive()) return;
      const expectedDownload = 'download_bytes' in plan ? plan.download_bytes : undefined;
      const downloadText = expectedDownload === undefined
        ? '沿用之前已审核的待处理计划'
        : `本计划预计新下载 ${fmtBytes(expectedDownload)}；ASR/VLM 建议审片资源不包含在内`;
      const variantText = variant ? `\n生成方案：${variant.title}` : '';
      if (!window.confirm(`批准缺少的核心制作组件下载与校验？\n作品：${projectId}${variantText}\n目录：${storage.root}\n计划：${plan.plan_id}\n哈希：${plan.sha256}\n${downloadText}。已复用 ${fmtBytes(reused)}，原安装保持不变。`)) {
        stopWith('models', '已取消下载，没有执行安装计划。需要时可重新检查并审核原计划。');
        return;
      }
      setStage('models', { status: 'running', note: '正在批准、下载并校验缺少的组件…' });
      await approveAndExecuteSetupPlan(plan, check);
      if (!alive()) return;
      ready = await api<Readiness>('/setup/readiness');
      const verified = ready.layers.find(layer => layer.layer === 'L2_files_verified');
      if (!verified || verified.status !== 'READY') {
        stopWith('models', '下载或校验没有全部完成。请到专业设置的「安装详情」查看具体缺失项，处理后重试。');
        return;
      }
    }
    setStage('models', { status: 'ok', note: adoptedExisting
      ? `已复用原目录中哈希校验通过的 H3 权重（${fmtBytes(reused)}）；0 字节复制，0 字节下载。`
      : '模型与固定清单核对一致（哈希校验通过）。' });

    // 5. 运行环境 + 生成方案登记 + 服务验证
    setStage('workflows', { status: 'running', note: RUNNING_TEXT.workflows });
    if (!onlyDiagnose) {
      try {
        if (!adoptedExisting) {
          if (!window.confirm('将按固定部署清单准备受管实例、配置节点和模型绑定，并启动本机服务验证后停止。是否批准这些有序步骤？')) {
            stopWith('workflows', '已取消实例准备，没有执行实例计划。'); return;
          }
          let complete = false;
          const port = servicePort.current;
          if (!port) throw new Error('SERVICE_PORT_UNKNOWN：端口状态读取不到，不能准备实例。');
          const variant = variantChoice.current;
          for (let step = 0; step < 8; step += 1) {
            if (!alive()) return;
            setStage('workflows', { status: 'running', note: `正在准备运行环境（第 ${step + 1} 步）…` });
            const next = await api<SetupPlan & { complete: boolean; port?: number }>(
              '/setup/next-instance-plan', json('POST', {
                project_id: projectId, instance_name: 'shine-h3', port, mode: 'local',
                ...(variant ? { catalog: variant.catalog_id, variant_id: variant.variant_id } : {}),
              }));
            if (!alive()) return;
            if (typeof next.port === 'number') servicePort.current = next.port;
            if (next.complete) { complete = true; break; }
            await approveAndExecuteSetupPlan({ ...next, kind: 'INSTANCE_LIFECYCLE' }, check);
            if (!alive()) return;
          }
          if (!complete) throw new Error('INSTANCE_CHAIN_INCOMPLETE：实例步骤未收敛，请检查计划记录。');
        } else {
          setStage('workflows', { status: 'running', note: '保留现有 ComfyUI 与模型，只登记固定工作流并生成 Shine 隔离运行配置…' });
        }
        const chosen = variantChoice.current;
        await api('/setup/profiles', json('POST', {
          project_id: projectId,
          ...(chosen ? { variant_id: chosen.variant_id, catalog: chosen.catalog_id } : {}),
        }));
        if (!alive()) return;
        await api('/setup/configuration', json('POST', {
          project_id: projectId,
          ...(servicePort.current ? { port: servicePort.current } : {}),
        }));
      } catch (error) {
        stopWith('workflows', `生成方案登记失败：${(error as Error).message}。已完成的步骤不会丢失，重新打开向导可继续。`);
        return;
      }
    }
    const runtime = await api<RuntimeState>('/setup/runtime');
    if (!alive()) return;
    // The runtime configuration is machine-wide: it is created once and reused
    // by every project on this computer, so another project's candidate is a
    // valid shared environment instead of "no configuration".
    if (!runtime.candidate) {
      stopWith('workflows', '还没有本机可用的真实制作配置。请到专业设置的「制作环境」生成运行配置。');
      return;
    }
    const body = { project_id: projectId, recipe_sha256: runtime.candidate.recipe_sha256 };
    if (onlyDiagnose) {
      const validation = runtime.service_validation;
      if (runtime.status !== 'ACTIVE') {
        setStage('workflows', { status: 'ok', note: '生成方案与配置已就绪，等待最后的验证与启用。' });
        setStage('runtime', { status: 'pending', note: '' });
        setFinished({ ok: false, summary: '检查完成：一切就绪，只差「验证并启用」最后一步。点击重新检测并启用即可完成。' });
        return;
      }
      if (validation && validation.status !== 'READY') {
        stopWith('runtime', `本机服务验证未通过：${(validation.blockers ?? []).join('；') || '需要重新验证'}。可重新运行向导完成验证。`);
        return;
      }
      setStage('workflows', { status: 'ok', note: '生成方案与本机服务验证均为有效。' });
      setStage('runtime', { status: 'ok', note: '真实制作环境已启用。' });
      setFinished({ ok: true, summary: '检查完成：真实制作环境处于可用状态。' });
      return;
    }
    try {
      setStage('workflows', { status: 'running', note: '正在检查本机 H3 服务与工作流；建议审片资源会单独报告，不会阻止制作…' });
      const validation = await api<{ status: string; advisory_reviews?: Record<string, { status: string }> }>('/setup/services/validate', {
        ...json('POST', body), signal: timeoutSignal(3_600_000),
      });
      if (!alive()) return;
      if (validation.status !== 'READY') {
        stopWith('workflows', '本机服务验证未通过，配置未启用。请到专业设置的「制作环境」查看具体未通过项。');
        return;
      }
      const reviewerReady = Object.values(validation.advisory_reviews ?? {}).filter(item => item.status === 'READY').length;
      const reviewerTotal = Object.keys(validation.advisory_reviews ?? {}).length;
      setStage('workflows', { status: 'ok', note: `H3 服务与工作流已验证。建议审片资源就绪 ${reviewerReady}/${reviewerTotal} 项；未就绪时仍可制作，相关检查会留待人工复核。` });
    } catch (error) {
      stopWith('workflows', `服务验证失败：${(error as Error).message}`);
      return;
    }

    // 6. 激活
    setStage('runtime', { status: 'running', note: RUNNING_TEXT.runtime });
    try {
      const activated = await api<RuntimeState>('/setup/runtime/activate', json('POST', body));
      if (activated.status !== 'ACTIVE') throw new Error(`RUNTIME_NOT_ACTIVE：${activated.status}`);
      if (!alive()) return;
      const confirmed = await verifyProductionReady();
      if (!confirmed.ok) { stopWith('runtime', confirmed.note); return; }
      if (confirmed.note) setStage('runtime', { status: 'warn', note: confirmed.note });
      await onRefresh();
      if (!alive()) return;
    } catch (error) {
      stopWith('runtime', `启用失败：${(error as Error).message}`);
      return;
    }
    setStage('runtime', { status: 'ok', note: '真实制作环境已启用（重启后依然有效）。' });
    setFinished({ ok: true, summary: '真实制作已准备好。现在可以返回作品，开始生成影片。' });
  }

  async function continueAfterReuse() {
    if (!reuseOffer) return;
    const alive = () => !cancelled.current && mounted.current;
    setReuseOffer(null);
    const installedScans = reuseOffer.scans.filter(scan => scan.software?.installed);
    if (installedScans.length) {
      if (installedScans.length !== 1) {
        stopWith('existing', '检测到多套已安装 ComfyUI。为避免选错目录或重复安装，请到专业设置选择要复用的那一套；本次没有复制、下载或修改文件。');
        return;
      }
      setStage('existing', { status: 'running', note: '正在原目录核验 ComfyUI 版本与模型哈希；大文件可能需要几分钟，不会复制或修改文件…' });
      try {
        const results: ExistingVerification[] = [];
        for (const scan of installedScans) {
          let result: ExistingVerification;
          do {
            result = await api<ExistingVerification>('/setup/existing-installations/verify', {
              ...json('POST', { path: scan.root, snapshot: scan.snapshot }),
              signal: timeoutSignal(3_600_000),
            });
            if (result.status === 'VERIFYING') {
              setStage('existing', { status: 'running', note: '另一个核验仍在读取本地文件；正在查询结果，不会重复扫描权重…' });
              await new Promise(resolve => setTimeout(resolve, result.retry_after_ms ?? 5000));
            }
          } while (result.status === 'VERIFYING');
          results.push(result);
        }
        const verifiedBytes = results.reduce((sum, result) => sum + result.verified_bytes, 0);
        const rejectedCount = results.reduce((sum, result) => sum + result.rejected.length, 0);
        const version = results.map(result => result.software.version_status).join('、');
        const recovered = results.some(result => result.verification_reused) ? '已恢复此前完成的核验结果，未重复读取权重。' : '';
        const scan = installedScans[0];
        const verification = results[0];
        const catalog = await api<Catalog>('/setup/catalog');
        const expectedH3Files = catalog.components
          .filter(component => component.component_id.startsWith('minimax-h3-'))
          .reduce((sum, component) => sum + component.files, 0);
        const verifiedH3Files = verification.verified
          .filter(item => item.component_id.startsWith('minimax-h3-')).length;
        if (verification.status !== 'INSTALLED_VERIFIED' || rejectedCount || verifiedH3Files < expectedH3Files) {
          setStage('existing', { status: 'warn', note: `ComfyUI 已安装（${version}），已核验 ${fmtBytes(verifiedBytes)}；${rejectedCount} 项校验失败。` });
          stopWith('models', 'ComfyUI 已按“已安装”识别，但 H3 固定权重没有全部通过哈希校验。为保护已有文件，本次不复制也不下载；请在专业设置查看具体缺项。');
          return;
        }
        setStage('existing', { status: 'ok', note: `已安装 ComfyUI：${version}；固定 H3 权重哈希校验通过 ${fmtBytes(verifiedBytes)}。${recovered}` });
        if (!window.confirm(`确认复用这套已安装环境？\n目录：${scan.root}\nH3 权重已通过哈希校验：${fmtBytes(verification.verified_bytes)}\n程序会在本机受管流程中启动并停止 ComfyUI，运行数据写入 Shine 的隔离目录。原安装目录不写入；0 字节复制，0 字节下载。`)) {
          setFinished({ ok: false, summary: '已确认这套环境已安装；你取消了本次接入，没有复制、下载或修改原安装。' });
          return;
        }
        setStage('existing', { status: 'running', note: '正在把已安装环境登记为本机复用项；不会复制或下载软件与模型…' });
        const adoption = await api<{ status: string; installed_h3_bytes: number; copied_bytes: number; downloaded_bytes: number }>(
          '/setup/existing-installations/adopt', json('POST', {
            project_id: projectId, path: scan.root, snapshot: scan.snapshot,
            instance_name: 'shine-h3', ...(servicePort.current ? { port: servicePort.current } : {}),
          }));
        if (!alive()) return;
        setReusedBytes(adoption.installed_h3_bytes);
        setStage('existing', { status: 'ok', note: `已登记为“复用本机已安装环境”：${fmtBytes(adoption.installed_h3_bytes)} H3 权重原位使用；复制 ${adoption.copied_bytes} 字节，下载 ${adoption.downloaded_bytes} 字节。` });
        const storage = await api<StorageState>('/setup/storage');
        await runTail(alive, false, adoption.installed_h3_bytes, storage, true);
      } catch (error) {
        stopWith('existing', `现有安装接入失败：${(error as Error).message}。原目录未被复制或修改，也没有下载软件或模型；请先查看原因再决定下一步。`);
      }
      return;
    }
    setStage('existing', { status: 'running', note: '正在校验并复制可复用的模型（仅哈希与固定清单一致的文件）…' });
    let imported = 0;
    try {
      const total = await importReuse(reuseOffer.scans);
      if (!mounted.current) return;
      imported = total;
      setReusedBytes(total);
      setStage('existing', { status: 'ok', note: `已复用 ${fmtBytes(total)} 已有模型；缺少的部分才会在后续按需下载。` });
    } catch (error) {
      stopWith('existing', `复用已有模型失败：${(error as Error).message}。原安装未做任何修改。`);
      return;
    }
    const storage = await api<StorageState>('/setup/storage');
    await runTail(() => !cancelled.current && mounted.current, false, imported, storage);
  }

  /** 一次确认完成基础工具补装：固定计划 ID、固定参数、无任意命令。 */
  async function repairTools() {
    if (!repairOffer) return;
    const plan = repairOffer;
    setRepairOffer(null);
    repairAttempts.current += 1;
    setStage('hardware', { status: 'running', note: '正在用 Windows 自带 PowerShell 5.1 补装固定工具包；已下载的部分会保留，不会重复下载同一包。' });
    try {
      const result = await api<{
        status: string; message?: string; unsatisfied_packages?: string[];
        verification?: Array<{ package_id: string; installed: boolean }>;
      }>('/setup/tool-repair/execute', {
        ...json('POST', { plan_id: plan.plan_id }),
        signal: timeoutSignal(3_600_000),
      });
      const note = REPAIR_IN_PROGRESS_TEXT[result.status] ?? result.status;
      if (result.status !== 'SUCCEEDED') {
        const packages = (result.unsatisfied_packages ?? []).join('、');
        stopWith('hardware', `${note}${result.message ? `：${result.message}` : ''}${packages ? `（${packages}）` : ''}。已下载的数据会保留，可以稍后重试；本次不会自动循环。`);
        return;
      }
      setStage('hardware', { status: 'running', note: `${note} 正在继续后面的准备…` });
      await run();
    } catch (error) {
      stopWith('hardware', `补装执行失败：${(error as Error).message}。已下载的数据会保留；可以稍后重试。`);
    }
  }

  function chooseVariant(option: VariantOption) {
    variantChoice.current = option;
    setVariantOffer(null);
    void safely(run);
  }

  function declineReuse() {
    if (!reuseOffer) return;
    const bytes = reuseOffer.totalBytes;
    if (!window.confirm(`不复用已有模型的话，缺少的组件将按固定清单下载（可能多花约 ${fmtBytes(bytes)} 的下载量）。\n确认不复用、直接继续？`)) return;
    setReuseOffer(null);
    setStage('existing', { status: 'ok', note: '已选择不复用；缺少的组件将在确认后下载。' });
    void safely(async () => {
      const storage = await api<StorageState>('/setup/storage');
      await runTail(() => !cancelled.current && mounted.current, false, 0, storage);
    });
  }

  const reuseHasInstalledSoftware = reuseOffer?.scans.some(scan => scan.software?.installed) ?? false;

  useEffect(() => {
    // Defer until the effect survives React StrictMode's setup/cleanup probe.
    let active = true;
    cancelled.current = false;
    queueMicrotask(() => { if (active) void safely(run); });
    return () => { active = false; };
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={mode === 'enable' ? '启用真实制作' : '检查真实制作环境'} onClick={() => undefined}>
      <div className="wizard-modal" onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" aria-label="关闭" onClick={() => { cancelled.current = true; onClose(); }}><X size={16} /></button>
        <h2>{mode === 'enable' ? '启用真实制作' : '检查真实制作环境'}</h2>
        <p className="wizard-lead">
          {mode === 'enable'
            ? '按顺序完成下面的检查。已有环境会自动识别和复用；需要下载或启动服务的操作会先征求你的确认。'
            : '只做检查，不改动任何文件。发现的问题会在这里说明原因和处理办法。'}
        </p>
        <ol className="wizard-stages" data-testid="wizard-stages">
          {stages.map((stage) => (
            <li key={stage.id} className={`wizard-stage ${stage.status}`} data-stage={stage.id}>
              <span className="wizard-stage-icon">
                {stage.status === 'ok' ? <CheckCircle2 size={16} />
                  : stage.status === 'running' ? <Loader2 size={16} className="spin" />
                  : stage.status === 'blocked' ? <TriangleAlert size={16} />
                  : <CircleDashed size={16} />}
              </span>
              <div>
                <b>{stage.title}</b>
                {stage.note && <span>{stage.note}</span>}
              </div>
            </li>
          ))}
        </ol>
        {llmRequired && <LLMSetupPanel onChanged={onRefresh} />}
        {reuseOffer && (
          <div className="wizard-reuse" data-testid="wizard-reuse">
            <p>{reuseHasInstalledSoftware
              ? `发现已安装的 ComfyUI${reuseOffer.totalBytes ? `，另有约 ${fmtBytes(reuseOffer.totalBytes)} 权重待核验` : ''}。核验会直接读取原文件；大文件可能需要几分钟，不会复制、覆盖或下载。再次打开时会查询已完成的核验结果。`
              : `发现可复用的已有模型（约 ${fmtBytes(reuseOffer.totalBytes)}）。核验后只复制与固定清单一致的模型到本机缓存，原安装不会被修改。`}</p>
            <div className="wizard-actions">
          <button type="button" className="btn-secondary" onClick={() => void safely(continueAfterReuse)}>{reuseHasInstalledSoftware ? '核验并确认复用' : '校验并复用模型'}</button>
              {!reuseHasInstalledSoftware && <button type="button" className="btn-secondary" onClick={declineReuse}>不复用，直接继续</button>}
            </div>
          </div>
        )}
        {repairOffer && (
          <div className="wizard-reuse" data-testid="wizard-repair">
            <p>
              本机缺少这些基础程序：
              <b>{repairOffer.packages.map(pkg => pkg.label).join('、')}</b>
              。将使用 Windows 自带的 PowerShell 5.1 按固定清单补装（约 {fmtBytes(repairOffer.download_bytes)}），
              每个安装包都会校验 SHA-256；已安装且达标的 Node/npm/uv 不会被重复下载，也不会改动其他软件或驱动。
            </p>
            <div className="wizard-actions">
              <button type="button" className="btn-secondary" onClick={() => void safely(repairTools)}>确认自动补装</button>
              <button type="button" className="btn-secondary" onClick={() => {
                setRepairOffer(null);
                stopWith('hardware', `已取消自动补装，没有执行任何下载或安装。缺少：${repairOffer.packages.map(pkg => pkg.label).join('、')}。可以到专业设置手动处理。`);
              }}>暂不补装</button>
            </div>
          </div>
        )}
        {variantOffer && (
          <div className="wizard-reuse" data-testid="wizard-variant">
            <p>
              这台电脑没有可直接投产的标准方案。先选一套 H3 组合，系统才按你的选择准备资源；
              候选方案下载完成只代表资源已整备，不代表通过真机验收。没有暗选默认 INT8。
            </p>
            <div className="wizard-actions wizard-variant-list">
              {variantOffer.map(option => (
                <button type="button" key={option.variant_id} className="btn-secondary"
                  onClick={() => chooseVariant(option)}>
                  {option.title}（{option.acceptance_status}，约 {fmtBytes(option.total_bytes ?? 0)}）
                </button>
              ))}
            </div>
          </div>
        )}
        {needProject && (
          <div className="wizard-reuse">
            <p>先创建一个作品，再回来启用真实制作。</p>
            <div className="wizard-actions"><button type="button" className="btn-secondary" onClick={() => { cancelled.current = true; onClose(); }}>去创建作品</button></div>
          </div>
        )}
        {finished && (
          <div className={`wizard-result ${finished.ok ? 'ok' : 'bad'}`} data-testid="wizard-result">
            <p>{finished.summary}</p>
            <div className="wizard-actions">
              {finished.ok && <button type="button" className="btn-secondary" onClick={onFinished}>返回作品，开始制作</button>}
              {!finished.ok && <button type="button" className="btn-secondary" onClick={onOpenExpertSetup}>到专业设置处理</button>}
              <button type="button" className="btn-secondary" onClick={() => void safely(run)}>
                <RefreshCw size={13} /> 重新检查
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
