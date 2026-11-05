import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CircleCheck, Loader2, Sparkles } from 'lucide-react';
import { api, ERROR_CODE_LABELS, type StyleVersion } from '../api';

export type MaterialStrategy = 'reuse_master' | 'upload' | 'generate';

export type MaterialRequirement = {
  requirement_id: string;
  family: 'character' | 'scene' | 'prop';
  entity_id: string;
  strategy: MaterialStrategy | null;
  status: string;
  bound_asset_id: string | null;
  brief_note: string;
  description: string;
};

export type MaterialCandidate = {
  candidate_id: string;
  status: string;
  content_sha256: string | null;
  is_mock: boolean;
  formal_asset_id: string | null;
  attempt_no: number;
};

export type MaterialItem = {
  item_id: string;
  requirement_id: string | null;
  family: string | null;
  entity_id: string | null;
  generation_revision: number;
  status: string;
  error_code: string | null;
  candidates: MaterialCandidate[];
};

export type MaterialRun = {
  run_id: string;
  status: string;
  kind: string;
  state_version: number;
  counts: { total: number; candidate_ready: number; failed: number; needs_review: number; queued: number; running: number };
  items: MaterialItem[];
  allowed_actions: string[];
};

export type MaterialEvaluation = {
  materials_ready: boolean;
  requirements: { requirement_id: string; family: string; entity_id: string; asset_id: string | null; status: string; reason: string | null }[];
  missing: { requirement_id: string; family: string; entity_id: string; asset_id: string | null; status: string; reason: string | null }[];
  reason: string | null;
};

export type MaterialState = {
  project_id: string;
  stage: string;
  plan_revision_id: string | null;
  plan_status: string | null;
  document_id: string | null;
  visual_direction: { era: string; palette: string; lighting: string; texture: string; notes: string } | null;
  confirmation: { confirmation_id: string; confirmation_hash: string } | null;
  expected_snapshot?: string | null;
  materials_ready?: boolean;
  material_evaluation?: MaterialEvaluation;
  requirements: MaterialRequirement[];
  runs: MaterialRun[];
  blockers: { code: string; note: string }[];
};

const FAMILY_LABELS: Record<string, string> = {
  character: '人物',
  scene: '场景',
  prop: '道具',
};

const DIRECTION_LABELS = {
  era: '时代',
  palette: '色调',
  lighting: '光线',
  texture: '质感',
  notes: '补充说明',
} as const;

const ITEM_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中',
  BRIEF_RUNNING: '正在写外观描述',
  BRIEF_READY: '外观描述就绪',
  WAITING_GPU: '等待显卡',
  SUBMIT_INTENT: '已提交意图',
  RUNNING: '渲染中',
  OUTPUT_STORED: '已存图',
  QC_PENDING: '技术质检中',
  CANDIDATE_READY: '候选可用',
  FAILED: '失败',
  NEEDS_REVIEW: '需要核对',
  CANCELLED: '已取消',
};


function reasonText(code: string | null | undefined): string {
  if (!code) return '';
  return ERROR_CODE_LABELS[code] ?? `这一步没有完成（${code}）。请到专业设置查看专业日志。`;
}

