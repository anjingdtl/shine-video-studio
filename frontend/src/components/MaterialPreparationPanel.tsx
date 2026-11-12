import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CircleCheck, Loader2, Sparkles } from 'lucide-react';
import { ImagePreview } from './ImagePreview';
import { StoryPreparationPanel } from './StoryPreparationPanel';
import { ScriptTextConsent } from './ScriptTextConsent';
import { API, api, ERROR_CODE_LABELS, type Asset, type Shot, type StyleVersion } from '../api';
import { assignLibraryVersion, searchLibraryForProject } from '../asset-center/api';
import { useStageRail, useRailPresent } from './StageRail';
import type { RailAction } from './ActionRail';
import type { LibraryAsset, LibraryVersion } from '../asset-center/api';

/** Plan and batch states in the words of the page, not the words of the ledger. */
const PLAN_STATUS_LABELS: Record<string, string> = {
  DRAFT: '待确认需求',
  CONFIRMED: '已确认清单',
  MATERIALS_READY: '素材已齐备',
  NEEDS_CLARIFICATION: '需要澄清',
};
const RUN_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '生成中', COMPLETED: '已完成',
  PARTIAL_FAILED: '部分失败', NEEDS_REVIEW: '待核对', CANCELLED: '已取消',
};

export type MaterialStrategy = 'reuse_master' | 'upload' | 'generate' | 'omit_prop';

