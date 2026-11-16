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
import { API, ApiError, api, json, promoteMaterialCandidate } from '../api';

type CandidateInfo = {
  candidate_id: string;
  status: string;
  content_sha256: string | null;
  is_mock: boolean;
  formal_asset_id: string | null;
  attempt_no: number;
};

type ProgressItem = {
  item_id: string;
  requirement_id?: string | null;
  entity_id: string | null;
  family: string | null;
  status: string;
  error_code: string | null;
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

export function MaterialGenerationProgress({ projectId, onOpenLibrary, onPromoted, refreshKey = 0 }: {
  projectId: string;
  onOpenLibrary?: () => void;
  /** 提供时在本页展示候选并晋升；晋升后回调父组件刷新需求清单。 */
  onPromoted?: () => void;
  /** 提交决策后由父组件 bump，立即刷新而不必等下一个轮询周期。 */
  refreshKey?: number;
}) {
  const [runs, setRuns] = useState<GenerationRun[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [promoting, setPromoting] = useState('');
  const [promoError, setPromoError] = useState('');
  const [promoNote, setPromoNote] = useState('');

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

  async function promote(candidate: CandidateInfo) {
    if (promoting) return;
    setPromoting(candidate.candidate_id); setPromoError(''); setPromoNote('');
    try {
      await promoteMaterialCandidate(candidate.candidate_id, candidate.is_mock);
      setPromoNote('已晋升为主素材，并回绑到需要它的镜头。');
      onPromoted?.();
      await load();
    } catch (error) {
      setPromoError(describeError(error));
    } finally {
      setPromoting('');
    }
  }

  const [resuming, setResuming] = useState('');
  /** 待核对/失败项在本页重试：后端守卫保证 UNKNOWN image 提交不被重发。 */
  async function resumeReview(run: GenerationRun) {
    if (resuming) return;
    const ids = run.items
      .filter((item) => item.status === 'NEEDS_REVIEW' || item.status === 'FAILED')
      .map((item) => item.item_id);
    if (!ids.length) return;
    setResuming(run.run_id); setPromoError('');
    try {
      await api(`/projects/${projectId}/material-preparation/runs/${run.run_id}/resume`,
        json('POST', { expected_state_version: run.state_version ?? 0, item_ids: ids }));
      onPromoted?.();
      await load();
    } catch (error) {
      setPromoError(describeError(error));
    } finally {
      setResuming('');
    }
  }

  // 只显示有内容的批次：在途的，或已产出候选的。空批次不占位。
  const shown = runs.filter((row) => row.counts.total > 0
    && (row.counts.running > 0 || row.counts.queued > 0 || row.counts.candidate_ready > 0
        || row.counts.needs_review > 0 || row.counts.failed > 0)).slice(0, 3);
  if (!loaded || shown.length === 0) return null;

  return <section className="material-generation-progress" aria-label="素材生成进度">
    <div className="mgp-title"><RefreshCw size={13} className={active.length ? 'spin' : ''} />
      <b>素材生成进度</b>
      <span className="muted">后台 Worker 正在按你确认的决策出图，刷新或切页不会中断。</span>
    </div>
    {promoNote && <p className="mgp-done"><Check size={12} /> {promoNote}</p>}
    {promoError && <p role="alert" className="form-error">{promoError}</p>}
    {shown.map((run) => {
      const settled = run.counts.candidate_ready + run.counts.failed + run.counts.needs_review;
      const pct = run.counts.total ? Math.round((settled / run.counts.total) * 100) : 0;
      const inflight = run.counts.running + run.counts.queued;
      const working = inflight > 0;
      const inflightItems = run.items.filter((item) => item.status === 'RUNNING' || item.status === 'BRIEF_RUNNING');
      // 页内候选选定：只在本页负责晋升时展示，按需求条目归类。
      const candidateItems = onPromoted
        ? run.items.filter((item) => (item.candidates ?? []).some(selectable))
        : [];
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
          正在处理：{inflightItems.slice(0, 4).map((item) =>
            `${item.entity_id ?? '素材'}（${ITEM_LABEL[item.status] ?? item.status}）`).join('、')}
        </p>}

        {candidateItems.length > 0 && <div className="mgp-candidates">
          <p className="mgp-candidates-title">在本页选定候选并采用（晋升为主素材后自动回绑镜头）：</p>
          {candidateItems.map((item) => {
            const candidates = item.candidates ?? [];
            return <div className="mgp-candidate-item" key={item.item_id}>
              <span className="mgp-candidate-entity">
                {FAMILY_LABEL[item.family ?? ''] ?? item.family} · {item.entity_id ?? '素材'}
              </span>
              <div className="mgp-candidate-grid">
                {candidates.map((candidate) => {
                  const isBusy = promoting === candidate.candidate_id;
                  const adopted = !!candidate.formal_asset_id;
                  const canSelect = selectable(candidate);
                  return <figure className="mgp-candidate" key={candidate.candidate_id}>
                    <img src={`${API}/candidate-assets/${candidate.candidate_id}/thumbnail`}
                      alt={`${item.entity_id ?? '素材'} 候选 ${candidate.attempt_no}`} loading="lazy" />
                    {adopted
                      ? <figcaption className="ok"><Check size={11} /> 已采用</figcaption>
                      : canSelect
                        ? <button type="button" className="btn-text" disabled={!!promoting}
                            onClick={() => void promote(candidate)}>
                            {isBusy ? '晋升中…' : '选用并晋升'}</button>
                        : <figcaption className="muted">{candidate.status}</figcaption>}
                  </figure>;
                })}
              </div>
            </div>;
          })}
        </div>}

        {run.counts.candidate_ready > 0 && !working && !onPromoted && <p className="mgp-done">
          <Check size={12} /> {run.counts.candidate_ready} 项候选已就绪。
          {onOpenLibrary && <button type="button" className="btn-text" onClick={onOpenLibrary}>
            去素材中心选定并采用</button>}
        </p>}
        {run.counts.needs_review > 0 && <p className="mgp-review">
          {run.counts.needs_review} 项结果待核对（不会自动重发）。
          {onPromoted && <button type="button" className="btn-text" disabled={!!resuming}
            onClick={() => void resumeReview(run)}>
            {resuming === run.run_id ? '重试中…' : '重试待核对项'}</button>}
          {onOpenLibrary && !onPromoted && <button type="button" className="btn-text" onClick={onOpenLibrary}>
            去素材中心核对</button>}
        </p>}
        <details className="mgp-items"><summary>逐项状态</summary>
          <ul>{run.items.map((item) => <li key={item.item_id}>
            <span>{FAMILY_LABEL[item.family ?? ''] ?? item.family} · {item.entity_id ?? '素材'}</span>
            <em className={item.status === 'FAILED' || item.status === 'NEEDS_REVIEW' ? 'warn' : ''}>
              {ITEM_LABEL[item.status] ?? item.status}{item.error_code ? `（${item.error_code}）` : ''}</em>
          </li>)}</ul>
        </details>
      </div>;
    })}
  </section>;
}