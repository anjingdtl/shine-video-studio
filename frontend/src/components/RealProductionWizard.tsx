import { CheckCircle2, CircleDashed, Loader2, RefreshCw, TriangleAlert, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api as request, json, type SystemStatus } from '../api';
import { LLMSetupPanel } from './LLMSetupPanel';
import { approveAndExecuteSetupPlan, type SetupPlan } from '../setupExecution';

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

type EnvReport = { blockers: Array<{ code: string; tool?: string; detail?: string }>; ram: { status: string }; disk: { status: string; free_gb: number }; gpu: Record<string, unknown> };
type ExistingScan = {
  root: string; snapshot: string; candidate_bytes: number;
  matches: Array<{ component_id: string; size: number }>;
  software?: { installed: boolean; version_status: string; actual_revision?: string | null;
    runtime?: { status: string }; node_revisions_verified?: boolean };
};
type ExistingVerification = { status: string; software: NonNullable<ExistingScan['software']>;
  verified: Array<{ component_id: string; size: number }>;
  rejected: Array<{ component_id: string; size: number }>;
  verified_bytes: number; copied_bytes: number; verification_reused?: boolean; retry_after_ms?: number };
type RuntimeState = {
  status: string; error: string | null; render_provider: string;
  candidate: { project_id: string; recipe_sha256: string } | null;
  service_validation?: { status: string; blockers?: string[] } | null;
};
type Readiness = { layers: Array<{ layer?: string; status?: string }> };
type StorageState = { root: string; requires_selection: boolean; free_bytes: number };
type Catalog = { total_bytes: number; components: Array<{ component_id: string }> };

