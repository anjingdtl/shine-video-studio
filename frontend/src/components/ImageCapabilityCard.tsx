import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, CircleDashed, Image as ImageIcon, Loader2 } from 'lucide-react';
import { api } from '../api';

export type ImageFamilyReadiness = {
  layer: string;
  evidence_attempt_id: string | null;
  can_generate: boolean;
};

export type ImageRendererState = {
  state: string;
  instance_id: string;
  port: number | null;
  pid: number | null;
  stage?: string;
};

export type ImageReadiness = {
  configured: boolean;
  layer: string;
  families: Record<string, ImageFamilyReadiness>;
  renderer: ImageRendererState;
  fingerprint: string;
  config_revision: number;
  binding_candidate?: { instance_id: string; base_url: string; model_root: string } | null;
  config_digest: string;
  graph_sha256: string;
  output_node_id: string;
  output_class_type: string;
  model_files: { role: string; filename: string; expected_sha256?: string; actual_sha256?: string; cached?: boolean; verified_at?: string }[];
  graph_report: { complete: boolean; missing_nodes?: string[]; missing_inputs?: unknown[] } | null;
  prompt_template: { template_version: string; families: string[] };
  blockers: { code: string; layer?: string; detail?: string; remediation?: string }[];
  license: { name: string; scope: string; note: string };
  upstream: {
    template_repository: string;
    template_commit: string;
    template_sha256: string;
    model_repository: string;
    model_revision: string;
    required_bytes: number;
  };
};

const LAYER_LABELS: Record<string, string> = {
  NOT_CONFIGURED: '未配置',
  FILES_VERIFIED: '文件已校验',
  GRAPH_VERIFIED: '节点与图已校验',
  RENDER_VERIFIED: '已真实出图验证',
};

const FAMILY_LABELS: Record<string, string> = {
  character: '角色',
  scene: '场景',
  prop: '道具',
};

const IMAGE_CATALOG = 'qwen-image-2.1-windows-x64';
const IMAGE_COMPONENTS = ['qwen-image-2.1-unet', 'qwen-image-2.1-text-encoder', 'qwen-image-2.1-vae'];

const RENDERER_LABELS: Record<string, string> = {
  NOT_BOUND: '未绑定',
  NOT_STARTED: '未启动',
  RUNNING: '运行中',
  GONE: '已退出',
  STOPPED: '已停止',
  UNVERIFIED: '无法确认归属',
};

const BLOCKER_LABELS: Record<string, string> = {
  IMAGE_RUNTIME_NOT_BOUND: '还没有绑定本机的受管渲染器，AI 不能出图。绑定后即可登记并验证三类小样。',
  IMAGE_MODEL_ROOT_NOT_CONFIGURED: '还没有登记模型目录，无法核对权重。',
  IMAGE_MODEL_FILE_MISSING: '缺少权重文件，出图不能开始。',
  IMAGE_MODEL_SIZE_MISMATCH: '权重文件大小与固定清单不一致，请重新下载这一份。',
  IMAGE_MODEL_HASH_MISMATCH: '权重文件校验不通过，出图不能开始。',
  IMAGE_GRAPH_NOT_VERIFIED: '还没有对活体渲染器核对节点与参数图。',
};

