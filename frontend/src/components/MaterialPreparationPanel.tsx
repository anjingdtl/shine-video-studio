import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CircleCheck, Loader2, Sparkles } from 'lucide-react';
import { ImagePreview } from './ImagePreview';
import { StoryPreparationPanel } from './StoryPreparationPanel';
import { API, api, ERROR_CODE_LABELS, type Asset, type StyleVersion } from '../api';

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
  allowed_actions?: string[];
  brief?: { schema_version: string; appearance: string; must_keep: string[]; avoid: string[]; questions: string[] } | null;
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

function MaterialPreparationContent({ projectId, onMessage, onPromoted, frozenStyle }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onPromoted?: () => void;
  /** The frozen Style Bible, when there is one. */
  frozenStyle?: StyleVersion | null;
}) {
  const [state, setState] = useState<MaterialState | null>(null);
  const [documents, setDocuments] = useState<{ id: string; filename: string; sha256: string }[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [operationReport, setOperationReport] = useState('');
  const [briefEdits, setBriefEdits] = useState<Record<string, string>>({});
  const [textScope, setTextScope] = useState<{ scope_hash: string; document_sha256: string; transmission: string; roles: { role: string; configured: boolean; provider?: string; base_url?: string; model?: string; allowed?: boolean }[] } | null>(null);
  const [candidateGroup, setCandidateGroup] = useState('all');
  const [visibleRuns, setVisibleRuns] = useState(8);
  const [visibleCandidates, setVisibleCandidates] = useState(24);
  const [strategy, setStrategy] = useState<Record<string, MaterialStrategy>>({});
  const [direction, setDirection] = useState({ era: '', palette: '', lighting: '', texture: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sceneReport, setSceneReport] = useState<{ scene_key: string; status: string; error_code?: string; task_id?: string; external_outcome?: string }[]>([]);
  const [promotionReport, setPromotionReport] = useState<{ entity: string; ok: boolean; note: string }[]>([]);
  const [evaluation, setEvaluation] = useState<MaterialEvaluation | null>(null);
  const [stepReport, setStepReport] = useState<{ confirm?: string; generate?: string }>({});
  // The work the user is looking at right now. A response that belongs to the
  // previous work must never be written into the new one.
  const directionDirty = useRef(false);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;

  const load = useCallback(async () => {
    const requested = projectId;
    const [material, docs, masters] = await Promise.all([
      api<MaterialState>(`/projects/${requested}/material-preparation`),
      api<{ id: string; filename: string; sha256: string }[]>(`/projects/${requested}/documents`),
      api<Asset[]>(`/projects/${requested}/assets`),
    ]);
    if (activeProject.current !== requested) return;
    setState(material);
    setDocuments(docs);
    setAssets(masters);
    setDirection((current) => directionDirty.current ? current : material.visual_direction ?? current);
    setDocumentId((current) => current || material.document_id || docs[0]?.id || '');
  }, [projectId]);

  // Switching works must not carry the previous work's state into the new one.
  useEffect(() => {
    setState(null);
    setDocumentId('');
    setAssets([]);
    setSelected({});
    setBindings({});
    setDirection({ era: '', palette: '', lighting: '', texture: '', notes: '' });
    directionDirty.current = false;
    setPromotionReport([]);
    setProblem(null);
    setSceneReport([]);
    setStrategy({});
    setEvaluation(null);
    setStepReport({});
    void load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    setTextScope(null);
    if (!documentId) return;
    let current = true;
    api<NonNullable<typeof textScope>>(`/projects/${projectId}/material-preparation/text-consent/${documentId}`)
      .then((result) => { if (current) setTextScope(result); }).catch(() => undefined);
    return () => { current = false; };
  }, [projectId, documentId]);
  useEffect(() => { setSelected({}); setBriefEdits({}); setOperationReport(''); }, [projectId, state?.plan_revision_id]);

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

  // One default for one requirement, used everywhere: the user's own edit
  // wins, then the authoritative server snapshot, then "this project already
  // has an unconfirmed upload for this identity" — which must never silently
  // turn into a generation.
  const defaultStrategy = (requirement: MaterialRequirement): MaterialStrategy =>
    strategy[requirement.requirement_id]
    ?? requirement.strategy
    ?? (assets.some((a) => a.role === requirement.family && a.entity_id === requirement.entity_id && !a.locked)
      ? 'upload' : 'generate');

  const analyseScript = () => run(async () => {
    const document = documents.find((row) => row.id === documentId);
    if (!document) throw new Error('先导入或选择一份剧本文件，再分析剧本。');
    const prepared = await jsonPost<{
      preparations: unknown[];
      scenes: { scene_key: string; status: string; error_code?: string; task_id?: string; external_outcome?: string }[];
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

  // An unknown external result is kept, never silently resent.  The user can
  // deliberately ask for this one scene again; that is an explicit new
  // request with its own audit trail, and the old unknown row survives it.
  const replaceUnknownScene = (scene: { scene_key: string; task_id?: string }) => run(async () => {
    const document = documents.find((row) => row.id === documentId);
    if (!document) throw new Error('先导入或选择一份剧本文件，再分析剧本。');
    if (!scene.task_id) throw new Error('这一场没有可核对的未知任务。');
    const prepared = await jsonPost<{
      preparations: unknown[];
      scenes: { scene_key: string; status: string; error_code?: string; task_id?: string; external_outcome?: string }[];
    }>(`/projects/${projectId}/material-preparation/scenes/prepare`, {
      document_id: document.id,
      expected_document_hash: document.sha256,
      replace_unknown_task_id: scene.task_id,
      replacement_key: `scene-replace-${scene.task_id}`,
    });
    setSceneReport(prepared.scenes);
    setOperationReport(`场 ${scene.scene_key} 已按你的明确请求重新提交；原来的未知任务记录保留在台账里。`);
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
      strategy: defaultStrategy(requirement),
      entity_id: requirement.entity_id,
      asset_id: bindings[requirement.requirement_id] ?? requirement.bound_asset_id ?? null,
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
      .filter((requirement) => defaultStrategy(requirement) === 'generate')
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
    const targets = runs.filter((row) => row.kind === 'production').flatMap((row) => row.items);
    if (!Object.keys(selected).length) throw new Error('请先逐项选择候选图片。');
    const promoted: Record<string, string> = {};
    const outcomes: { entity: string; ok: boolean; note: string }[] = [];
    for (const item of targets) {
      if (!item.requirement_id) continue;
      const candidate = item.candidates.find((row) => row.candidate_id === selected[item.requirement_id!] && !row.formal_asset_id);
      if (!candidate) continue;
      // Every item carries its own one-use grant, so a partial success is
      // reported per item instead of being announced as one atomic batch.
      try {
        const grant = await jsonPost<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
          action: 'promote',
          target_id: candidate.candidate_id,
        });
        const result = await jsonPost<{ formal_asset_id?: string; asset_id?: string }>(`/candidate-assets/${candidate.candidate_id}/promote`, {
          grant_id: grant.grant_id,
          snapshot: grant.snapshot,
          offline: candidate.is_mock,
          evidence: '导演模式人工选用并晋升素材候选。',
        });
        if (result.formal_asset_id ?? result.asset_id) promoted[item.requirement_id] = (result.formal_asset_id ?? result.asset_id)!;
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
    if (Object.keys(promoted).length && state?.plan_revision_id && state.expected_snapshot) {
      await jsonPost(`/projects/${projectId}/material-preparation/confirm`, {
        plan_revision_id: state.plan_revision_id, expected_snapshot: state.expected_snapshot,
        decisions: requirements.map((r) => ({ requirement_id: r.requirement_id, entity_id: r.entity_id,
          strategy: promoted[r.requirement_id] ? 'reuse_master' : defaultStrategy(r),
          asset_id: promoted[r.requirement_id] ?? bindings[r.requirement_id] ?? r.bound_asset_id,
          brief_note: r.brief_note ?? '' })), visual_direction: direction,
      });
      setSelected({});
    }
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

  const decideText = (granted: boolean) => run(async () => {
    if (!textScope) return;
    const result = await jsonPost<NonNullable<typeof textScope>>(`/projects/${projectId}/material-preparation/text-consent`, {
      document_id: documentId, expected_document_hash: textScope.document_sha256,
      expected_scope_hash: textScope.scope_hash, granted,
    });
    setTextScope(result);
    setOperationReport(granted ? '已保存上表所列服务范围内的文字授权；只发送本剧本必要文字，不发送素材字节。'
      : '已撤回文字授权；后续调用停止，已经发送的请求无法撤回。');
  });
  const itemAction = (batch: MaterialRun, item: MaterialItem, action: 'resume' | 'reconcile' | 'regenerate') => run(async () => {
    if (action === 'regenerate') {
      if (!state?.confirmation || !item.requirement_id || !item.brief) throw new Error('尚无可修改的外观描述，请先恢复出品人步骤。');
      const appearance = briefEdits[item.item_id] ?? item.brief.appearance;
      const rightsId = await ensureRights();
      const requestBytes = new TextEncoder().encode(`${item.item_id}:${item.generation_revision}:${appearance}`);
      const requestHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', requestBytes)))
        .map((byte) => byte.toString(16).padStart(2, '0')).join('');
      await jsonPost(`/projects/${projectId}/material-preparation/regenerate`, {
        requirement_id: item.requirement_id, ...state.confirmation,
        idempotency_key: `regenerate-${requestHash}`,
        rights_id: rightsId, brief: { ...item.brief, appearance }, seed: item.generation_revision + 1,
      });
      setOperationReport('已创建新的生成版本；历史图片保留，需要重新选用。');
    } else {
      const result = await jsonPost<{ note?: string; refused?: unknown[] }>(`/projects/${projectId}/material-preparation/runs/${batch.run_id}/${action}`, {
        expected_state_version: batch.state_version, idempotency_key: `${action}-${item.item_id}-${batch.state_version}`,
        item_ids: [item.item_id],
      });
      setOperationReport(result.note ?? (result.refused?.length ? '此项未恢复，请刷新核对当前状态。' : '此项已恢复到队列。'));
    }
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
    (requirement) => defaultStrategy(requirement) === 'generate',
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
  const promotable = Object.keys(selected).length > 0;
  return (
    <section className="material-panel" aria-label="AI 素材准备">
      <div className="module-guide">
        <Sparkles size={13} />
        <div>
          <p><b>先读剧本、备素材，再冻结风格。</b>这一步只调用文字模型，不占用显卡；真正出图在「选择素材」阶段排队。</p>
          <p>AI 只生成候选。晋升为主素材需要你在下面明确确认，AI 不会替你决定。</p>
        </div>
      </div>

      {operationReport && <p role="status">{operationReport}</p>}
      {textScope && <details className="material-stage"><summary>文字模型发送授权</summary>
        <p>{textScope.transmission}</p>
        {textScope.roles.map((row) => <p key={row.role}>{({ producer: '出品人', scriptwriter: '编剧', director: '导演', cinematographer: '摄影指导', continuity: '场记', editor: '剪辑师' } as Record<string, string>)[row.role] ?? row.role} · {row.configured ? `${row.provider} / ${row.model} / ${row.base_url}` : '未配置'} · {row.allowed ? '当前允许' : '需要授权'}</p>)}
        <button type="button" disabled={busy} onClick={() => decideText(true)}>允许向上述模型发送本剧本必要文字</button>
        <button type="button" disabled={busy} onClick={() => decideText(false)}>撤回文字发送授权</button>
        <small>更换剧本修订、角色路由或模型配置后需要重新确认。授权不包含图片、音频或视频字节。</small>
      </details>}
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
                  {scene.status === 'NEEDS_REVIEW' && scene.task_id && <button type="button" className="text-button"
                    disabled={busy} onClick={() => replaceUnknownScene(scene)}>核对后重新请求此场</button>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>

      {documentId && <StoryPreparationPanel key={`${projectId}:${documentId}`} projectId={projectId}
        documentId={documentId} documentHash={documents.find((d) => d.id === documentId)?.sha256 ?? ''}
        styleFrozen={!!frozenStyle} onMaterials={() => { void load(); }} />}

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
                      value={defaultStrategy(requirement)}
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
                  {(strategy[requirement.requirement_id] ?? requirement.strategy) !== 'generate' && <label className="material-field">
                    <span>本项目素材与版本 · {requirement.entity_id}</span>
                    <select value={bindings[requirement.requirement_id] ?? requirement.bound_asset_id ?? ''}
                      onChange={(event) => setBindings({ ...bindings, [requirement.requirement_id]: event.target.value })}>
                      <option value="">请选择具体素材；未确认上传不会自动生成</option>
                      {assets.filter((a) => a.role === requirement.family && a.entity_id === requirement.entity_id).map((a) =>
                        <option key={a.id} value={a.id}>{a.filename} · {a.sha256.slice(0, 8)} · {a.locked ? '已确认' : '待确认上传'}</option>)}
                    </select>
                  </label>}
                </li>
              ))}
            </ul>
          )}
          <fieldset className="material-direction">
            <legend>视觉方向（先定方向，再冻结风格）</legend>
            <p className="material-hint">这五项会直接进入英文出图提示词，请用英文填写；中文会被提示词规则拒绝。</p>
            {(Object.keys(DIRECTION_LABELS) as (keyof typeof DIRECTION_LABELS)[]).map((field) => (
              <label key={field} className="material-field">
                <span>{DIRECTION_LABELS[field]}</span>
                <input
                  value={direction[field]}
                  onChange={(event) => { directionDirty.current = true; setDirection({ ...direction, [field]: event.target.value }); }}
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
          <label className="material-field"><span>候选素材分组</span><select value={candidateGroup} onChange={(e) => setCandidateGroup(e.target.value)}>
            <option value="all">全部候选</option><option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option>
          </select></label>
          {runs.slice(0, visibleRuns).map((row) => (
            <div className="material-run" key={row.run_id}>
              <b>{row.kind === 'validation' ? '出图能力验证' : '素材批次'} · {row.status}</b>
              <small>
                候选 {row.counts.candidate_ready}/{row.counts.total} · 失败 {row.counts.failed} · 待核对 {row.counts.needs_review}
              </small>
              {row.kind === 'validation' && <p className="material-hint">这是能力验证小样，不属于正式素材需求，不能在这里选用。</p>}
              <div className="media-grid" aria-label={`${row.kind === 'validation' ? '验证小样' : '正式候选'} ${row.run_id}`}>
                {row.items.filter((item) => candidateGroup === 'all' || item.family === candidateGroup).slice(0, visibleCandidates).map((item) => (
                  <article className="media-card" key={item.item_id}>
                    <b>{item.family || item.entity_id
                      ? [FAMILY_LABELS[item.family ?? ''] ?? item.family, item.entity_id].filter(Boolean).join(' · ')
                      : '能力验证小样'}</b>
                    <small>批次 {row.run_id.slice(0, 8)} · 生成 v{item.generation_revision}</small>
                    <span>{ITEM_STATUS_LABELS[item.status] ?? item.status}</span>
                    {item.error_code && <em>{reasonText(item.error_code)}</em>}
                    {row.kind === 'production' && item.allowed_actions?.includes('regenerate') && item.brief && <label className="material-field">
                      <span>修改 {item.entity_id} 的外观描述后生成新版本（外观描述用英文填写）</span>
                      <textarea value={briefEdits[item.item_id] ?? item.brief.appearance} maxLength={1200}
                        onChange={(e) => setBriefEdits({ ...briefEdits, [item.item_id]: e.target.value })} />
                    </label>}
                    {row.kind === 'production' && item.allowed_actions?.map((action) =>
                      ['resume', 'reconcile', 'regenerate'].includes(action) && (action !== 'regenerate' || item.brief) && <button type="button" key={action} disabled={busy}
                        onClick={() => itemAction(row, item, action as 'resume' | 'reconcile' | 'regenerate')}>
                        {action === 'resume' ? '恢复此项' : action === 'reconcile' ? '核对此项未知结果' : '再生成一版（保留旧图）'}
                      </button>)}
                    {item.candidates.map((candidate) => (
                      <div key={candidate.candidate_id} className={`material-candidate ${selected[item.requirement_id ?? ''] === candidate.candidate_id ? 'is-selected' : ''}`}>
                        <ImagePreview thumbnail={`${API}/candidate-assets/${candidate.candidate_id}/thumbnail`}
                          original={`${API}/candidate-assets/${candidate.candidate_id}/media`}
                          label={`${item.entity_id} · 生成 v${item.generation_revision} · 尝试 ${candidate.attempt_no}`} />
                        {candidate.is_mock && <b>离线测试候选，不能用于真实生产</b>}
                        <small>尝试 {candidate.attempt_no} · {candidate.formal_asset_id ? '已晋升' : candidate.status === 'READY_FOR_REVIEW' ? '待人工选用' : candidate.status}</small>
                        {row.kind === 'production' && item.requirement_id && requirements.some((r) => r.requirement_id === item.requirement_id) && !candidate.formal_asset_id && <label>
                          <input type="checkbox" disabled={candidate.status !== 'READY_FOR_REVIEW'} checked={selected[item.requirement_id] === candidate.candidate_id}
                            aria-label={`选择 ${item.entity_id} 生成v${item.generation_revision} ${candidate.candidate_id}`}
                            onChange={(event) => setSelected((current) => {
                              const next = { ...current };
                              if (event.target.checked) next[item.requirement_id!] = candidate.candidate_id;
                              else delete next[item.requirement_id!];
                              return next;
                            })} />选用此版本
                        </label>}
                      </div>
                    ))}
                  </article>
                ))}
              </div>
              {row.items.length > visibleCandidates && <button type="button" onClick={() => setVisibleCandidates((n) => n + 24)}>加载更多候选</button>}
              {row.allowed_actions.includes('cancel') && (
                <button type="button" className="subtle-button" disabled={busy} onClick={() => cancelRun(row.run_id)}>
                  停止这个批次
                </button>
              )}
            </div>
          ))}
          {runs.length > visibleRuns && <button type="button" onClick={() => setVisibleRuns((n) => n + 8)}>加载更多批次</button>}
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


export function MaterialPreparationPanel(props: Parameters<typeof MaterialPreparationContent>[0]) {
  return <MaterialPreparationContent key={props.projectId} {...props} />;
}