export type MaterialRequirement = {
  requirement_id: string;
  requirement_hash: string;
  family: 'character' | 'scene' | 'prop';
  entity_id: string;
  canonical_entity_id?: string | null;
  strategy: MaterialStrategy | null;
  status: string;
  bound_asset_id: string | null;
  brief_note: string;
  description: string;
  carrier?: string | null;
  carrier_variants?: string[];
  core_prop_reason?: string;
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
  carrier?: string | null;
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

export type SceneStatus = {
  scene_key: string;
  title: string;
  task_id: string | null;
  status: string;
  error_code: string | null;
  external_outcome: string | null;
  phase: string | null;
  state_version: number | null;
  reconciliation: string | null;
  dead_worker: boolean;
  created_at: string | null;
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

/** 道具需求的内容载体类型（与后端 contracts.MaterialCarrier 对齐）。 */
const propCarrierLabels: Record<string, string> = {
  object: '实物',
  paper: '纸质文书',
  screen: '屏幕显示',
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

const SCENE_PHASE_LABELS: Record<string, string> = {
  PREPARING: '准备中', WAITING_RESOURCE: '等待资源', PREPARING_PROVIDER: '准备调用',
  WAITING_PROVIDER: '等待模型回答',
};

function sceneStatusLabel(scene: SceneStatus): string {
  switch (scene.status) {
    case 'NOT_STARTED': return '未分析';
    case 'RUNNING': return `分析中 · ${SCENE_PHASE_LABELS[scene.phase ?? ''] ?? '进行中'}`;
    case 'SUCCEEDED': return '已拆解';
    case 'NEEDS_REVIEW': return scene.external_outcome === 'UNKNOWN' ? '结果未知 · 待核对' : '待核对';
    case 'REJECTED': case 'FAILED': return '失败';
    case 'SUPERSEDED':
      if (scene.reconciliation === 'USER_AUTHORIZED_NEW_REQUEST') return '已授权重发 · 点「分析剧本」继续';
      if (scene.reconciliation === 'NOT_SENT_PROVEN') return '已证明未发送 · 点「分析剧本」继续';
      return '已退役';
    default: return scene.status;
  }
}

function MaterialPreparationContent({ projectId, onMessage, onPromoted, frozenStyle, documentsVersion, shots = [] }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onPromoted?: () => void;
  /** The frozen Style Bible, when there is one. */
  frozenStyle?: StyleVersion | null;
  /** Bumped when a script was imported or generated, so the list reloads. */
  documentsVersion?: number;
  shots?: Shot[];
}) {
  const [state, setState] = useState<MaterialState | null>(null);
  const [documents, setDocuments] = useState<{ id: string; filename: string; sha256: string }[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [operationReport, setOperationReport] = useState('');
  const [briefEdits, setBriefEdits] = useState<Record<string, string>>({});
  const [candidateGroup, setCandidateGroup] = useState('all');
  const [visibleCandidates, setVisibleCandidates] = useState(24);
  const [strategy, setStrategy] = useState<Record<string, MaterialStrategy>>({});
  const [direction, setDirection] = useState({ era: '', palette: '', lighting: '', texture: '', notes: '' });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sceneStatus, setSceneStatus] = useState<SceneStatus[] | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const [promotionReport, setPromotionReport] = useState<{ entity: string; ok: boolean; note: string }[]>([]);
  const [librarySearchRequirement, setLibrarySearchRequirement] = useState<string | null>(null);
  const [librarySearchResults, setLibrarySearchResults] = useState<Array<{ asset: LibraryAsset; version: LibraryVersion }>>([]);
  const [librarySearchError, setLibrarySearchError] = useState('');
  const [librarySearchBusy, setLibrarySearchBusy] = useState(false);
  /** The assignment receipt that can be turned into a per-shot adoption. */
  const [adoptionReceipt, setAdoptionReceipt] = useState<null | {
    requirementId: string; projectAssetId: string; libraryVersionId?: string;
    role: string; entity: string; key: string;
  }>(null);
  const [adoptionPreview, setAdoptionPreview] = useState<null | {
    shot_count: number; blocked: Array<Record<string, any>>;
    shots: Array<Record<string, any>>;
  }>(null);
  const [adoptionShotId, setAdoptionShotId] = useState('');
  const [adoptionBusy, setAdoptionBusy] = useState(false);
  const [adoptionReport, setAdoptionReport] = useState('');
  const [evaluation, setEvaluation] = useState<MaterialEvaluation | null>(null);
  const [stepReport, setStepReport] = useState<{ confirm?: string; generate?: string }>({});
  /** The stage the person has open; null means "follow the server". */
  // Which stage fold the page opens with. A person's own toggles are native and
  // never written back here, so this only tracks "the server moved the journey".
  const [openStage] = useState<number | null>(null);
  // The work the user is looking at right now. A response that belongs to the
  // previous work must never be written into the new one.
  const directionDirty = useRef(false);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;

  async function searchGlobalMaterial(requirement: MaterialRequirement,
                                      scope: 'project' | 'library' = 'project') {
    setLibrarySearchRequirement(requirement.requirement_id);
    setLibrarySearchResults([]);
    setLibrarySearchError('');
    setLibrarySearchBusy(true);
    try {
      const result = await searchLibraryForProject(projectId, requirement.entity_id, requirement.family, scope);
      if (activeProject.current === projectId) {
        setLibrarySearchResults(result.items);
        // Another production's stills are not candidates for this one, so the
        // default search stays inside this project and says why it is empty.
        if (!result.items.length) {
          setLibrarySearchError(scope === 'project'
            ? '本项目还没有用过素材库里的版本。需要借鉴其他作品的素材时，点「搜索全部素材库」。'
            : '素材库里没有匹配的已审核版本。');
        }
      }
    } catch (error) {
      setLibrarySearchError(error instanceof Error ? error.message : String(error));
    } finally { setLibrarySearchBusy(false); }
  }

  async function assignGlobalMaterial(requirement: MaterialRequirement, result: { asset: LibraryAsset; version: LibraryVersion }) {
    setLibrarySearchBusy(true);
    setLibrarySearchError('');
    try {
      const assignment = await assignLibraryVersion({
        project_id: projectId,
        library_version_id: result.version.id,
        role: requirement.family,
        entity_id: requirement.entity_id,
        idempotency_key: crypto.randomUUID(),
        confirmed: true,
        requirement_context: {
          project_id: projectId,
          requirement_id: requirement.requirement_id,
          requirement_hash: requirement.requirement_hash,
        },
      });
      if (activeProject.current !== projectId) return;
      setBindings((current) => ({ ...current, [requirement.requirement_id]: assignment.project_asset_id }));
      setStrategy((current) => ({ ...current, [requirement.requirement_id]: 'reuse_master' }));
      setLibrarySearchRequirement(null);
      setAdoptionReceipt({
        requirementId: requirement.requirement_id,
        projectAssetId: assignment.project_asset_id,
        libraryVersionId: result.version.id,
        role: requirement.family,
        entity: requirement.entity_id,
        key: crypto.randomUUID(),
      });
      setAdoptionPreview(null);
      setAdoptionShotId('');
      setAdoptionReport('');
      setOperationReport(`已把全局素材 V${result.version.ordinal} 复制为本项目独立素材身份；请继续确认需求清单，或直接「用于这些镜头」。`);
      await load();
    } catch (error) {
      setLibrarySearchError(error instanceof Error ? error.message : String(error));
    } finally { setLibrarySearchBusy(false); }
  }

  function openProjectAdoption(requirement: MaterialRequirement, asset: Asset) {
    setAdoptionReceipt({
      requirementId: requirement.requirement_id, projectAssetId: asset.id,
      role: requirement.family, entity: requirement.entity_id, key: crypto.randomUUID(),
    });
    setAdoptionShotId('');
    setAdoptionPreview(null);
    setAdoptionReport('');
  }

  async function previewAdoption() {
    if (!adoptionReceipt) return;
    setAdoptionBusy(true);
    setAdoptionReport('');
    try {
      const preview = await api<{
        shot_count: number; blocked: Array<Record<string, any>>;
        shots: Array<Record<string, any>>;
      }>('/asset-center/adoptions/preview', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId,
          library_version_id: adoptionReceipt.libraryVersionId,
          project_asset_id: adoptionReceipt.projectAssetId,
          role: adoptionReceipt.role,
          entity_id: adoptionReceipt.entity,
          shot_ids: adoptionShotId ? [adoptionShotId] : undefined,
        }),
      });
      if (activeProject.current !== projectId) return;
      setAdoptionPreview(preview);
    } catch (error) {
      setAdoptionReport(error instanceof Error ? error.message : String(error));
    } finally { setAdoptionBusy(false); }
  }

  async function applyAdoption() {
    if (!adoptionReceipt || !adoptionPreview) return;
    const usable = adoptionPreview.shots.filter((row) => !row.conflict);
    if (!usable.length) {
      setAdoptionReport('这些镜头都不能直接采用：先处理冲突（正在拍摄的镜头先收束，已确认导演包先生成新版本）。');
      return;
    }
    setAdoptionBusy(true);
    try {
      const result = await api<{
        adopted_count: number; reused_count: number;
        shots: Array<Record<string, any>>; replayed: boolean;
      }>('/asset-center/adoptions', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId,
          library_version_id: adoptionReceipt.libraryVersionId,
          project_asset_id: adoptionReceipt.projectAssetId,
          shot_ids: usable.map((row) => row.shot_id),
          role: adoptionReceipt.role,
          entity_id: adoptionReceipt.entity,
          // The preview the person saw is the exact revision set they confirm.
          expected_shot_revisions: Object.fromEntries(usable.map((row) => [row.shot_id, row.revision])),
          idempotency_key: adoptionReceipt.key,
          confirmed: true,
        }),
      });
      if (activeProject.current !== projectId) return;
      setAdoptionPreview(null);
      setAdoptionReceipt({ ...adoptionReceipt, key: crypto.randomUUID() });
      setAdoptionReport(
        `已采用到 ${result.adopted_count} 个镜头（另有 ${result.reused_count} 个原本已绑定）。`
        + (result.adopted_count ? '镜头版本已更新；当前导演包需要用最新镜头重新生成。' : ''),
      );
      await load();
      onPromoted?.();
    } catch (error) {
      setAdoptionReport(error instanceof Error ? error.message : String(error));
    } finally { setAdoptionBusy(false); }
  }

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

  // The scene analysis is one long synchronous request on the server. Its
  // per-scene state lives in the ledger, so the page reads it live and after
  // any refresh instead of keeping the outcome only in browser memory.
  const loadScenes = useCallback(async () => {
    if (!documentId) return;
    const requested = projectId;
    const result = await api<{ scenes: SceneStatus[] }>(
      `/projects/${requested}/material-preparation/scenes/status?document_id=${documentId}`);
    if (activeProject.current !== requested) return;
    setSceneStatus(result.scenes);
  }, [projectId, documentId]);
  useEffect(() => { void loadScenes().catch(() => undefined); }, [loadScenes]);
  const scenesRunning = (sceneStatus ?? []).some((scene) => scene.status === 'RUNNING');
  useEffect(() => {
    if (!analysing && !scenesRunning) return undefined;
    const timer = window.setInterval(() => { void loadScenes().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [analysing, scenesRunning, loadScenes]);

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
    setSceneStatus(null);
    setStrategy({});
    setEvaluation(null);
    setStepReport({});
    void load().catch(() => undefined);
  }, [load]);

  // A newly imported or generated script (the list is newest first): reload
  // and select it, so the person continues with the script just provided.
  useEffect(() => {
    if (!documentsVersion) return;
    const requested = projectId;
    api<{ id: string; filename: string; sha256: string }[]>(`/projects/${requested}/documents`)
      .then((docs) => {
        if (activeProject.current !== requested) return;
        setDocuments(docs);
        if (docs[0]) setDocumentId(docs[0].id);
      })
      .catch(() => undefined);
  }, [documentsVersion, projectId]);
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
      ? 'upload' : requirement.family === 'prop' && !requirement.core_prop_reason ? 'omit_prop' : 'generate');

  const requirementDecision = (requirement: MaterialRequirement, promotedAssetId?: string) => {
    const id = requirement.requirement_id;
    const chosen = promotedAssetId ?? bindings[id];
    const decision = strategy[id] === 'omit_prop' ? 'omit_prop' : chosen ? 'reuse_master' : defaultStrategy(requirement);
    return {
      requirement_id: id,
      entity_id: requirement.entity_id,
      strategy: decision,
      asset_id: decision === 'omit_prop' ? null : chosen || requirement.bound_asset_id || null,
      brief_note: requirement.brief_note ?? '',
    };
  };

  const analyseScript = () => run(async () => {
    const document = documents.find((row) => row.id === documentId);
    if (!document) throw new Error('先导入或选择一份剧本文件，再分析剧本。');
    setAnalysing(true);
    try {
      const prepared = await jsonPost<{ preparations: unknown[] }>(
        `/projects/${projectId}/material-preparation/scenes/prepare`, {
          document_id: document.id,
          expected_document_hash: document.sha256,
        });
      await loadScenes();
      await jsonPost(`/projects/${projectId}/material-preparation/analyze`, {
        document_id: document.id,
        expected_document_hash: document.sha256,
        idempotency_key: `material-analyze-${document.sha256.slice(0, 16)}`,
        scene_preparations: prepared.preparations,
      });
      await load();
    } finally {
      setAnalysing(false);
      await loadScenes().catch(() => undefined);
    }
  });

  const reconcileUnknownScene = (scene: { scene_key: string; task_id?: string | null; state_version?: number | null }) => run(async () => {
    if (!scene.task_id || scene.state_version === undefined || scene.state_version === null) throw new Error('这一场缺少可核对的任务版本。');
    const result = await jsonPost<{ outcome: string; external_outcome?: string; resubmittable: boolean }>(
      `/projects/${projectId}/material-preparation/scenes/${scene.task_id}/reconcile`,
      { expected_state_version: scene.state_version },
    );
    setOperationReport(result.outcome === 'NOT_SENT_PROVEN'
      ? `场 ${scene.scene_key} 已证明原请求未发出；如需继续，请单独点击「分析剧本」。`
      : result.outcome === 'RESULT_ADOPTED'
        ? `场 ${scene.scene_key} 的已落库结果已核对并采用。`
        : `场 ${scene.scene_key} 仍为 ${result.external_outcome ?? 'UNKNOWN'}；原任务保留，不会创建新请求。`);
    await loadScenes();
    await load();
  });

  const resendUnknownScene = (scene: SceneStatus) => {
    if (!window.confirm(`「${scene.scene_key}」的旧请求可能仍有答案，可能多花一次模型调用。确认以一次新的请求重问？`)) return;
    void run(async () => {
      const result = await jsonPost<{ note: string }>(
        `/projects/${projectId}/material-preparation/scenes/${scene.task_id}/resend-unknown`,
        { expected_state_version: scene.state_version,
          evidence: '用户确认对结果未知的素材分场发起一次新请求' },
      );
      setOperationReport(result.note);
      await loadScenes();
    });
  };

  const confirmAndGenerate = () => run(async () => {
    if (!state?.plan_revision_id || !state.expected_snapshot) {
      throw new Error('先分析剧本，生成需求清单。');
    }
    const decisions = requirements.map((requirement) => requirementDecision(requirement));
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
    const generatable = decisions
      .filter((decision) => decision.strategy === 'generate')
      .map((decision) => decision.requirement_id);
    if (!generatable.length) {
      setStepReport((current) => ({
        ...current,
        generate: '这一版没有需要生成的素材，保留现有主素材即可继续。',
      }));
      await load();
      return;
    }
    await jsonPost(`/projects/${projectId}/material-preparation/generate`, {
      confirmation_id: confirmed.confirmation_id,
      confirmation_hash: confirmed.confirmation_hash,
      requirement_ids: generatable,
      idempotency_key: `material-generate-${confirmed.confirmation_id.slice(0, 12)}`,
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
        decisions: requirements.map((r) => requirementDecision(r, promoted[r.requirement_id])),
        visual_direction: direction,
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

  /** Let the operator replace one requirement with their own prepared picture. */
  const uploadOwnMaterial = (requirement: { requirement_id: string; entity_id: string; family: string }) =>
    async (event: { target: HTMLInputElement }) => {
      const file = event.target.files?.[0];
      event.target.value = '';
      if (!file) return;
      setBusy(true);
      setProblem(null);
      try {
        const current = state?.requirements.find((row) => row.requirement_id === requirement.requirement_id);
        if (!current) throw new Error('素材清单已变化，请刷新后重新上传。');
        const form = new FormData();
        form.append('file', file);
        form.append('expected_requirement_hash', current.requirement_hash);
        const asset = await api<Asset>(
          `/projects/${projectId}/material-preparation/requirements/${requirement.requirement_id}/upload`,
          { method: 'POST', body: form },
        );
        setBindings((current) => ({ ...current, [requirement.requirement_id]: asset.id }));
        setStrategy((current) => ({ ...current, [requirement.requirement_id]: 'reuse_master' }));
        setOperationReport(`已上传、确认并锁定「${asset.filename}」，已绑定到「${requirement.entity_id}」。`);
        await load();
        onPromoted?.();
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        setProblem(`${detail}。支持 PNG、JPG/JPEG、WebP。HEIC 等格式请先导出为 JPG 或 PNG。`);
      } finally {
        setBusy(false);
      }
    };

  /** One new generation revision for every requirement, keeping old candidates. */
  const regenerateAll = () => run(async () => {
    const row = await jsonPost<{ items?: string[]; run_id?: string }>(
      `/projects/${projectId}/material-preparation/regenerate-all`,
      { idempotency_key: `material-regen-all-${Date.now()}` },
    );
    setOperationReport(`已为 ${row.items?.length ?? 0} 项需求排队重新出图；旧候选保留，可对比后再选用。`);
    await load();
  });

  const cancelRun = (runId: string) => run(async () => {
    const target = runs.find((row) => row.run_id === runId);
    await jsonPost(`/projects/${projectId}/material-preparation/runs/${runId}/cancel`, {
      expected_state_version: target?.state_version ?? 0,
      idempotency_key: `material-cancel-${runId}`,
    });
    await load();
  });

  const itemAction = (batch: MaterialRun, item: MaterialItem, action: 'resume' | 'reconcile' | 'regenerate') => run(async () => {
    if (action === 'regenerate') {
      if (!state?.confirmation || !item.requirement_id || !item.brief) throw new Error('尚无可修改的外观描述，请先恢复出品人步骤。');
      const appearance = briefEdits[item.item_id] ?? item.brief.appearance;
      const requestBytes = new TextEncoder().encode(`${item.item_id}:${item.generation_revision}:${appearance}`);
      const requestHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', requestBytes)))
        .map((byte) => byte.toString(16).padStart(2, '0')).join('');
      await jsonPost(`/projects/${projectId}/material-preparation/regenerate`, {
        requirement_id: item.requirement_id, ...state.confirmation,
        idempotency_key: `regenerate-${requestHash}`,
        // The edited description IS the answer to the producer's questions;
        // re-sending them would only re-trigger the clarification refusal.
        brief: { ...item.brief, appearance, questions: [] }, seed: item.generation_revision + 1,
      });
      setOperationReport('已创建新的生成版本；你对澄清问题的回答已并入描述，历史图片保留，需要重新选用。');
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
  const sceneDone = (sceneStatus ?? []).filter((scene) => scene.status === 'SUCCEEDED').length;
  const sceneReview = (sceneStatus ?? []).filter((scene) => scene.status === 'NEEDS_REVIEW').length;

  // A batch runs for minutes. The page re-queries the durable ledger instead
  // of guessing, and stops polling once nothing is in flight.
  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [running, load]);
  // The server names four stages.  A finished plan still belongs to "选择素材";
  // every other value means there is nothing to select yet.
  const stage = state?.stage ?? 'read_script';
  const stageIndex = stage === 'materials_ready' || stage === 'select_materials' ? 2
    : stage === 'confirm_requirements' ? 1 : 0;
  // The person may open any stage and keep it open; the server stage only
  // decides where the page starts.
  const shownStage = openStage === null ? stageIndex : openStage;
  const readScriptRef = useRef<HTMLDetailsElement>(null);
  const confirmRef = useRef<HTMLDetailsElement>(null);
  const selectRef = useRef<HTMLDetailsElement>(null);
  // The server stage decides where the page starts, once. Binding the folds to
  // a controlled `open` plus an onToggle handler made them fight each other, so
  // "① 读剧本" could not be opened by hand and the "分析剧本" button inside it was
  // unreachable — the material list could never be produced. The folds are now
  // native: only a stage change moves them, and a person may open any of them.
  useEffect(() => {
    if (readScriptRef.current) readScriptRef.current.open = shownStage === 0;
    if (confirmRef.current) confirmRef.current.open = shownStage === 1;
    if (selectRef.current) selectRef.current.open = shownStage === 2;
  }, [shownStage]);
  const promotable = Object.keys(selected).length > 0;
  const railPresent = useRailPresent();
  // ④ 拍摄素材准备的顺序主操作：分析剧本 → 确认清单并出图 → 晋升所选素材。
  let materialPrimary: RailAction | null = null;
  if (stage === 'confirm_requirements') {
    materialPrimary = { label: confirmLabel, onClick: confirmAndGenerate, loading: busy,
      disabled: busy || requirements.length === 0, disabledReason: requirements.length === 0 ? '先分析剧本生成需求清单' : undefined,
      icon: <Sparkles size={14} /> };
  } else if (stage === 'select_materials' || stage === 'materials_ready') {
    materialPrimary = promotable ? { label: '确认使用所选素材为正式素材', onClick: promoteSelected, loading: busy,
      disabled: busy || !promotable, icon: <CircleCheck size={14} /> } : null;
  } else {
    materialPrimary = { label: '分析剧本生成素材清单', onClick: analyseScript, loading: busy || analysing,
      disabled: busy || !documentId || analysing || scenesRunning,
      disabledReason: !documentId ? '先在上方选择剧本文件'
        : (analysing || scenesRunning) ? '逐场分析进行中，等当前请求落定后再继续' : undefined,
      icon: <Sparkles size={14} /> };
  }
  useStageRail('material-prep', { priority: 1, primary: materialPrimary },
    [stage, busy, documentId, requirements.length, promotable, confirmLabel, analysing, scenesRunning]);
  // One dense grid of every candidate, whatever batch produced it: a person
  // looks at pictures, not at batch folders.  An item without a picture yet
  // (it failed, or the producer asked a question) still gets a tile, so the
  // work that needs the person never disappears from the grid.
  // One slot per requirement. A requirement that was regenerated three times used
  // to occupy four frames in the same grid - the three failed attempts plus the
  // newest - which reads as four different materials. Only the newest revision
  // keeps the slot; the older attempts stay in the batch history.
  const newestRunForRequirement = new Map<string, string>();
  const productionRuns = runs.filter((row) => row.kind === 'production');
  for (const row of productionRuns) {
    for (const item of row.items) {
      const requirementId = item.requirement_id;
      if (requirementId && !newestRunForRequirement.has(requirementId)) {
        newestRunForRequirement.set(requirementId, row.run_id);
      }
    }
  }
  // Only production batches fill the picker. A capability validation renders the
  // same three synthetic samples again after every renderer restart; those are
  // proof that the renderer works, never candidates for a requirement, and
  // showing them here made the project look like it had "materials" while the
  // real ones were still unrendered.
  const currentItems = (row: typeof runs[number]) => row.items.filter((item) =>
    !item.requirement_id || newestRunForRequirement.get(item.requirement_id) === row.run_id);

  const tiles = productionRuns.flatMap((row) => currentItems(row).flatMap((item) => {
    const base = {
      row,
      item,
      title: [
        FAMILY_LABELS[item.family ?? ''] ?? item.family,
        item.entity_id,
        item.family === 'prop' && item.carrier ? `载体：${propCarrierLabels[item.carrier] ?? item.carrier}` : '',
      ].filter(Boolean).join(' · ') || '素材需求',
      error: item.error_code ? reasonText(item.error_code) : '',
      questions: item.brief?.questions ?? [],
      brief: item.allowed_actions?.includes('regenerate') ? item.brief ?? null : null,
      selectable: !!item.requirement_id && requirements.some((r) => r.requirement_id === item.requirement_id),
      actions: (item.allowed_actions ?? [])
        .filter((a) => ['resume', 'reconcile', 'regenerate'].includes(a) && (a !== 'regenerate' || item.brief))
        .map((a) => ({
          action: a as 'resume' | 'reconcile' | 'regenerate',
          label: a === 'resume' ? '恢复此项' : a === 'reconcile' ? '核对此项未知结果' : '再生成一版（保留旧图）',
        })),
    };
    const list = item.candidates.length ? item.candidates : [null];
    return list.map((candidate, index) => ({
      ...base,
      candidate,
      first: index === 0,
      meta: candidate
        ? `生成 v${item.generation_revision} · ${candidate.formal_asset_id ? '已晋升'
          : candidate.status === 'READY_FOR_REVIEW' ? '待人工选用'
          : (ITEM_STATUS_LABELS[candidate.status] ?? candidate.status)}`
        : `第 ${item.generation_revision} 版 · 尚未出图`,
    }));
  })).filter((tile) => candidateGroup === 'all' || tile.item.family === candidateGroup);
  const totals = productionRuns.reduce((acc, row) => ({
    total: acc.total + (row.counts?.total ?? 0),
    ready: acc.ready + (row.counts?.candidate_ready ?? 0),
    failed: acc.failed + (row.counts?.failed ?? 0),
    review: acc.review + (row.counts?.needs_review ?? 0),
  }), { total: 0, ready: 0, failed: 0, review: 0 });
  return (
    <section className="material-panel" aria-label="AI 素材准备">
      <div className="notice">
        <Sparkles size={13} />
        <div>
          <p><b>先读剧本、备素材，再冻结风格。</b>这一步只调用文字模型，不占用显卡；真正出图在「选择素材」阶段排队。</p>
          <p>AI 只生成候选。晋升为主素材需要你在下面明确确认，AI 不会替你决定。</p>
        </div>
      </div>

      {operationReport && <p role="status">{operationReport}</p>}
      {documentId && <ScriptTextConsent key={`consent:${projectId}:${documentId}`} projectId={projectId} documentId={documentId} />}
      {problem && (
        <div className="blocker-item is-action" role="alert">
          <AlertTriangle size={14} className="required-icon" />
          <div className="blocker-reason"><b>这一步没有完成</b><small>{problem}</small></div>
        </div>
      )}
      {styleRows.length > 0 && (
        <details className="material-stage">
          <summary>影视风格已冻结（版本 {frozenStyle?.version}）：逐项对照素材视觉方向</summary>
          <div className="material-stage-body">
            {styleRows.map((row) => (
              <p key={row.label}>
                {row.label}：素材方向「{row.direction || '未填'}」／已冻结风格「{row.style || '未填'}」
              </p>
            ))}
            <p className="material-hint">两者不一致时，请在下方「影视风格」新建一个版本；已冻结的合同不会被自动修改。</p>
          </div>
        </details>
      )}
      {!!(state?.blockers ?? []).length && <details className="material-stage">
        <summary>还有 {state!.blockers.length} 项素材未绑定（点击展开清单）</summary>
        <div className="material-stage-body material-blocker-list">
          {(state?.blockers ?? []).map((blocker, index) => (
            <div className="blocker-item is-action" key={`${blocker.code}-${index}`}>
              <AlertTriangle size={14} className="required-icon" />
              <div className="blocker-reason">
                <b>{reasonText(blocker.code)}</b>
                <small>{blocker.note}</small>
                <small className="material-code">代码 {blocker.code}</small>
              </div>
            </div>
          ))}
        </div>
      </details>}

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

      <details className="material-stage" ref={readScriptRef}>
        <summary>① 读剧本{state?.plan_status ? ` · ${PLAN_STATUS_LABELS[state.plan_status] ?? ''}` : ''}</summary>
        <div className="material-stage-body">
          {documents.length === 0 && (
            <p className="material-hint">这个作品还没有剧本文件。请在本页上方「创意与剧本」里输入想法生成剧本，或导入现成剧本。</p>
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
          {!railPresent && <button type="button" className="btn-primary"
            disabled={busy || !documentId || analysing || scenesRunning} onClick={analyseScript}>
            {busy || analysing ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />} 分析剧本
          </button>}
          {(analysing || scenesRunning) && <p className="material-hint" role="status">
            逐场分析进行中：每一场是一次独立的模型调用，进度实时来自服务端台账，刷新页面不会中断。
          </p>}
          {sceneStatus && sceneStatus.length > 0 && (
            <div className="notice" role="status" aria-label="逐场分析进度">
              {scenesRunning || analysing ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
              <div>
                <p><b>逐场分析 {sceneDone}/{sceneStatus.length} 场完成</b>
                  {sceneReview > 0 ? ` · ${sceneReview} 场待核对` : ''}</p>
                <ul className="material-scene-list">
                  {sceneStatus.map((scene) => (
                    <li key={scene.scene_key}>
                      <span>{scene.scene_key}{scene.title ? ` · ${scene.title}` : ''}</span>
                      <span>{sceneStatusLabel(scene)}</span>
                      {scene.error_code && <em>{reasonText(scene.error_code)}</em>}
                      {scene.task_id && scene.state_version !== null
                        && (scene.status === 'NEEDS_REVIEW'
                          || ['UNKNOWN', 'NOT_SENT'].includes(scene.external_outcome ?? ''))
                        && <button type="button" className="btn-text" disabled={busy}
                          onClick={() => reconcileUnknownScene(scene)}>核对已有结果</button>}
                      {scene.task_id && scene.state_version !== null
                        && scene.status === 'NEEDS_REVIEW' && scene.external_outcome === 'UNKNOWN'
                        && <button type="button" className="btn-text" disabled={busy}
                          onClick={() => resendUnknownScene(scene)}>明确重发这一场</button>}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      </details>

      {documentId && <StoryPreparationPanel key={`${projectId}:${documentId}`} projectId={projectId}
        documentId={documentId} documentHash={documents.find((d) => d.id === documentId)?.sha256 ?? ''}
        styleFrozen={!!frozenStyle} onMaterials={() => { void load(); }} />}

      <details className="material-stage" ref={confirmRef}>
        <summary>② 确认需求 · {requirements.length} 项</summary>
        <div className="material-stage-body">
          {requirements.length === 0 ? (
            <p className="material-hint">还没有需求清单，先在上一步分析剧本。</p>
          ) : (
            <ul className="material-requirement-list">
              {requirements.map((requirement) => (
                <li key={requirement.requirement_id}>
                  <b>{FAMILY_LABELS[requirement.family] ?? requirement.family} · {requirement.entity_id}</b>
                  {requirement.canonical_entity_id && <small>与「{requirement.canonical_entity_id}」共用主素材</small>}
                  {requirement.family === 'prop' && requirement.carrier
                    && <small className="material-carrier">载体：{propCarrierLabels[requirement.carrier] ?? requirement.carrier}{(requirement.carrier_variants ?? []).length > 0 ? `（另有：${requirement.carrier_variants!.map((c) => propCarrierLabels[c] ?? c).join('、')}）` : ''}</small>}
                  <small>{requirement.description}</small>
                  {requirement.core_prop_reason && <small>需要独立参考的原因 · {requirement.core_prop_reason}</small>}
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
                      {requirement.family === 'prop' && <option value="omit_prop">随场景生成（无需独立道具图）</option>}
                    </select>
                  </label>
                  {defaultStrategy(requirement) !== 'generate' && defaultStrategy(requirement) !== 'omit_prop' && <label className="material-field">
                    <span>本项目素材与版本 · {requirement.entity_id}</span>
                    <select value={bindings[requirement.requirement_id] ?? requirement.bound_asset_id ?? ''}
                      onChange={(event) => setBindings({ ...bindings, [requirement.requirement_id]: event.target.value })}>
                      <option value="">请选择具体素材；未确认上传不会自动生成</option>
                      {assets.filter((a) => a.role === requirement.family
                        && a.entity_id === (requirement.canonical_entity_id ?? requirement.entity_id)).map((a) =>
                        <option key={a.id} value={a.id}>{a.filename} · {a.sha256.slice(0, 8)} · {a.locked ? '已确认' : '待确认上传'}</option>)}
                    </select>
                  </label>}
                  {!requirement.canonical_entity_id && <label className="material-field">
                    <span>上传并设为主素材（PNG、JPG/JPEG、WebP）</span>
                    <input type="file" accept=".png,.jpg,.jpeg,.webp" disabled={busy}
                      onChange={(event) => void uploadOwnMaterial(requirement)(event)} />
                  </label>}
                  <button type="button" className="btn-text" disabled={busy || librarySearchBusy}
                    onClick={() => void searchGlobalMaterial(requirement)}>
                    {librarySearchBusy && librarySearchRequirement === requirement.requirement_id ? '正在搜索…' : '从素材中心查找已审核版本'}
                  </button>
                  <button type="button" className="btn-text" disabled={busy || librarySearchBusy}
                    title="素材库是跨项目共用的；默认只搜本项目，这里显式放宽到全部项目"
                    onClick={() => void searchGlobalMaterial(requirement, 'library')}>
                    搜索全部素材库（跨项目）
                  </button>
                  {assets.filter((asset) => asset.id === (bindings[requirement.requirement_id] ?? requirement.bound_asset_id)
                    && asset.approved && asset.locked && asset.role === requirement.family && asset.entity_id === requirement.entity_id)
                    .map((asset) => <button key={asset.id} type="button" className="btn-text" disabled={adoptionBusy}
                      onClick={() => openProjectAdoption(requirement, asset)}>绑定已选主素材到镜头</button>)}
                  {adoptionReceipt?.requirementId === requirement.requirement_id && <div className="material-stage-body material-adoption">
                    <b>选择镜头并核对版本</b>
                    <small>采用会只推进你确认的镜头 revision；历史导演包保留原快照，当前导演包需要重新生成。</small>
                    {shots.length > 0 && <label className="material-field">
                      <span>镜头范围</span>
                      <select value={adoptionShotId} disabled={adoptionBusy} onChange={(event) => {
                        setAdoptionShotId(event.target.value);
                        setAdoptionPreview(null);
                      }}>
                        <option value="">自动查找匹配镜头</option>
                        {shots.map((shot) => <option key={shot.id} value={shot.id}>{shot.name} · r{shot.revision}</option>)}
                      </select>
                    </label>}
                    <div className="material-button-row">
                      <button type="button" className="btn-text" disabled={adoptionBusy}
                        onClick={() => void previewAdoption()}>用于这些镜头</button>
                      {adoptionPreview?.shots.some((row) => !row.conflict) && <button type="button" className="btn-secondary" disabled={adoptionBusy}
                        onClick={() => void applyAdoption()}>确认采用到 {adoptionPreview.shots.filter((row) => !row.conflict).length} 个镜头</button>}
                    </div>
                    {adoptionPreview?.shot_count === 0 && <p>未找到匹配镜头，请从列表选择具体镜头。</p>}
                    {adoptionPreview && <ul className="material-adoption-shots">
                      {adoptionPreview.shots.map((row) => <li key={row.shot_id}>
                        <b>{row.shot_name}</b>
                        <small>镜头 r{row.revision} · {row.already_bound ? '已绑定这个素材'
                          : row.conflict === 'SHOT_PRODUCTION_IN_FLIGHT'
                            ? `正在拍摄或结果未知（${row.production_status ?? '进行中'}）：先按原尝试收束`
                            : row.conflict === 'ASSET_RIGHTS_NOT_APPROVED'
                              ? row.rights?.message
                              : row.conflict === 'SHOT_IN_CONFIRMED_PACKAGE'
                              ? '属于当前已确认导演包：先生成并确认新导演包'
                              : '将采用并生成新镜头版本'}</small>
                      </li>)}
                    </ul>}
                    {adoptionReport && <p role="status" className="material-hint">{adoptionReport}</p>}
                  </div>}
                  {librarySearchRequirement === requirement.requirement_id && <div className="material-stage-body">
                    {librarySearchError && <p role="alert" className="blocker-reason">{librarySearchError}</p>}
                    {librarySearchBusy && <p className="material-hint">正在查找全局正式版本…</p>}
                    {!librarySearchBusy && !librarySearchError && librarySearchResults.length === 0 && <p className="material-hint">没有找到可分配的已晋升版本。你可以先去素材中心导入、审核并晋升。</p>}
                    {librarySearchResults.map((result) => <div className="material-library-result" key={result.version.id}>
                      <div><b>{result.asset.title} · V{result.version.ordinal}</b><small>{result.version.content_sha256.slice(0, 12)}</small></div>
                      <button type="button" className="btn-secondary" disabled={librarySearchBusy}
                        onClick={() => void assignGlobalMaterial(requirement, result)}>分配到本项目</button>
                    </div>)}
                  </div>}
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
          {!railPresent && <button type="button" className="btn-primary" disabled={busy || requirements.length === 0} onClick={confirmAndGenerate}>
            {confirmLabel}
          </button>}
        </div>
      </details>

      <details className="material-stage" ref={selectRef}>
        <summary>③ 选择素材 · {tiles.length} 项候选</summary>
        <div className="material-stage-body">
          {state?.stage === 'no_plan' && <p className="material-hint" role="status">
            本项目还没有逐场素材清单：上方「项目素材」里是直接上传并确认的主素材，所以这里没有生成候选。
            需要 AI 候选时，先在 ① 读剧本点「分析剧本」，再在 ② 确认需求；确认后 ③ 才会排队出图。
          </p>}
          {state && runs.length === 0 && state.stage !== 'no_plan' && <p className="material-hint">还没有出图批次。确认清单后，生成的项目会在这里排队。</p>}
          {tiles.length > 0 && <>
            <div className="material-toolbar">
              <label className="material-field"><span>候选素材分组</span><select value={candidateGroup} onChange={(e) => setCandidateGroup(e.target.value)}>
                <option value="all">全部候选</option><option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option>
              </select></label>
              <small>已选用 {Object.keys(selected).length} 项 · 候选 {totals.ready}/{totals.total} · 失败 {totals.failed} · 待核对 {totals.review}</small>
            </div>
            <div className="batch-bar">
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => {
                const next = { ...selected };
                for (const tile of tiles) {
                  if (tile.candidate && tile.selectable && !tile.candidate.formal_asset_id
                    && tile.candidate.status === 'READY_FOR_REVIEW' && tile.item.requirement_id) {
                    next[tile.item.requirement_id] = tile.candidate.candidate_id;
                  }
                }
                setSelected(next);
              }}>全选可用候选</button>
              <button type="button" className="btn-text" disabled={busy || Object.keys(selected).length === 0}
                onClick={() => setSelected({})}>清空选择</button>
            </div>
            <div className="media-grid material-grid" aria-label="候选素材预览">
              {tiles.slice(0, visibleCandidates).map((tile) => (
                <article className="media-card material-tile" key={tile.candidate?.candidate_id ?? tile.item.item_id}>
                  {tile.candidate
                    ? <ImagePreview thumbnail={`${API}/candidate-assets/${tile.candidate.candidate_id}/thumbnail`}
                      original={`${API}/candidate-assets/${tile.candidate.candidate_id}/media`}
                      label={`${tile.item.entity_id ?? ''} · 生成 v${tile.item.generation_revision}`} />
                    : <div className="material-tile-empty">尚未出图</div>}
                  <b>{tile.title}</b>
                  <small>{tile.meta}</small>
                  {tile.candidate?.is_mock && <em>离线测试候选，不能用于真实生产</em>}
                  {tile.first && tile.error && <em>{tile.error}</em>}
                  {tile.first && tile.questions.length > 0 && <div>
                    <b>出品人需要澄清</b>
                    <ul>{tile.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
                  </div>}
                  {tile.candidate && tile.selectable && !tile.candidate.formal_asset_id && <label>
                    <input type="checkbox" disabled={tile.candidate.status !== 'READY_FOR_REVIEW'}
                      checked={selected[tile.item.requirement_id!] === tile.candidate.candidate_id}
                      aria-label={`选择 ${tile.item.entity_id} 生成v${tile.item.generation_revision} ${tile.candidate.candidate_id}`}
                      onChange={(event) => setSelected((current) => {
                        const next = { ...current };
                        if (event.target.checked) next[tile.item.requirement_id!] = tile.candidate!.candidate_id;
                        else delete next[tile.item.requirement_id!];
                        return next;
                      })} />选用此版本
                  </label>}
                  {tile.first && tile.actions.length > 0 && <div className="material-tile-actions">
                    {tile.actions.map((action) => <button type="button" key={action.action} disabled={busy}
                      onClick={() => itemAction(tile.row, tile.item, action.action)}>{action.label}</button>)}
                  </div>}
                  {tile.item.requirement_id && <label className="material-tile-upload">
                    <input type="file" accept=".png,.jpg,.jpeg,.webp" disabled={busy}
                      onChange={(event) => void uploadOwnMaterial({
                        requirement_id: tile.item.requirement_id!,
                        entity_id: String(tile.item.entity_id ?? ''),
                        family: String(tile.item.family ?? ''),
                      })(event)} />
                    上传我的图片替换
                  </label>}
                  {tile.first && tile.brief && <details className="material-tile-brief"><summary>修改外观描述</summary>
                    <label className="material-field">
                      <span>修改 {tile.item.entity_id} 的外观描述后生成新版本（外观描述用英文填写）</span>
                      <textarea value={briefEdits[tile.item.item_id] ?? tile.brief.appearance} maxLength={1600}
                        onChange={(e) => setBriefEdits({ ...briefEdits, [tile.item.item_id]: e.target.value })} />
                    </label>
                  </details>}
                </article>
              ))}
            </div>
            {tiles.length > visibleCandidates && <button type="button" onClick={() => setVisibleCandidates((n) => n + 24)}>加载更多候选</button>}
            {runs.filter((row) => row.allowed_actions.includes('cancel')).map((row) => (
              <button type="button" className="btn-secondary" key={row.run_id} disabled={busy}
                onClick={() => cancelRun(row.run_id)}>停止这个批次</button>
            ))}
            <details className="material-tech"><summary>批次与技术信息</summary>
              {runs.map((row) => <p key={row.run_id}>
                {row.kind === 'validation' ? '出图能力验证' : '素材批次'} · {RUN_STATUS_LABELS[row.status] ?? row.status} ·
                候选 {row.counts.candidate_ready}/{row.counts.total} · 失败 {row.counts.failed} · 待核对 {row.counts.needs_review} · {row.run_id}
              </p>)}
            </details>
          </>}
          {!railPresent && <button type="button" className="btn-primary" disabled={busy || !promotable} onClick={promoteSelected}>
            确认使用所选素材
          </button>}
          <button type="button" className="btn-text" disabled={busy || !runs.length} onClick={regenerateAll}>
            重跑全部素材（保留旧候选）
          </button>
          {evaluation && (
            <div className={evaluation.materials_ready ? 'material-ready' : 'blocker-item is-action'} role="status">
              {evaluation.materials_ready ? (
                <><CircleCheck size={14} className="required-icon" />
                  <div className="blocker-reason"><b>素材齐备</b><small>每一项必需素材都已绑定已批准并锁定的主素材，可以继续准备导演包。</small></div>
                </>
              ) : (
                <>
                  <AlertTriangle size={14} className="required-icon" />
                  <div className="blocker-reason">
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
