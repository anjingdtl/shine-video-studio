import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Cpu, RefreshCw, Wrench } from 'lucide-react';
import { ApiError, api, json } from '../api';

/**
 * 一个变体在这台电脑上的五层就绪状态。
 *
 * 五层是有依赖顺序的：权重到位 → 字节核验 → 方案登记 → 本机运行时核对 →
 * 真实出片。页面只给**一个**下一步：「差不多能用」不是一个动作。
 *
 * 候选变体在真实出片之前永远是候选，不会进入默认生产推荐；这一层没走到
 * 之前，页面不会把它说成可以用。
 */

export type ReadinessLayer = {
  id: string;
  label: string;
  state: 'DONE' | 'PENDING';
  detail: string;
};

export type VariantHostFacts = {
  status?: string;
  gpu?: string | null;
  vram_total_mb?: number | null;
  vram_free_mb?: number | null;
  ram_total_mb?: number | null;
  ram_available_mb?: number | null;
  commit_total_mb?: number | null;
  commit_available_mb?: number | null;
  page_file_status?: string | null;
  disk_free_gb?: number | null;
  cuda_status?: string | null;
  compute_capability?: string | null;
  torch?: { status?: string; version?: string | null; cuda_build?: string | null; arch_list?: string[] | null };
  probed_at?: string | null;
};

export type VariantReadiness = {
  variant_id: string;
  title: string | null;
  label: string | null;
  summary: string | null;
  quantization: string | null;
  acceptance_status: string;
  source: string | null;
  tier: string | null;
  catalog_id: string;
  checkpoint_id: string | null;
  auto_download_allowed: boolean;
  workflow_profile_ids: string[];
  layers: ReadinessLayer[];
  reached_layer: string;
  reached_label: string;
  production_ready: boolean;
  next_step: {
    code: string;
    label: string;
    reason: string;
    action: string | null;
    blocked: boolean;
  };
  host: VariantHostFacts;
  profiles: Array<{
    name: string;
    profile_id: string | null;
    registered: boolean;
    validated: boolean;
    graph_sha256: string | null;
    checkpoint_id: string | null;
    variant_id: string | null;
    variant_acceptance_status: string | null;
    reference_slots: string[];
  }>;
  real_render: { job_id: string; shot_id: string } | null;
};

const ACCEPTANCE_LABELS: Record<string, string> = {
  SUPPORTED: '已验收',
  EXPERIMENTAL: '实验性',
  CANDIDATE: '候选（未完成真机验收）',
  BLOCKED: '阻断',
};

