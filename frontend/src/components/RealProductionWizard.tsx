import { CheckCircle2, CircleDashed, Loader2, RefreshCw, TriangleAlert, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, json } from '../api';

/** 「启用真实制作」单一路径向导。检查顺序和每个接口调用都与专业版
 *  制作环境面板共用同一后端合同；这里只做普通用户能读懂的进度呈现。
 *  已有 ComfyUI 优先识别与复用：只有确认缺少的组件才会下载，绝不默认
 *  重新下载几十 GB 模型。底层八个安装步骤由程序按顺序协调，用户只看
 *  到进度、缺失项和确需授权的操作。 */

type StageId = 'project' | 'hardware' | 'existing' | 'models' | 'workflows' | 'runtime' | 'done';
type StageStatus = 'pending' | 'running' | 'ok' | 'warn' | 'blocked';
type Stage = { id: StageId; title: string; status: StageStatus; note: string };

const STAGE_TITLES: Array<{ id: StageId; title: string }> = [
  { id: 'project', title: '检查当前作品' },
  { id: 'hardware', title: '检查本机硬件与基础程序' },
  { id: 'existing', title: '检测已有 ComfyUI 与模型' },
  { id: 'models', title: '核对模型版本与固定配置' },
  { id: 'workflows', title: '登记生成方案并验证本机服务' },
  { id: 'runtime', title: '启用真实制作环境' },
];

const INITIAL_STAGES: Stage[] = STAGE_TITLES.map(({ id, title }) => ({ id, title, status: 'pending', note: '' }));

