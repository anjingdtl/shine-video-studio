/** 素材生成进度 + 页内候选选定：素材准备页与素材中心共用的一条进度事实。
 *
 *  只读 `/material-preparation/generation-progress`——它与素材页读的是同一份
 *  `public_run` 台账投影（同一个执行者：常驻 Worker），不是第二套判定。
 *  轮询只在还有批次在途时进行；落定后停止，不空转。
 *
 *  传入 `onPromoted` 时（导演④素材准备页），候选直接在本页选定并晋升：复用
 *  `/candidate-assets/{id}/promote` 这同一个晋升执行者（晋升即回绑需求与镜头），
 *  不再把用户引去素材中心。素材中心是平行的素材管理模块，不串进生产环节。 */
import { useCallback, useEffect, useState } from 'react';
import { Check, RefreshCw } from 'lucide-react';
import { API, ApiError, api, promoteMaterialCandidate, type AdoptionReport, type RequirementShotBinding } from '../api';

type CandidateInfo = {
  candidate_id: string;
  status: string;
  content_sha256: string | null;
  is_mock: boolean;
  formal_asset_id: string | null;
  attempt_no: number;
  adoption?: AdoptionReport | null;
};

type CharacterSheetProgressInfo = {
  current_step?: string | null;
  current_step_label?: string | null;
  completed_steps?: Array<Record<string, unknown>>;
  total_steps?: number;
};

type ProgressItem = {
  item_id: string;
  requirement_id?: string | null;
  entity_id: string | null;
  family: string | null;
  status: string;
  error_code: string | null;
  character_sheet_mode?: boolean;
  character_sheet_progress?: CharacterSheetProgressInfo | null;
  candidates?: CandidateInfo[];
};

export type GenerationRun = {
  run_id: string;
  status: string;
  kind: string;
  state_version?: number;
  counts: { total: number; candidate_ready: number; failed: number; needs_review: number; queued: number; running: number };
  items: ProgressItem[];
};

export type AdoptedMaster = {
  requirement_id: string;
  entity_id: string | null;
  family: string | null;
  asset_id: string;
  shotBinding?: RequirementShotBinding | null;
};

const ITEM_LABEL: Record<string, string> = {
  QUEUED: '排队中', BRIEF_RUNNING: '写外观描述', BRIEF_READY: '外观就绪',
  WAITING_GPU: '等待显卡', SUBMIT_INTENT: '已提交意图', RUNNING: '渲染中',
  OUTPUT_STORED: '已存图', QC_PENDING: '技术质检中', CANDIDATE_READY: '候选可用',
  FAILED: '失败', NEEDS_REVIEW: '待核对', CANCELLED: '已取消',
};

const FAMILY_LABEL: Record<string, string> = { character: '人物', scene: '场景', prop: '道具' };

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

/** 一个候选是否可由用户选用晋升：已质检待审、且尚未晋升过。 */
function selectable(candidate: CandidateInfo): boolean {
  return candidate.status === 'READY_FOR_REVIEW' && !candidate.formal_asset_id;
}