function gib(mb?: number | null): string {
  if (mb == null || Number.isNaN(mb)) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

type Props = {
  projectId: string;
  variantId: string;
  /** 变体名，页面标题用。 */
  title: string;
  /** 下一步需要「导入已有权重」时，交给上层把安装面板翻出来。 */
  onNeedImport?: () => void;
  onChanged?: () => void | Promise<void>;
};

export function VariantReadinessCard({ projectId, variantId, title, onNeedImport, onChanged }: Props) {
  const [data, setData] = useState<VariantReadiness | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [problem, setProblem] = useState('');
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState('');

  const detect = useCallback(async () => {
    if (!projectId) { setState('error'); setProblem('先选一个作品，再检查这个变体。'); return; }
    setState('loading');
    setProblem('');
    try {
      const rows = await api<VariantReadiness>(
        `/setup/variant-readiness?project_id=${encodeURIComponent(projectId)}&variant_id=${encodeURIComponent(variantId)}`,
      );
      setData(rows);
      setState('ready');
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : String(e));
      setState('error');
    }
  }, [projectId, variantId]);

  useEffect(() => { void detect(); }, [detect]);

  const runAction = useCallback(async (action: string) => {
    setWorking(true);
    setMessage('');
    try {
      if (action === 'REGISTER_PROFILES') {
        const result = await api<{ profiles: Array<{ name: string; status: string }> }>(
          '/setup/profiles', json('POST', { project_id: projectId, variant_id: variantId }),
        );
        const registered = result.profiles.map((row) => row.name).join('、');
        setMessage(`已登记：${registered}`);
      } else if (action === 'VALIDATE_PROFILES') {
        const result = await api<{ profiles: Array<{ name: string; validated: boolean; code: string | null }> }>(
          '/setup/profiles/validate', json('POST', { project_id: projectId, variant_id: variantId }),
        );
        const ok = result.profiles.filter((row) => row.validated).map((row) => row.name);
        const bad = result.profiles.filter((row) => !row.validated);
        setMessage(ok.length
          ? `本机节点核对通过：${ok.join('、')}`
          : `还没有核对通过：${bad.map((row) => `${row.name}（${row.code ?? '未通过'}）`).join('、')}`);
      } else if (action === 'EXISTING_INSTALLATION_VERIFY' || action === 'EXISTING_INSTALLATION_IMPORT') {
        onNeedImport?.();
        setMessage('请在「检测已有安装 / 导入模型」里完成：产品按钉住的 revision、字节数和 SHA-256 核验，不自动下载。');
      }
      await detect();
      await onChanged?.();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  }, [projectId, variantId, detect, onChanged, onNeedImport]);

  const host = data?.host ?? {};
  const next = data?.next_step ?? null;

  return <section className="card section-card variant-readiness" id="variant-readiness">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">LOW-VRAM CANDIDATE</div>
        <h2>{title} · 五层就绪状态</h2>
        <p>官方 W6A8 低显存候选：官方 pinned 的 DiT + INT8 ConvRot 视频 VAE + FP32 音频 VAE + NVFP4 文本编码器。
          官方没有公布这一组合的显存门槛，所以它在本机能不能跑通必须真机验证。</p>
      </div>
      <button className="secondary-button" disabled={state === 'loading'} onClick={() => { void detect(); }}>
        {state === 'loading' ? <RefreshCw size={13} className="spin" /> : <Cpu size={13} />}
        重新检查
      </button>
    </div>

    {state === 'error' && <p className="form-error">检查失败：{problem}</p>}
    {state === 'idle' && <p className="empty-inline">正在读取本机事实…</p>}

    {data && state !== 'error' && <>
      <div className="recommend-host">
        <span>显卡：{host.gpu || '未识别'}</span>
        <span>显存：{gib(host.vram_total_mb)}（可用 {gib(host.vram_free_mb)}）</span>
        <span>内存：{gib(host.ram_total_mb)}（可用 {gib(host.ram_available_mb)}）</span>
        <span>提交上限：{gib(host.commit_total_mb)}（可用 {gib(host.commit_available_mb)}）</span>
        <span>CUDA：{host.compute_capability ?? '未识别'}</span>
        <span>Torch：{host.torch?.version ?? '未安装'}{host.torch?.cuda_build ? `（cu${host.torch.cuda_build}）` : ''}</span>
        <span>磁盘可用：{host.disk_free_gb != null ? `${host.disk_free_gb} GB` : '未识别'}</span>
      </div>

      <div className="readiness-layers">
        {data.layers.map((layer) => <div className="readiness-layer" key={layer.id}>
          <div>
            <b>{layer.label}</b>
            <span>{layer.detail}</span>
          </div>
          {layer.state === 'DONE'
            ? <span className="workflow-size-ok"><Check size={12} /> 已完成</span>
            : <span className="chip warn"><AlertTriangle size={11} /> 待完成</span>}
        </div>)}
      </div>

      {next && <div className={`blocked-callout ${next.blocked ? '' : 'run-hint'}`} role="status">
        <Wrench size={15} />
        <div>
          <b>当前唯一下一步：{next.label}</b>
          <p>{next.reason}</p>
          {next.action && <button className="must-button" disabled={working}
            onClick={() => { void runAction(next.action as string); }}>
            {working ? '处理中…' : next.label}
          </button>}
        </div>
      </div>}

      {message && <p className="run-hint">{message}</p>}

      <p className="run-hint">
        验收状态：{ACCEPTANCE_LABELS[data.acceptance_status] ?? data.acceptance_status}
        {data.auto_download_allowed ? '' : ' · 不会自动下载'}
        {' · 部署目录 '}{data.catalog_id}
        {data.checkpoint_id ? ` · 权重 ${data.checkpoint_id}` : ''}
      </p>
    </>}
  </section>;
}

export default VariantReadinessCard;