type EnvReport = { blockers: Array<{ code: string; tool?: string; detail?: string }>; ram: { status: string }; disk: { status: string; free_gb: number }; gpu: Record<string, unknown> };
type ExistingScan = { root: string; snapshot: string; candidate_bytes: number; matches: Array<{ component_id: string; size: number }> };
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
  const [needProject, setNeedProject] = useState(false);
  const cancelled = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelled.current = true; }; }, []);

  function setStage(id: StageId, patch: Partial<Stage>) {
    if (!mounted.current) return;
    setStages((current) => current.map((stage) => stage.id === id ? { ...stage, ...patch } : stage));
  }

  function stopWith(id: StageId, note: string) {
    setStage(id, { status: 'blocked', note });
    if (mounted.current) setFinished({ ok: false, summary: note });
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

  async function run() {
    cancelled.current = false;
    setStages(INITIAL_STAGES.map((stage) => ({ ...stage, status: 'pending', note: '' })));
    setFinished(null); setReuseOffer(null); setReusedBytes(0); setNeedProject(false);
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
        if (scan.matches.length) scans.push(scan);
      } catch { /* 单个目录检测失败不阻塞整体 */ }
    }
    const candidateBytes = scans.reduce((sum, scan) => sum + scan.candidate_bytes, 0);
    if (candidateBytes > 0) {
      setStage('existing', {
        status: 'warn',
        note: `发现已有 ComfyUI：${scans.length} 个目录中有可复用模型（约 ${fmtBytes(candidateBytes)}）。下面选择「校验并复用」即可避免重复下载。`,
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
      if (!alive()) return;
      const estimate = Math.max(0, catalog.total_bytes - reused);
      const note = `将按固定清单下载并校验缺少的组件（最多约 ${fmtBytes(estimate)}；已复用 ${fmtBytes(reused)} 不会重复下载）。`;
      setStage('models', { status: 'warn', note: `模型尚未校验。${note}` });
      // 下载属于重操作：先征求确认。
      if (!window.confirm(`需要下载并校验缺少的模型与组件（最多约 ${fmtBytes(estimate)}，已复用部分不重复下载）。\n目录与版本由系统固定，完成后自动继续。是否继续？`)) {
        stopWith('models', '已按你的选择停止：没有下载任何模型。需要时可重新打开向导继续。');
        return;
      }
      const plan = await api<{ plan_id: string; sha256: string; kind: string }>(
        '/setup/install-plan', json('POST', {
          components: catalog.components.map(c => c.component_id), project_id: projectId,
          disk_free: storage.free_bytes,
        }));
      if (!alive()) return;
      const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
        json('POST', { action: 'install_approve', target_id: plan.plan_id }));
      await api(`/comfyops/install-plans/${plan.plan_id}/approve`, json('POST', {
        grant_id: grant.grant_id, snapshot: grant.snapshot,
      }));
      if (!alive()) return;
      setStage('models', { status: 'running', note: '正在下载并校验模型与组件（耗时较长，请保持页面打开）…' });
      await api(`/comfyops/install-plans/${plan.plan_id}/execute`, { method: 'POST' });
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
        for (let step = 0; step < 8; step += 1) {
          if (!alive()) return;
          setStage('workflows', { status: 'running', note: `正在准备运行环境（第 ${step + 1} 步）…` });
          const next = await api<{ complete: boolean; plan_id?: string; sha256?: string; kind?: string }>(
            '/setup/next-instance-plan', json('POST', { project_id: projectId, instance_name: 'shine-h3', port: 8188, mode: 'local' }));
          if (!alive()) return;
          if (next.complete) break;
          await api(`/comfyops/instance-plans/${next.plan_id}/execute`, { method: 'POST' });
          if (!alive()) return;
        }
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
      await api('/setup/runtime/activate', json('POST', body));
      if (!alive()) return;
      await onRefresh();
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
    setStage('existing', { status: 'running', note: '正在校验并复制可复用的模型（仅哈希与固定清单一致的文件）…' });
    try {
      const total = await importReuse(reuseOffer.scans);
      if (!mounted.current) return;
      setReusedBytes(total);
      setStage('existing', { status: 'ok', note: `已复用 ${fmtBytes(total)} 已有模型；缺少的部分才会在后续按需下载。` });
    } catch (error) {
      stopWith('existing', `复用已有模型失败：${(error as Error).message}。原安装未做任何修改。`);
      return;
    }
    const storage = await api<StorageState>('/setup/storage');
    await runTail(() => !cancelled.current && mounted.current, false, reuseOffer.totalBytes, storage);
  }

  function declineReuse() {
    if (!reuseOffer) return;
    const bytes = reuseOffer.totalBytes;
    setReuseOffer(null);
    if (!window.confirm(`不复用已有模型的话，缺少的组件将按固定清单下载（可能多花约 ${fmtBytes(bytes)} 的下载量）。\n确认不复用、直接继续？`)) return;
    setStage('existing', { status: 'ok', note: '已选择不复用；缺少的组件将在确认后下载。' });
    void (async () => {
      const storage = await api<StorageState>('/setup/storage');
      await runTail(() => !cancelled.current && mounted.current, false, 0, storage);
    })();
  }

  useEffect(() => { void run(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={mode === 'enable' ? '启用真实制作' : '检查真实制作环境'} onClick={() => undefined}>
      <div className="wizard-modal" onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" aria-label="关闭" onClick={onClose}><X size={16} /></button>
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
        {reuseOffer && (
          <div className="wizard-reuse" data-testid="wizard-reuse">
            <p>发现可复用的已有模型（约 {fmtBytes(reuseOffer.totalBytes)}）。复用会先做哈希校验，只复制与固定清单一致的文件，原安装不会被修改。</p>
            <div className="wizard-actions">
              <button type="button" className="primary-button" onClick={() => void continueAfterReuse()}>校验并复用</button>
              <button type="button" className="subtle-button" onClick={declineReuse}>不复用，直接继续</button>
            </div>
          </div>
        )}
        {needProject && (
          <div className="wizard-reuse">
            <p>先创建一个作品，再回来启用真实制作。</p>
            <div className="wizard-actions"><button type="button" className="primary-button" onClick={onClose}>去创建作品</button></div>
          </div>
        )}
        {finished && (
          <div className={`wizard-result ${finished.ok ? 'ok' : 'bad'}`} data-testid="wizard-result">
            <p>{finished.summary}</p>
            <div className="wizard-actions">
              {finished.ok && <button type="button" className="primary-button" onClick={onFinished}>返回作品，开始制作</button>}
              {!finished.ok && <button type="button" className="subtle-button" onClick={onOpenExpertSetup}>到专业设置处理</button>}
              <button type="button" className="subtle-button" onClick={() => void run()}>
                <RefreshCw size={13} /> 重新检查
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