const HARDWARE_BLOCKER_ZH: Record<string, string> = {
  TOOL_MISSING_INSTALLABLE: '缺少必需的基础程序', TOOL_STALE: '基础程序版本过旧',
  GPU_VRAM_BELOW_FLOOR: '显卡可用显存不足', RAM_BELOW_FLOOR: '本机内存不足或无法读取',
  DISK_BELOW_FLOOR: '安装目录空间不足', PORT_OCCUPIED: '服务端口被占用',
  DATA_DIR_NOT_WRITABLE: '项目数据目录无法写入', DEPLOYMENT_DIR_NOT_WRITABLE: '安装目录无法写入',
  NVIDIA_SMI_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
  NVIDIA_DRIVER_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
  NVIDIA_SMI_OUTPUT_UNPARSEABLE: '显卡检测结果无法读取，请检查显卡驱动',
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
  function check() {
    if (!mounted.current || cancelled.current || selectedProject.current !== originalProject.current) throw new Error('WIZARD_CANCELLED');
  }
  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    check();
    const result = await request<T>(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(60_000) });
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
          signal: AbortSignal.timeout(3_600_000),
        });
      total += result.reused_bytes;
    }
    return total;
  }

  async function verifyProductionReady() {
    const status = await api<SystemStatus>('/system/status');
    if (!['READY', 'RUNNING'].includes(status.production_mode.mode)) {
      throw new Error(`真实制作暂未就绪（${status.production_mode.code ?? status.production_mode.mode}）：${status.production_mode.detail}`);
    }
  }

  async function run() {
    cancelled.current = false;
    setStages(INITIAL_STAGES.map((stage) => ({ ...stage, status: 'pending', note: '' })));
    setFinished(null); setLlmRequired(false); setReuseOffer(null); setReusedBytes(0); setNeedProject(false);
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

    setStage('llm', { status: 'running', note: '检查文本模型、连接检测与全部文本工种路由…' });
    const llm = await api<{ status: string }>('/setup/llm-readiness');
    if (llm.status !== 'READY') {
      setLlmRequired(true);
      stopWith('llm', '请先完成核心 LLM 配置、连接测试及文本工种指定（LLM_SETUP_REQUIRED），再点击重新检查继续。');
      return;
    }
    setStage('llm', { status: 'ok', note: '核心 LLM 配置与连接检查通过；文本生成质量独立验收。' });

    // Read durable state before creating any further intent. Never replay ambiguous work.
    const plans = await api<Array<{ id: string; status: string }>>(`/comfyops/install-plans?project_id=${projectId}`);
    for (const plan of plans) {
      const key = `shine.setupUnconfirmed.${plan.id}`;
      if (localStorage.getItem(key)) {
        if (['FILES_VERIFIED_PENDING_RUNTIME', 'STOPPED', 'RECONCILED_NO_RETRY'].includes(plan.status)) localStorage.removeItem(key);
        else { stopWith('project', '有执行结果未确认（EXECUTION_UNCONFIRMED），请到专业设置对账。'); return; }
      }
    }
    if (plans.some(plan => ['INSTALLING', 'SUBMIT_INTENT', 'NEEDS_REVIEW'].includes(plan.status))) {
      stopWith('project', '存在执行中或需要对账的计划（RECONCILE_REQUIRED）。请到专业设置查看尝试记录，不能重新执行。');
      return;
    }
    const active = await api<RuntimeState>('/setup/runtime');
    if (active.status === 'INVALID_REQUIRES_REVIEW') {
      stopWith('project', `配置需要检查（INVALID_REQUIRES_REVIEW）：${String(active.error)}。请到专业设置处理。`); return;
    }
    if (active.status === 'ACTIVE' && active.candidate?.project_id === projectId && active.service_validation?.status === 'READY') {
      await verifyProductionReady();
      for (const stage of STAGE_TITLES) setStage(stage.id, { status: 'ok', note: '已有配置与服务验证有效。' });
      setFinished({ ok: true, summary: '真实制作已就绪；渲染器空闲停止时也无需重新安装。' });
      return;
    }

    // 2. 硬件与基础程序
    setStage('hardware', { status: 'running', note: RUNNING_TEXT.hardware });
    try {
      const env = await api<EnvReport>('/setup/environment');
      if (!alive()) return;
      if (env.blockers.length) {
        stopWith('hardware', `本机还缺少必要条件：${env.blockers.map(b => HARDWARE_BLOCKER_ZH[b.code] ?? `环境检测未通过（${b.code}）`).join('；')}。处理后在专业设置的「制作环境」里可查看细节。`);
        return;
      }
      setStage('hardware', { status: 'ok', note: '显卡、内存、磁盘与基础程序均满足要求。' });
    } catch (error) {
      stopWith('hardware', `环境检查失败：${(error as Error).message}`);
      return;
    }

    // 3. 已有 ComfyUI 与模型：优先复用，禁止默认重下
    setStage('existing', { status: 'running', note: RUNNING_TEXT.existing });
    const storage = await api<StorageState>('/setup/storage');
    if (!alive()) return;
    if (storage.requires_selection) {
      stopWith('existing', '还没有选择安装目录。请到专业设置的「制作环境」选择一个空间足够的非系统盘目录后重试。');
      return;
    }
    const found = await api<{ installations: string[] }>('/setup/existing-installations');
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
          : '未检测到已有 ComfyUI；将在确认后按固定清单下载缺少的组件。',
      });
    }

    await runTail(alive, onlyDiagnose, reusedBytes, storage);
  }

  /** 4–6：模型核对 → 生成方案与服务验证 → 激活。 */
  async function runTail(alive: () => boolean, onlyDiagnose: boolean, reused: number, storage: StorageState) {
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
      if (onlyDiagnose) {
        stopWith('models', '模型与固定清单尚未核对完成（未校验的文件不会用于真实生成）。请到专业设置的「制作环境」执行下载与校验。');
        return;
      }
      const catalog = await api<Catalog>('/setup/catalog');
      const previous = await api<Array<{ id: string; sha256: string; status: string; payload: { deployment?: unknown } }>>(`/comfyops/install-plans?project_id=${projectId}`);
      const pending = previous.filter(p => p.payload.deployment && ['AWAITING_APPROVAL', 'APPROVED'].includes(p.status));
      if (pending.length > 1) throw new Error('MULTIPLE_PENDING_PLANS：请到专业设置选择并核对计划。');
      const plan = pending.length ? { plan_id: pending[0].id, sha256: pending[0].sha256, status: pending[0].status }
        : await api<SetupPlan & { download_bytes: number }>('/setup/install-plan', json('POST', {
          components: catalog.components.map(c => c.component_id), project_id: projectId, disk_free: storage.free_bytes,
        }));
      if (!alive()) return;
      if (!window.confirm(`批准固定部署清单中的缺失组件下载与校验？\n作品：${projectId}\n目录：${storage.root}\n计划：${plan.plan_id}\n哈希：${plan.sha256}\n已复用 ${fmtBytes(reused)}，已有缓存将校验复用，完整清单上限约 ${fmtBytes(catalog.total_bytes)}。原安装保持不变。`)) {
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
    setStage('models', { status: 'ok', note: '模型与固定清单核对一致（哈希校验通过）。' });

    // 5. 运行环境 + 生成方案登记 + 服务验证
    setStage('workflows', { status: 'running', note: RUNNING_TEXT.workflows });
    if (!onlyDiagnose) {
      try {
        if (!window.confirm('将按固定部署清单准备受管实例、配置节点和模型绑定，并启动本机服务验证后停止。是否批准这些有序步骤？')) {
          stopWith('workflows', '已取消实例准备，没有执行实例计划。'); return;
        }
        let complete = false;
        for (let step = 0; step < 8; step += 1) {
          if (!alive()) return;
          setStage('workflows', { status: 'running', note: `正在准备运行环境（第 ${step + 1} 步）…` });
          const next = await api<SetupPlan & { complete: boolean }>(
            '/setup/next-instance-plan', json('POST', { project_id: projectId, instance_name: 'shine-h3', port: 8188, mode: 'local' }));
          if (!alive()) return;
          if (next.complete) { complete = true; break; }
          await approveAndExecuteSetupPlan({ ...next, kind: 'INSTANCE_LIFECYCLE' }, check);
          if (!alive()) return;
        }
        if (!complete) throw new Error('INSTANCE_CHAIN_INCOMPLETE：实例步骤未收敛，请检查计划记录。');
        await api('/setup/profiles', json('POST', { project_id: projectId }));
        if (!alive()) return;
        await api('/setup/configuration', json('POST', { project_id: projectId }));
      } catch (error) {
        stopWith('workflows', `生成方案登记失败：${(error as Error).message}。已完成的步骤不会丢失，重新打开向导可继续。`);
        return;
      }
    }
    const runtime = await api<RuntimeState>('/setup/runtime');
    if (!alive()) return;
    if (!runtime.candidate || runtime.candidate.project_id !== projectId) {
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
      setStage('workflows', { status: 'running', note: '正在启动本机服务并验证工作流与审片能力，完成后自动停止…' });
      const validation = await api<{ status: string }>('/setup/services/validate', {
        ...json('POST', body), signal: AbortSignal.timeout(3_600_000),
      });
      if (!alive()) return;
      if (validation.status !== 'READY') {
        stopWith('workflows', '本机服务验证未通过，配置未启用。请到专业设置的「制作环境」查看具体未通过项。');
        return;
      }
      setStage('workflows', { status: 'ok', note: '工作流、审片能力与固定节点全部验证通过。' });
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
      await verifyProductionReady();
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
    setReuseOffer(null);
    const installedScans = reuseOffer.scans.filter(scan => scan.software?.installed);
    if (installedScans.length) {
      setStage('existing', { status: 'running', note: '正在原目录核验 ComfyUI 版本与模型哈希；大文件可能需要几分钟，不会复制或修改文件…' });
      try {
        const results: ExistingVerification[] = [];
        for (const scan of installedScans) {
          let result: ExistingVerification;
          do {
            result = await api<ExistingVerification>('/setup/existing-installations/verify', {
              ...json('POST', { path: scan.root, snapshot: scan.snapshot }),
              signal: AbortSignal.timeout(3_600_000),
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
        const nodeVersionsKnown = results.every(result => result.software.node_revisions_verified);
        const recovered = results.some(result => result.verification_reused) ? '已恢复此前完成的核验结果，未重复读取权重。' : '';
        setStage('existing', { status: 'ok', note: `已安装 ComfyUI：${version}；已核验 ${fmtBytes(verifiedBytes)} 权重，${rejectedCount} 项未通过哈希；0 字节复制，0 字节下载。${recovered}` });
        stopWith('workflows', `发现并确认了现有 ComfyUI 与模型（EXISTING_INSTALL_RUNTIME_ADOPTION_REQUIRED）。当前运行环境兼容性${results.some(result => result.software.runtime?.status === 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED') ? '尚未验证' : '未确认'}，${nodeVersionsKnown ? '节点版本已核实' : '节点固定版本尚未核实'}。为避免重复安装、覆盖或下载，向导已停止；请在专业设置查看本机安装诊断。`);
      } catch (error) {
        stopWith('existing', `现有安装核验失败：${(error as Error).message}。原目录未被复制或修改；请重新检测后再试。`);
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
              <button type="button" className="primary-button" onClick={() => void safely(continueAfterReuse)}>{reuseHasInstalledSoftware ? '核验已安装环境' : '校验并复用模型'}</button>
              {!reuseHasInstalledSoftware && <button type="button" className="subtle-button" onClick={declineReuse}>不复用，直接继续</button>}
            </div>
          </div>
        )}
        {needProject && (
          <div className="wizard-reuse">
            <p>先创建一个作品，再回来启用真实制作。</p>
            <div className="wizard-actions"><button type="button" className="primary-button" onClick={() => { cancelled.current = true; onClose(); }}>去创建作品</button></div>
          </div>
        )}
        {finished && (
          <div className={`wizard-result ${finished.ok ? 'ok' : 'bad'}`} data-testid="wizard-result">
            <p>{finished.summary}</p>
            <div className="wizard-actions">
              {finished.ok && <button type="button" className="primary-button" onClick={onFinished}>返回作品，开始制作</button>}
              {!finished.ok && <button type="button" className="subtle-button" onClick={onOpenExpertSetup}>到专业设置处理</button>}
              <button type="button" className="subtle-button" onClick={() => void safely(run)}>
                <RefreshCw size={13} /> 重新检查
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
