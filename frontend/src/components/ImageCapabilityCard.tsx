import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, CircleDashed, Image as ImageIcon, Loader2 } from 'lucide-react';
import { api } from '../api';

export type ImageFamilyReadiness = {
  layer: string;
  evidence_attempt_id: string | null;
  can_generate: boolean;
};

export type ImageReadiness = {
  configured: boolean;
  layer: string;
  families: Record<string, ImageFamilyReadiness>;
  fingerprint: string;
  config_revision: number;
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

  const ensureRights = async (): Promise<string> => {
    const created = await jsonPost<{ id: string }>('/preparation/rights', {
      project_id: projectId,
      scope: 'asset',
      license_id: 'local-research-and-evaluation',
      evidence: '本地研究用途：由出图能力验证流程登记，用于本机生成三类小样。',
      purpose: 'LOCAL_RESEARCH_AND_EVALUATION_ONLY',
    });
    const grant = await jsonPost<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
      action: 'rights_approve',
      target_id: created.id,
    });
    await jsonPost(`/preparation/rights/${created.id}/approve`, {
      grant_id: grant.grant_id,
      snapshot: grant.snapshot,
      offline: true,
      evidence: '本地研究用途授权：只用于本机评估，不产生商业授权。',
    });
    return created.id;
  };

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

  const validate = async () => {
    setWorking(true);
    setProblem(null);
    setNote(null);
    try {
      const rightsId = await ensureRights();
      const result = await jsonPost<{ run_id: string }>('/setup/image-runtime/validate', {
        project_id: projectId,
        idempotency_key: `image-validate-${projectId}`,
        families: ['character', 'scene', 'prop'],
        rights_id: rightsId,
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
  const generatable = families.filter(([, row]) => row.can_generate).map(([name]) => FAMILY_LABELS[name] ?? name);
  const gigabytes = state ? (state.upstream.required_bytes / 1024 ** 3).toFixed(2) : '—';

  return (
    <section className="card section-card image-capability" aria-label="AI 素材出图能力">
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
        <div className="required-action" key={`${blocker.code}-${blocker.layer ?? ''}`}>
          <AlertTriangle size={14} className="required-icon" />
          <div className="required-text">
            <b>{BLOCKER_LABELS[blocker.code] ?? blocker.code}</b>
            <small>{blocker.detail ?? blocker.remediation ?? '按上面的提示处理后再试。'}</small>
          </div>
        </div>
      ))}
      {problem && (
        <div className="required-action" role="alert">
          <AlertTriangle size={14} className="required-icon" />
          <div className="required-text"><b>这一步没有完成</b><small>{problem}</small></div>
        </div>
      )}
      {note && (
        <div className="module-guide" role="status">
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
        <div className="module-guide" role="status">
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
      <button type="button" className="subtle-button" disabled={working || busy} onClick={registerInstall}>
        登记出图权重安装计划
      </button>
      <button type="button" className="must-button" disabled={working || busy || !state?.configured} onClick={validate}>
        {working ? <Loader2 size={13} /> : <ImageIcon size={13} />} 验证三类真实小样
      </button>
      {!state?.configured && (
        <p className="material-hint">
          还没有绑定受管渲染器，出图能力无法验证。先在「安装与下载」完成本机环境检测与受管实例绑定。
        </p>
      )}
    </section>
  );
}
