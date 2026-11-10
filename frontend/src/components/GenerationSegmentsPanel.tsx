import { useCallback, useEffect, useRef, useState } from 'react';
import { API, api, json } from '../api';
import { OpenVideoFolderButton } from './OpenVideoFolderButton';

type Segment = {
  shot_id: string; position: number; story_beat: string; duration_s: number;
  shot_revision: number; job_id: string | null; attempt_no: number | null;
  series_id?: string | null; episode_position?: number | null; episode_title?: string | null;
  status: string; artifact_id: string | null; blocker: string | null; can_regenerate: boolean;
  editorial_cuts: { beat_id?: string; start_s?: number }[];
  dependent_job_ids: string[]; replacement_job_id: string | null;
  replacement_status: string | null; can_select_replacement: boolean;
};
type SegmentState = { series_id: string | null; run_id?: string | null; segments: Segment[] };
const PAGE_SIZE = 6;

/** The page speaks in outcomes, not in engine codes. */
const SEGMENT_STATUS: Record<string, string> = {
  PASSED: '已通过', RUNNING: '生成中', QUEUED: '排队中', SUBMITTING: '提交中',
  QC_PENDING: '质检中', REPAIR_PENDING: '返工中', FAILED: '失败',
  NEEDS_REVIEW: '待人工处理', BLOCKED: '已阻断', CANCELLED: '已取消', STALE: '已失效',
};

/**
 * A produced shot as a poster with the real clip behind it.
 *
 * The poster is a small derivative the server builds from the produced file;
 * the clip itself only loads when the person asks for it.  When no poster can
 * be built the player is shown inline instead, so a missing decoder never
 * leaves the person with an unexplained empty frame.
 */
export function VideoPreview({ poster, video, label }: { poster: string; video: string; label: string }) {
  const [open, setOpen] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!open || !node) return;
    // A runtime without the dialog API still has to show the player.
    if (typeof node.showModal === 'function') node.showModal();
    else node.setAttribute('open', '');
  }, [open]);
  return <>
    {posterFailed
      // A missing poster does not mean the clip is unplayable: show the real
      // player inline instead of a dead "preview unavailable" frame.
      ? <div className="media-preview-fallback" aria-label={`预览 ${label}`}>
          <video controls preload="metadata" src={video} aria-label={label} />
          <small>封面不可用，可直接播放</small>
        </div>
      : <button type="button" className="media-preview-button segment-poster" aria-label={`预览 ${label}`} onClick={() => setOpen(true)}>
          <img src={poster} alt={label} loading="lazy" decoding="async" onError={() => setPosterFailed(true)} />
          <span className="segment-poster-play" aria-hidden="true">▶</span>
        </button>}
    {open && <dialog ref={dialog} className="media-preview-dialog" aria-label={`预览 ${label}`} onClose={() => setOpen(false)}>
      <button type="button" onClick={() => { dialog.current?.close(); setOpen(false); }}>关闭预览</button>
      <video controls autoPlay src={video} style={{ width: '100%', maxHeight: '75vh' }} />
      <p>{label}</p>
    </dialog>}
  </>;
}

export function LazyVideo({ src, label }: { src: string; label: string }) {
  const [loaded, setLoaded] = useState(false);
  return loaded ? <video controls preload="metadata" src={src} aria-label={label} style={{ width: '100%', maxHeight: 480 }} />
    : <button type="button" onClick={() => setLoaded(true)}>预览 {label}</button>;
}