export function ImageCapabilityCard({ projectId, busy }: { projectId: string; busy: boolean }) {
  const [state, setState] = useState<ImageReadiness | null>(null);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [plan, setPlan] = useState<{ plan_id: string; download_bytes: number; disk_required: number; root: string } | null>(null);

  const load = useCallback(async () => {
    setState(await api<ImageReadiness>('/setup/image-readiness'));
  }, []);

  useEffect(() => { void load().catch(() => undefined); }, [load]);

  const jsonPost = <T,>(path: string, body: unknown): Promise<T> => api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const registerInstall = async () => {
    setWorking(true);
    setProblem(null);
    setNote(null);
    try {
      const storage = await api<{ root: string; free_bytes: number }>('/setup/storage');
      const created = await jsonPost<{ plan_id: string; download_bytes: number; disk_required: number }>(
        '/setup/install-plan',
        {
          catalog: IMAGE_CATALOG,
          components: IMAGE_COMPONENTS,
          project_id: projectId,
          disk_free: storage.free_bytes,
        },
      );
      setPlan({ ...created, root: storage.root });
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  };

  const startRenderer = async () => {
    setWorking(true);
    setProblem(null);
    setNote(null);
    try {
      const result = await jsonPost<{ base_url: string }>('/setup/image-runtime/start', {});
      setNote(`本机出图渲染器已启动（${result.base_url}）。它属于本工作台，完成后可以用下面的按钮关闭并释放显存。`);
      await load();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  };

  const bindRenderer = async () => {
    if (!state?.binding_candidate) return;
    setWorking(true); setProblem(null); setNote(null);
    try {
      const result = await jsonPost<{ readiness: ImageReadiness }>('/setup/image-runtime', {
        ...state.binding_candidate, expected_config_revision: state.config_revision,
      });
      setState(result.readiness);
      setNote('已绑定本机受管实例。下一步是验证真实出图。');
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
      await load().catch(() => undefined);
    } finally {
      setWorking(false);
    }
  };

  const stopRenderer = async () => {
    setWorking(true);
    setProblem(null);
    setNote(null);
    try {
      await jsonPost('/setup/image-runtime/stop', {});
      setNote('本机出图渲染器已停止，显存已释放。');
      await load();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  };

  const validate = async () => {
    setWorking(true);
    setProblem(null);
    setNote(null);
    try {
      const result = await jsonPost<{ run_id: string }>('/setup/image-runtime/validate', {
        project_id: projectId,
        idempotency_key: `image-validate-${projectId}-${state?.fingerprint ?? 'unknown'}`,
        families: ['character', 'scene', 'prop'],
      });
      setNote(`三类真实小样已排队（批次 ${result.run_id}）。后台出图期间可以继续做别的；完成后这里会显示验证结果。`);
      await load();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  };
  const families = Object.entries(state?.families ?? {});
  const rendererState = state?.renderer?.state ?? 'NOT_STARTED';
  const generatable = families.filter(([, row]) => row.can_generate).map(([name]) => FAMILY_LABELS[name] ?? name);
  const gigabytes = state ? (state.upstream.required_bytes / 1024 ** 3).toFixed(2) : '—';

  return (
    <section id="image-capability" className="card section-card image-capability" aria-label="AI 素材出图能力">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">AI MATERIAL CAPABILITY</div>
          <h2><ImageIcon size={15} /> AI 素材出图能力</h2>
          <p>这一页显示 AI 帮你生成人物、场景、道具素材的能力。四层是一层一层往上叠的，缺一层都不能对外说「能出图」。</p>
        </div>
      </div>

      <div className="setup-status-grid image-status-grid">
        <div className={`setup-status ${state?.configured ? 'ok' : 'pending'}`}>
          <div className="setup-status-icon"><ImageIcon size={16} /></div>
          <div>
            <b>出图运行时</b>
            <span>{state ? (LAYER_LABELS[state.layer] ?? state.layer) : '正在检查'}</span>
          </div>
          {state?.configured ? <Check size={15} /> : <AlertTriangle size={15} />}
        </div>
        <div className={`setup-status ${rendererState === 'RUNNING' ? 'ok' : 'pending'}`}>
          <div className="setup-status-icon"><ImageIcon size={16} /></div>
          <div>
            <b>本机出图渲染器</b>
            <span>{state ? (RENDERER_LABELS[rendererState] ?? rendererState) : '正在检查'}</span>
          </div>
          {rendererState === 'RUNNING' ? <Check size={15} /> : <CircleDashed size={15} />}
        </div>
        <div className={`setup-status ${generatable.length === 3 ? 'ok' : 'pending'}`}>
          <div className="setup-status-icon"><Check size={16} /></div>
          <div>
            <b>可生成的类别</b>
            <span>{generatable.length ? `${generatable.join('、')} 可生成` : '还没有类别通过真实出图验证'}</span>
          </div>
          {generatable.length === 3 ? <Check size={15} /> : <AlertTriangle size={15} />}
        </div>
      </div>

      <ul className="image-family-list">
        {families.map(([name, row]) => (
          <li key={name}>
            <span>{FAMILY_LABELS[name] ?? name}</span>
            <span>{LAYER_LABELS[row.layer] ?? row.layer}</span>
            {row.can_generate ? <Check size={13} /> : <CircleDashed size={13} />}
          </li>
        ))}
      </ul>

      {(state?.blockers ?? []).map((blocker) => (
        <div className="blocker-item is-action" key={`${blocker.code}-${blocker.layer ?? ''}`}>
          <AlertTriangle size={14} className="required-icon" />
          <div className="blocker-reason">
            <b>{BLOCKER_LABELS[blocker.code] ?? blocker.code}</b>
            <small>{blocker.detail ?? blocker.remediation ?? '按上面的提示处理后再试。'}</small>
          </div>
        </div>
      ))}
      {problem && (
        <div className="blocker-item is-action" role="alert">
          <AlertTriangle size={14} className="required-icon" />
          <div className="blocker-reason"><b>这一步没有完成</b><small>{problem}</small></div>
        </div>
      )}
      {note && (
        <div className="notice" role="status">
          <Check size={13} />
          <div><p>{note}</p></div>
        </div>
      )}

      <div className="image-capability-foot">
        <span>
          固定模板 {state?.upstream.template_commit.slice(0, 12) ?? '—'}… · 模型 {state?.upstream.model_revision.slice(0, 12) ?? '—'}… · 权重约 {gigabytes} GiB
        </span>
        <span>{state?.license.note}</span>
      </div>

      {plan && (
        <div className="notice" role="status">
          <Check size={13} />
          <div>
            <p>
              已登记出图权重安装计划 <b>{plan.plan_id}</b>：预计下载 {(plan.download_bytes / 1024 ** 3).toFixed(2)} GiB，
              需要 {(plan.disk_required / 1024 ** 3).toFixed(2)} GiB（含下载临时空间），目标盘 {plan.root}。
            </p>
            <p>到「安装与下载」核对并批准这个计划后才会开始下载；每份文件都会按固定哈希校验。</p>
          </div>
        </div>
      )}
      <button type="button" className="btn-secondary" disabled={working || busy} onClick={registerInstall}>
        登记出图权重安装计划
      </button>
      <button type="button" className="btn-secondary" disabled={working || busy || !state?.configured}
        onClick={startRenderer}>
        {working ? <Loader2 size={13} /> : <ImageIcon size={13} />} 启动本机出图渲染器
      </button>
      <button type="button" className="btn-secondary" disabled={working || busy || rendererState !== 'RUNNING'}
        onClick={stopRenderer}>
        停止出图渲染器并释放显存
      </button>
      <button type="button" className="btn-primary" disabled={working || busy || !state?.configured} onClick={validate}>
        {working ? <Loader2 size={13} /> : <ImageIcon size={13} />} 验证三类真实小样
      </button>
      {!state?.configured && state?.binding_candidate && <div className="notice">
        <p>可复用已登记的本机实例，模型目录为 {state.binding_candidate.model_root}。绑定后核验固定权重。</p>
        <button type="button" className="btn-primary" disabled={working || busy} onClick={bindRenderer}>
          {working ? '正在核验出图权重…' : '绑定现有实例用于出图'}
        </button>
      </div>}
      {!state?.configured && !state?.binding_candidate && (
        <p className="material-hint">
          还没有绑定受管渲染器，出图能力无法验证。先在「安装与下载」完成本机环境检测与受管实例绑定。
        </p>
      )}
    </section>
  );
}