export function MaterialPreparationPanel({ projectId, onMessage, onPromoted, frozenStyle }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onPromoted?: () => void;
  /** The frozen Style Bible, when there is one. */
  frozenStyle?: StyleVersion | null;
}) {
  const [state, setState] = useState<MaterialState | null>(null);
  const [documents, setDocuments] = useState<{ id: string; filename: string; sha256: string }[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [strategy, setStrategy] = useState<Record<string, MaterialStrategy>>({});
  const [direction, setDirection] = useState({ era: '', palette: '', lighting: '', texture: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sceneReport, setSceneReport] = useState<{ scene_key: string; status: string; error_code?: string }[]>([]);
  const [promotionReport, setPromotionReport] = useState<{ entity: string; ok: boolean; note: string }[]>([]);
  const [evaluation, setEvaluation] = useState<MaterialEvaluation | null>(null);
  const [stepReport, setStepReport] = useState<{ confirm?: string; generate?: string }>({});
  // The work the user is looking at right now. A response that belongs to the
  // previous work must never be written into the new one.
  const activeProject = useRef(projectId);
  activeProject.current = projectId;

  const load = useCallback(async () => {
    const requested = projectId;
    const [material, docs] = await Promise.all([
      api<MaterialState>(`/projects/${requested}/material-preparation`),
      api<{ id: string; filename: string; sha256: string }[]>(`/projects/${requested}/documents`),
    ]);
    if (activeProject.current !== requested) return;
    setState(material);
    setDocuments(docs);
    setDocumentId((current) => current || material.document_id || docs[0]?.id || '');
  }, [projectId]);

  // Switching works must not carry the previous work's state into the new one.
  useEffect(() => {
    setState(null);
    setProblem(null);
    setSceneReport([]);
    setStrategy({});
    setEvaluation(null);
    setStepReport({});
    void load().catch(() => undefined);
  }, [load]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setProblem(null);
    try {
      await action();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setProblem(message);
      onMessage?.(message);
    } finally {
      setBusy(false);
    }
  };
  const jsonPost = <T,>(path: string, body: unknown): Promise<T> => api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const analyseScript = () => run(async () => {
    const document = documents.find((row) => row.id === documentId);
    if (!document) throw new Error('先导入或选择一份剧本文件，再分析剧本。');
    const prepared = await jsonPost<{
      preparations: unknown[];
      scenes: { scene_key: string; status: string; error_code?: string }[];
    }>(`/projects/${projectId}/material-preparation/scenes/prepare`, {
      document_id: document.id,
      expected_document_hash: document.sha256,
    });
    setSceneReport(prepared.scenes);
    await jsonPost(`/projects/${projectId}/material-preparation/analyze`, {
      document_id: document.id,
      expected_document_hash: document.sha256,
      idempotency_key: `material-analyze-${document.sha256.slice(0, 16)}`,
      scene_preparations: prepared.preparations,
    });
    await load();
  });

  const ensureRights = async (): Promise<string> => {
    const created = await jsonPost<{ id: string }>('/preparation/rights', {
      project_id: projectId,
      scope: 'asset',
      license_id: 'local-research-and-evaluation',
      evidence: '本地研究用途：由导演模式素材准备流程登记，用于本机生成素材候选。',
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

  const confirmAndGenerate = () => run(async () => {
    if (!state?.plan_revision_id || !state.expected_snapshot) {
      throw new Error('先分析剧本，生成需求清单。');
    }
    const decisions = requirements.map((requirement) => ({
      requirement_id: requirement.requirement_id,
      strategy: strategy[requirement.requirement_id] ?? 'generate',
      entity_id: requirement.entity_id,
      asset_id: requirement.bound_asset_id ?? null,
      brief_note: requirement.brief_note,
    }));
    const confirmed = await jsonPost<{ confirmation_id: string; confirmation_hash: string }>(
      `/projects/${projectId}/material-preparation/confirm`,
      {
        plan_revision_id: state.plan_revision_id,
        expected_snapshot: state.expected_snapshot,
        decisions,
        visual_direction: direction,
      },
    );
    // The confirmation and the batch are two separate facts. Confirming the
    // list does not mean a batch was created, so the page says which one
    // happened instead of announcing a single success.
    setStepReport((current) => ({ ...current, confirm: `清单已确认（${decisions.length} 项）。` }));
    const generatable = requirements
      .filter((requirement) => (strategy[requirement.requirement_id] ?? 'generate') === 'generate')
      .map((requirement) => requirement.requirement_id);
    if (!generatable.length) {
      setStepReport((current) => ({
        ...current,
        generate: '这一版没有需要生成的素材，保留现有主素材即可继续。',
      }));
      await load();
      return;
    }
    const rightsId = await ensureRights();
    await jsonPost(`/projects/${projectId}/material-preparation/generate`, {
      confirmation_id: confirmed.confirmation_id,
      confirmation_hash: confirmed.confirmation_hash,
      requirement_ids: generatable,
      idempotency_key: `material-generate-${confirmed.confirmation_id.slice(0, 12)}`,
      rights_id: rightsId,
      mode: 'real',
    });
    setStepReport((current) => ({
      ...current,
      generate: `已排队 ${generatable.length} 项出图，后台生成中。`,
    }));
    await load();
  });

  const promoteSelected = () => run(async () => {
    const target = runs.find((row) => row.kind === 'production') ?? runs[0];
    if (!target) throw new Error('还没有可用的素材批次。');
    const outcomes: { entity: string; ok: boolean; note: string }[] = [];
    for (const item of target.items) {
      const candidate = item.candidates.find((row) => !row.formal_asset_id);
      if (!candidate) continue;
      // Every item carries its own one-use grant, so a partial success is
      // reported per item instead of being announced as one atomic batch.
      try {
        const grant = await jsonPost<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
          action: 'promote',
          target_id: candidate.candidate_id,
        });
        await jsonPost(`/candidate-assets/${candidate.candidate_id}/promote`, {
          grant_id: grant.grant_id,
          snapshot: grant.snapshot,
          offline: true,
          evidence: '导演模式人工选用并晋升素材候选。',
        });
        outcomes.push({ entity: `${item.family ?? ''} · ${item.entity_id ?? ''}`, ok: true, note: '已晋升为主素材' });
      } catch (error) {
        outcomes.push({
          entity: `${item.family ?? ''} · ${item.entity_id ?? ''}`,
          ok: false,
          note: error instanceof Error ? error.message : String(error),
        });
      }
    }
    setPromotionReport(outcomes);
    // The completion condition is judged by the server; the page never claims a
    // readiness it cannot prove.
    const judged = await jsonPost<MaterialEvaluation>(
      `/projects/${projectId}/material-preparation/complete`,
      {},
    );
    setEvaluation(judged);
    await load();
    onPromoted?.();
  });

  const cancelRun = (runId: string) => run(async () => {
    const target = runs.find((row) => row.run_id === runId);
    await jsonPost(`/projects/${projectId}/material-preparation/runs/${runId}/cancel`, {
      expected_state_version: target?.state_version ?? 0,
      idempotency_key: `material-cancel-${runId}`,
    });
    await load();
  });

  const requirements = state?.requirements ?? [];
  const runs = state?.runs ?? [];
  // The frozen Style Bible and the confirmed material direction are two
  // contracts. They are shown side by side; whether they agree is the user's
  // call, and a frozen contract is never rewritten behind their back.
  const styleDirection = state?.visual_direction ?? null;
  const styleRows = frozenStyle && styleDirection
    ? ([
        { label: '时代', direction: styleDirection.era, style: '' },
        { label: '色调', direction: styleDirection.palette, style: frozenStyle.snapshot.color_palette ?? '' },
        { label: '光线', direction: styleDirection.lighting, style: frozenStyle.snapshot.lighting_tone ?? '' },
        { label: '质感', direction: styleDirection.texture, style: frozenStyle.snapshot.lens_texture ?? '' },
      ] as { label: string; direction: string; style: string }[])
    : [];
  const pendingGeneration = requirements.filter(
    (requirement) => (strategy[requirement.requirement_id] ?? 'generate') === 'generate',
  ).length;
  const confirmLabel = pendingGeneration > 0 ? '确认清单并补齐' : '确认清单并继续';
  const running = runs.some((row) => !['COMPLETED', 'PARTIAL_FAILED', 'NEEDS_REVIEW', 'CANCELLED'].includes(row.status));

  // A batch runs for minutes. The page re-queries the durable ledger instead
  // of guessing, and stops polling once nothing is in flight.
  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [running, load]);
  const stage = state?.stage ?? 'read_script';
  const stageIndex = stage === 'select_materials' ? 2 : stage === 'confirm_requirements' ? 1 : 0;
  const promotable = runs.some((row) => row.items.some((item) => item.candidates.some((candidate) => !candidate.formal_asset_id)));
  return (
    <section className="material-panel" aria-label="AI 素材准备">
      <div className="module-guide">
        <Sparkles size={13} />
        <div>
          <p><b>先读剧本、备素材，再冻结风格。</b>这一步只调用文字模型，不占用显卡；真正出图在「选择素材」阶段排队。</p>
          <p>AI 只生成候选。晋升为主素材需要你在下面明确确认，AI 不会替你决定。</p>
        </div>
      </div>

      {problem && (
        <div className="required-action" role="alert">
          <AlertTriangle size={14} className="required-icon" />
          <div className="required-text"><b>这一步没有完成</b><small>{problem}</small></div>
        </div>
      )}
      {styleRows.length > 0 && (
        <div className="required-action">
          <AlertTriangle size={14} className="required-icon" />
          <div className="required-text">
            <b>影视风格已冻结（版本 {frozenStyle?.version}），素材视觉方向要你亲自对照</b>
            <small>
              {styleRows.map((row) => (
                <span key={row.label}>
                  {row.label}：素材方向「{row.direction || '未填'}」／已冻结风格「{row.style || '未填'}」
                </span>
              ))}
              两者不一致时，请在下方「影视风格」新建一个版本；已冻结的合同不会被自动修改。
            </small>
          </div>
        </div>
      )}
      {(state?.blockers ?? []).map((blocker) => (
        <div className="required-action" key={blocker.code}>
          <AlertTriangle size={14} className="required-icon" />
          <div className="required-text"><b>{blocker.code}</b><small>{blocker.note}</small></div>
        </div>
      ))}

      <ol className="material-stages" aria-label="素材准备阶段">
        {([
          { id: 'read_script', label: '① 读剧本' },
          { id: 'confirm_requirements', label: '② 确认需求' },
          { id: 'select_materials', label: '③ 选择素材' },
        ] as const).map((item, index) => (
          <li key={item.id} className={index === stageIndex ? 'now' : index < stageIndex ? 'done' : ''}>
            <b>{item.label}</b>
          </li>
        ))}
      </ol>

      <details className="material-stage" open={stageIndex === 0}>
        <summary>① 读剧本{state?.plan_status ? ` · ${state.plan_status}` : ''}</summary>
        <div className="material-stage-body">
          {documents.length === 0 && (
            <p className="material-hint">这个作品还没有剧本文件。先到「AI导演制作」上传剧本，或在上面选择已有文件。</p>
          )}
          <label className="material-field">
            <span>剧本文件</span>
            <select value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
              <option value="">请选择</option>
              {documents.map((document) => (
                <option key={document.id} value={document.id}>{document.filename}</option>
              ))}
            </select>
          </label>
          <button type="button" className="must-button" disabled={busy || !documentId} onClick={analyseScript}>
            {busy ? <Loader2 size={13} /> : <Sparkles size={13} />} 分析剧本
          </button>
          {sceneReport.length > 0 && (
            <ul className="material-scene-list">
              {sceneReport.map((scene) => (
                <li key={scene.scene_key}>
                  <span>{scene.scene_key}</span>
                  <span>{scene.status === 'PREPARED' || scene.status === 'REUSED' ? '已拆解' : scene.status}</span>
                  {scene.error_code && <em>{reasonText(scene.error_code)}</em>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>

      <details className="material-stage" open={stageIndex === 1}>
        <summary>② 确认需求 · {requirements.length} 项</summary>
        <div className="material-stage-body">
          {requirements.length === 0 ? (
            <p className="material-hint">还没有需求清单，先在上一步分析剧本。</p>
          ) : (
            <ul className="material-requirement-list">
              {requirements.map((requirement) => (
                <li key={requirement.requirement_id}>
                  <b>{FAMILY_LABELS[requirement.family] ?? requirement.family} · {requirement.entity_id}</b>
                  <small>{requirement.description}</small>
                  <label className="material-field">
                    <span>素材来源</span>
                    <select
                      value={strategy[requirement.requirement_id] ?? 'generate'}
                      onChange={(event) => setStrategy({
                        ...strategy,
                        [requirement.requirement_id]: event.target.value as MaterialStrategy,
                      })}
                    >
                      <option value="generate">生成候选（AI 出图）</option>
                      <option value="upload">我会上传</option>
                      <option value="reuse_master">已有主素材</option>
                    </select>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <fieldset className="material-direction">
            <legend>视觉方向（先定方向，再冻结风格）</legend>
            {(Object.keys(DIRECTION_LABELS) as (keyof typeof DIRECTION_LABELS)[]).map((field) => (
              <label key={field} className="material-field">
                <span>{DIRECTION_LABELS[field]}</span>
                <input
                  value={direction[field]}
                  onChange={(event) => setDirection({ ...direction, [field]: event.target.value })}
                />
              </label>
            ))}
          </fieldset>
          {stepReport.confirm && <p className="material-step-report">{stepReport.confirm}</p>}
          {stepReport.generate && <p className="material-step-report">{stepReport.generate}</p>}
          <button type="button" className="must-button" disabled={busy || requirements.length === 0} onClick={confirmAndGenerate}>
            {confirmLabel}
          </button>
        </div>
      </details>

      <details className="material-stage" open={stageIndex === 2}>
        <summary>③ 选择素材 · {runs.length} 个批次</summary>
        <div className="material-stage-body">
          {runs.length === 0 && <p className="material-hint">还没有出图批次。确认清单后，生成的项目会在这里排队。</p>}
          {runs.map((row) => (
            <div className="material-run" key={row.run_id}>
              <b>{row.kind === 'validation' ? '出图能力验证' : '素材批次'} · {row.status}</b>
              <small>
                候选 {row.counts.candidate_ready}/{row.counts.total} · 失败 {row.counts.failed} · 待核对 {row.counts.needs_review}
              </small>
              <ul>
                {row.items.map((item) => (
                  <li key={item.item_id}>
                    <span>{item.family} · {item.entity_id}</span>
                    <span>{ITEM_STATUS_LABELS[item.status] ?? item.status}</span>
                    {item.error_code && <em>{reasonText(item.error_code)}</em>}
                    {item.candidates.map((candidate) => (
                      <span key={candidate.candidate_id} className="material-candidate">
                        第 {candidate.attempt_no} 版 · {candidate.formal_asset_id ? '已晋升' : '待选用'}
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
              {row.allowed_actions.includes('cancel') && (
                <button type="button" className="subtle-button" disabled={busy} onClick={() => cancelRun(row.run_id)}>
                  停止这个批次
                </button>
              )}
            </div>
          ))}
          <button type="button" className="must-button" disabled={busy || !promotable} onClick={promoteSelected}>
            确认使用所选素材
          </button>
          {evaluation && (
            <div className={evaluation.materials_ready ? 'material-ready' : 'required-action'} role="status">
              {evaluation.materials_ready ? (
                <><CircleCheck size={14} className="required-icon" />
                  <div className="required-text"><b>素材齐备</b><small>每一项必需素材都已绑定已批准并锁定的主素材，可以继续冻结风格。</small></div>
                </>
              ) : (
                <>
                  <AlertTriangle size={14} className="required-icon" />
                  <div className="required-text">
                    <b>还有必需项没有绑定有效主素材</b>
                    <small>
                      {evaluation.missing.map((entry) => (
                        <span key={entry.requirement_id}>
                          {entry.family} · {entry.entity_id}：{reasonText(entry.reason)}
                        </span>
                      ))}
                    </small>
                  </div>
                </>
              )}
            </div>
          )}
          {promotionReport.length > 0 && (
            <ul className="material-promotion-report">
              {promotionReport.map((row, index) => (
                <li key={`${row.entity}-${index}`} className={row.ok ? 'ok' : 'failed'}>
                  <span>{row.entity}</span>
                  <span>{row.note}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>
    </section>
  );
}