export default function GenerationSegmentsPanel({ projectId, fingerprint }: { projectId: string; fingerprint: string }) {
  const [state, setState] = useState<SegmentState>({ series_id: null, run_id: null, segments: [] });
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(0);
  const reload = useCallback(async () => {
    const result = await api<SegmentState>(`/projects/${projectId}/generation-segments`);
    setState(result);
  }, [projectId]);
  useEffect(() => { void reload().catch((e) => setMessage(String(e))); }, [reload, fingerprint]);
  useEffect(() => { setPage(0); }, [projectId, fingerprint, state.run_id]);
  const pageCount = Math.max(1, Math.ceil(state.segments.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visibleSegments = state.segments.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const firstVisible = state.segments.length ? currentPage * PAGE_SIZE + 1 : 0;
  const lastVisible = Math.min((currentPage + 1) * PAGE_SIZE, state.segments.length);
  const pending = state.segments.some((s) => s.replacement_status && ['QUEUED', 'RUNNING', 'QC_PENDING'].includes(s.replacement_status));
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => { void reload().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [reload, pending]);
  async function act(segment: Segment, select: boolean) {
    setBusy(true); setMessage('');
    try {
      if (select) {
        const sourceSeriesId = segment.series_id ?? state.series_id;
        if (!sourceSeriesId) throw new Error('找不到此镜头所属的制作批次，暂不能选用新版本。');
        await api(`/projects/${projectId}/generation-segments/${sourceSeriesId}/select`, json('POST', {
          source_job_id: segment.job_id, replacement_job_id: segment.replacement_job_id,
        }));
        setMessage('已记录当前装配版本。独立段保持原版本；有依赖的后段须复核，通过后才能装配新成片。');
      } else {
        await api(`/projects/${projectId}/generation-segments/${segment.shot_id}/regenerate`, json('POST', {
          source_job_id: segment.job_id, expected_shot_revision: segment.shot_revision,
        }));
        setMessage('此生成分段已排入新的制作系列。旧通过版本保留，新版本须通过检查并由你选为装配版本。');
      }
      await reload();
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }
  return <section aria-label="H3 生成分段">
    <h3>生成分段</h3>
    <p>每张卡片是一次 H3 输出。当前作品共 {state.segments.length} 镜，每页最多显示 {PAGE_SIZE} 镜。点缩略图播放；段内剪辑切点共同生成，不能分别重拍。</p>
    <p>已通过镜头发现画面或声音问题，可点「重新生成此段」排入新的串行制作；旧版本保留。新版本通过质检后，再点「选为当前装配版本」。失败、待核对或租约未对账的镜头，先到「生成进度与检查」查看原任务和错误原因；UNKNOWN 任务只能核对，不能直接重发。</p>
    {!state.segments.length && <p className="material-hint">尚无制作系列。选定生产范围并开拍后，这里显示每个生成分段。</p>}
    {state.segments.length > PAGE_SIZE && <nav className="segment-pagination" aria-label="生成分段分页">
      <span>第 {currentPage + 1} / {pageCount} 页 · 显示 {firstVisible}–{lastVisible} / {state.segments.length} 镜</span>
      <div>
        <button type="button" disabled={currentPage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</button>
        <button type="button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>下一页</button>
      </div>
    </nav>}
    <div className="media-grid segment-grid">
      {visibleSegments.map((segment) => <article key={segment.shot_id} className="media-card segment-card">
        <div className="segment-card-head">
          <b>{segment.episode_position ? `第 ${segment.episode_position} 集 · ` : ''}{`第 ${segment.position} 镜 · ${segment.duration_s ?? '—'} 秒`}</b>
          <span className={`segment-status is-${(segment.status || 'none').toLowerCase()}`}>
            {SEGMENT_STATUS[segment.status] ?? segment.status}
          </span>
        </div>
        {segment.artifact_id && segment.job_id ? <>
          <VideoPreview poster={`${API}/jobs/${segment.job_id}/thumbnail?poster=1s`}
            video={`${API}/jobs/${segment.job_id}/media`}
            label={`生成段 ${segment.position} 当前版本`} />
          <div className="segment-card-foot">
            <small>{segment.editorial_cuts?.length ? `段内 ${segment.editorial_cuts.length} 个剪辑点` : '整段一次生成'}</small>
            <OpenVideoFolderButton artifactId={segment.artifact_id} />
          </div>
        </> : <p className="material-hint">尚无视频：等待前序通过、生成或质检完成。</p>}
        <details className="segment-detail">
          <summary>镜头描述</summary>
          <p>{segment.story_beat}</p>
          {segment.editorial_cuts?.length > 0 && <small>段内剪辑范围：{segment.editorial_cuts.map((cut) => `${cut.beat_id ?? '剪辑点'} @ ${cut.start_s ?? 0}s`).join('、')}（整体重拍）</small>}
        </details>
        <div className="segment-card-actions">
          <button type="button" disabled={busy || !segment.can_regenerate} onClick={() => act(segment, false)}>重新生成此段</button>
          {segment.replacement_job_id && <button type="button" disabled={busy || !segment.can_select_replacement} onClick={() => act(segment, true)}>选为当前装配版本</button>}
        </div>
        {segment.replacement_job_id && <small className="segment-note">新版本 · {SEGMENT_STATUS[segment.replacement_status ?? ''] ?? segment.replacement_status}</small>}
        {segment.blocker && <small className="segment-note">当前不能重复排拍：{segment.replacement_job_id ? '新版本等待完成或选用' : '当前版本未通过、仍在执行或需要核对，请先查看制作进度'}（{segment.blocker}）</small>}
        {segment.dependent_job_ids.length > 0 && <details className="segment-detail"><summary>影响 {segment.dependent_job_ids.length} 个真实依赖任务</summary>
          <p>选用新版本会使这些后继失效并要求复核，不会自动无限重拍。</p>
        </details>}
        <details className="segment-detail"><summary>技术信息</summary>
          <small>镜头修订 {segment.shot_revision} · job {segment.job_id?.slice(0, 8) ?? '未创建'} · attempt {segment.attempt_no ?? '未开始'}</small>
        </details>
      </article>)}
    </div>
    {message && <p role="status">{message}</p>}
  </section>;
}