export function MaterialGenerationProgress({ projectId, onOpenLibrary, onPromoted, onUnadopt, refreshKey = 0, masters = [], onInFlightChange }: {
  projectId: string;
  onOpenLibrary?: () => void;
  /** 提供时在本页展示候选并晋升；晋升后回调父组件刷新需求清单。 */
  onPromoted?: () => void;
  /** 提供时已采用素材可取消：走同一决策执行者改回生成并解绑镜头。 */
  onUnadopt?: (requirementId: string) => Promise<void> | void;
  /** 提交决策后由父组件 bump，立即刷新而不必等下一个轮询周期。 */
  refreshKey?: number;
  /** 已上传/已采用的主素材，与生成候选同列展示，避免两套选图面。 */
  masters?: AdoptedMaster[];
  /** 在途出图变化时通知父组件（同一份 generation-progress 事实），据此锁定主操作。 */
  onInFlightChange?: (inFlight: boolean) => void;
}) {
  const [runs, setRuns] = useState<GenerationRun[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [promoting, setPromoting] = useState('');
  const [promoError, setPromoError] = useState('');
  const [promoNote, setPromoNote] = useState('');
  /** 批量勾选晋升：候选 id → 是否勾选。晋升仍逐个走唯一执行者 promoteMaterialCandidate。 */
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      const body = await api<{ runs: GenerationRun[] }>(
        `/projects/${projectId}/material-preparation/generation-progress`);
      setRuns(body.runs ?? []);
    } catch {
      /* 读不到就保持现状：进度条不因此谎称“没有任务”。 */
    } finally {
      setLoaded(true);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const active = runs.filter((row) => row.counts.running > 0 || row.counts.queued > 0);
  useEffect(() => {
    if (active.length === 0) return undefined;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [active.length, load]);
  const inFlight = active.length > 0;
  useEffect(() => { onInFlightChange?.(inFlight); }, [inFlight, onInFlightChange]);

  async function promote(candidate: CandidateInfo) {
    if (promoting) return;
    setPromoting(candidate.candidate_id); setPromoError(''); setPromoNote('');
    try {
      const response = await promoteMaterialCandidate(candidate.candidate_id, candidate.is_mock);
      const adoption = response?.payload?.adoption;
      if (adoption && adoption.library_only) {
        const protectedList = adoption.protected_shots.map((s) => s.shot_name).join('、');
        setPromoNote(
          `新图已进入作品素材库（回绑 0 镜）：${protectedList || '当前镜头'} 因处于已确认导演包或已通过状态仍在使用旧图；如需替换请在下方点「按修订换图」。`,
        );
      } else if (adoption && adoption.protected_shots.length > 0) {
        const protectedList = adoption.protected_shots.map((s) => s.shot_name).join('、');
        setPromoNote(
          `已晋升为主素材并回绑 ${adoption.rebound_count} 镜；以下受保护镜头仍在使用旧图：${protectedList}。`,
        );
      } else {
        setPromoNote('已晋升为主素材，并回绑到需要它的镜头。');
      }
      onPromoted?.();
      await load();
    } catch (error) {
      setPromoError(describeError(error));
    } finally {
      setPromoting('');
    }
  }

  /** 批量晋升：对勾选候选逐个复用同一晋升执行者（串行，避免并发授权/回绑竞争），
   *  单项失败不中断其余；结束后统一刷新需求清单。不另起批量晋升链路。 */
  async function promoteBatch() {
    const targets = pendingTiles.filter(({ candidate }) => selected[candidate.candidate_id]);
    if (targets.length === 0 || promoting) return;
    setPromoting('batch'); setPromoError(''); setPromoNote('');
    let ok = 0; let firstError = '';
    for (const { candidate } of targets) {
      try {
        await promoteMaterialCandidate(candidate.candidate_id, candidate.is_mock);
        ok += 1;
      } catch (error) {
        if (!firstError) firstError = describeError(error);
      }
    }
    setSelected({});
    setPromoting('');
    if (ok > 0) {
      setPromoNote(`已批量晋升 ${ok} 项为主素材，并回绑到需要它们的镜头。`);
      onPromoted?.();
    }
    if (firstError) setPromoError(`部分候选晋升失败：${firstError}`);
    await load();
  }

  async function unadopt(requirementId: string) {
    if (!onUnadopt || promoting) return;
    setPromoting(`unadopt:${requirementId}`); setPromoError(''); setPromoNote('');
    try {
      await onUnadopt(requirementId);
      setPromoNote('已取消采用：旧主素材从镜头解绑，可重新选定或生成。');
      await load();
    } catch (error) {
      setPromoError(describeError(error));
    } finally {
      setPromoting('');
    }
  }

  // 只显示有内容的批次：在途的，或已产出候选的。空批次不占位。
  const shown = runs.filter((row) => row.counts.total > 0
    && (row.counts.running > 0 || row.counts.queued > 0 || row.counts.candidate_ready > 0
        || row.counts.needs_review > 0 || row.counts.failed > 0)).slice(0, 3);
  // 唯一选定面：待选候选 + 已采用主素材同列网格（每行四张）。下方批次只报进度，
  // 不再重复一套选图/晋升入口（三原则：一个选定面、一个晋升执行者）。
  const pendingTiles = onPromoted
    ? runs.flatMap((run) => run.items.flatMap((item) =>
        (item.candidates ?? []).filter(selectable).map((candidate) => ({ candidate, item }))))
    : [];
  const showSelection = Boolean(onPromoted) && (masters.length > 0 || pendingTiles.length > 0);
  if (!loaded || (shown.length === 0 && !showSelection)) return null;

  return <section className="material-generation-progress" aria-label="素材生成进度">
    <div className="mgp-title"><RefreshCw size={13} className={active.length ? 'spin' : ''} />
      <b>素材生成进度</b>
      <span className="muted">后台 Worker 正在按你确认的决策出图，刷新或切页不会中断。</span>
    </div>
    {promoNote && <p className="mgp-done"><Check size={12} /> {promoNote}</p>}
    {promoError && <p role="alert" className="form-error">{promoError}</p>}
    {showSelection && <div className="mgp-candidates">
      <p className="mgp-candidates-title">选定并采用（晋升为主素材后自动回绑镜头；已采用的可取消）：</p>
      {pendingTiles.length > 0 && <div className="mgp-batch-bar">
        <label className="mgp-batch-select">
          <input type="checkbox" disabled={!!promoting}
            checked={pendingTiles.every(({ candidate }) => selected[candidate.candidate_id])}
            aria-label="全选候选"
            onChange={(event) => setSelected(event.target.checked
              ? Object.fromEntries(pendingTiles.map(({ candidate }) => [candidate.candidate_id, true]))
              : {})} />
          全选
        </label>
        <span className="mgp-counts">已选 {pendingTiles.filter(({ candidate }) => selected[candidate.candidate_id]).length} 项</span>
        <button type="button" className="btn-secondary" disabled={!!promoting
          || !pendingTiles.some(({ candidate }) => selected[candidate.candidate_id])}
          onClick={() => void promoteBatch()}>
          {promoting === 'batch' ? '批量晋升中…' : '批量晋升所选'}
        </button>
      </div>}
      <div className="mgp-candidate-grid">
        {pendingTiles.map(({ candidate, item }) => <figure className="mgp-candidate" key={candidate.candidate_id}>
          <label className="mgp-candidate-check">
            <input type="checkbox" disabled={!!promoting} checked={!!selected[candidate.candidate_id]}
              aria-label={`勾选候选 ${item.entity_id ?? '素材'} ${candidate.attempt_no}`}
              onChange={(event) => setSelected((current) => ({ ...current, [candidate.candidate_id]: event.target.checked }))} />
          </label>
          <img src={`${API}/candidate-assets/${candidate.candidate_id}/thumbnail`}
            alt={`${item.entity_id ?? '素材'} 候选 ${candidate.attempt_no}`} loading="lazy" />
          <span className="mgp-candidate-entity">{FAMILY_LABEL[item.family ?? ''] ?? item.family} · {item.entity_id ?? '素材'}</span>
          <button type="button" className="btn-text" disabled={!!promoting}
            onClick={() => void promote(candidate)}>
            {promoting === candidate.candidate_id ? '晋升中…' : '选用并晋升'}</button>
        </figure>)}
        {masters.map((master) => {
          const sb = master.shotBinding;
          const isLibraryOnly = sb?.status === 'LIBRARY_ONLY';
          const isPartial = sb?.status === 'BOUND_PARTIAL';
          const captionText = isLibraryOnly
            ? `已入库（镜头仍用旧图 0/${sb?.target_shot_count ?? 0}）`
            : isPartial
              ? `部分镜头已绑定（${sb?.bound_to_current_count ?? 0}/${sb?.target_shot_count ?? 0}）`
              : sb && sb.target_shot_count > 0
                ? `已采用（${sb.bound_to_current_count}/${sb.target_shot_count} 镜）`
                : '已采用';
          return <figure className="mgp-candidate" key={`${master.requirement_id}-${master.asset_id}`}>
          <img src={`${API}/assets/${master.asset_id}/thumbnail`}
            alt={`${master.entity_id ?? '素材'} ${captionText}`} loading="lazy" />
          <span className="mgp-candidate-entity">{FAMILY_LABEL[master.family ?? ''] ?? master.family} · {master.entity_id ?? '素材'}</span>
          <figcaption className={isLibraryOnly || isPartial ? 'warn' : 'ok'}>
            {!isLibraryOnly && !isPartial && <Check size={11} />} {captionText}
          </figcaption>
          {onUnadopt && <button type="button" className="btn-text" disabled={!!promoting}
            onClick={() => void unadopt(master.requirement_id)}>
            {promoting === `unadopt:${master.requirement_id}` ? '取消中…' : '取消采用'}</button>}
        </figure>;
        })}
      </div>
    </div>}
    {shown.map((run) => {
      const settled = run.counts.candidate_ready + run.counts.failed + run.counts.needs_review;
      const pct = run.counts.total ? Math.round((settled / run.counts.total) * 100) : 0;
      const inflight = run.counts.running + run.counts.queued;
      const working = inflight > 0;
      const inflightItems = run.items.filter((item) => item.status === 'RUNNING' || item.status === 'BRIEF_RUNNING');
      return <div className="mgp-run" key={run.run_id}>
        <div className="mgp-head">
          <b>{working ? '后台正在生成素材…'
            : run.counts.candidate_ready > 0 ? '候选已生成，待你选定采用'
            : '这一批已结束'}</b>
          <span className="mgp-counts">
            候选 {run.counts.candidate_ready}/{run.counts.total}
            {run.counts.running > 0 && ` · 渲染中 ${run.counts.running}`}
            {run.counts.queued > 0 && ` · 排队 ${run.counts.queued}`}
            {run.counts.needs_review > 0 && ` · 待核对 ${run.counts.needs_review}`}
            {run.counts.failed > 0 && ` · 失败 ${run.counts.failed}`}
          </span>
        </div>
        <div className="mgp-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0}
          aria-valuemax={100} aria-label="素材生成进度">
          <i style={{ width: `${pct}%` }} className={working ? 'is-working' : ''} />
        </div>
        {inflightItems.length > 0 && <p className="mgp-now">
          正在处理：{inflightItems.slice(0, 4).map((item) => {
            const cs = item.character_sheet_progress;
            const csText = item.character_sheet_mode && cs?.current_step_label
              ? ` · 定妆照 ${Math.min((cs.completed_steps?.length ?? 0) + 1, cs.total_steps ?? 6)}/${cs.total_steps ?? 6} ${cs.current_step_label}`
              : '';
            return `${item.entity_id ?? '素材'}（${ITEM_LABEL[item.status] ?? item.status}${csText}）`;
          }).join('、')}
        </p>}

        {run.counts.candidate_ready > 0 && !working && onPromoted && <p className="mgp-done">
          <Check size={12} /> 候选已就绪，在上方「选定并采用」网格晋升。
        </p>}
        {run.counts.candidate_ready > 0 && !working && !onPromoted && <p className="mgp-done">
          <Check size={12} /> {run.counts.candidate_ready} 项候选已就绪。
          {onOpenLibrary && <button type="button" className="btn-text" onClick={onOpenLibrary}>
            去素材中心选定并采用</button>}
        </p>}
        {run.counts.needs_review > 0 && <p className="mgp-review">
          {run.counts.needs_review} 项结果待核对（不会自动重发）。
          {onOpenLibrary && !onPromoted && <button type="button" className="btn-text" onClick={onOpenLibrary}>
            去素材中心核对</button>}
        </p>}
        <details className="mgp-items"><summary>逐项状态</summary>
          <ul>{run.items.map((item) => <li key={item.item_id}>
            <span>{FAMILY_LABEL[item.family ?? ''] ?? item.family} · {item.entity_id ?? '素材'}</span>
            <em className={item.status === 'FAILED' || item.status === 'NEEDS_REVIEW' ? 'warn' : ''}>
              {ITEM_LABEL[item.status] ?? item.status}
              {item.error_code === 'CHARACTER_SHEET_LORA_REQUIRED'
                ? '（缺少「Qwen 多视角镜头」LoRA，请前往「设置 → 制作环境 → 角色五视图定妆照」勾选下载）'
                : item.error_code ? `（${item.error_code}）` : ''}</em>
          </li>)}</ul>
        </details>
      </div>;
    })}
  </section>;
}