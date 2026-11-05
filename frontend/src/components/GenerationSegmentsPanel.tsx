import { useCallback, useEffect, useState } from 'react';
import { API, api, json } from '../api';

type Segment = {
  shot_id: string; position: number; story_beat: string; duration_s: number;
  shot_revision: number; job_id: string | null; attempt_no: number | null;
  status: string; artifact_id: string | null; blocker: string | null; can_regenerate: boolean;
  editorial_cuts: { beat_id?: string; start_s?: number }[];
  dependent_job_ids: string[]; replacement_job_id: string | null;
  replacement_status: string | null; can_select_replacement: boolean;
};
type SegmentState = { series_id: string | null; segments: Segment[] };

export function LazyVideo({ src, label }: { src: string; label: string }) {
  const [loaded, setLoaded] = useState(false);
  return loaded ? <video controls preload="metadata" src={src} aria-label={label} style={{ width: '100%', maxHeight: 480 }} />
    : <button type="button" onClick={() => setLoaded(true)}>预览 {label}</button>;
}

export default function GenerationSegmentsPanel({ projectId, fingerprint }: { projectId: string; fingerprint: string }) {
  const [state, setState] = useState<SegmentState>({ series_id: null, segments: [] });
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const reload = useCallback(async () => {
    const result = await api<SegmentState>(`/projects/${projectId}/generation-segments`);
    setState(result);
  }, [projectId]);
  useEffect(() => { void reload().catch((e) => setMessage(String(e))); }, [reload, fingerprint]);
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
        await api(`/projects/${projectId}/generation-segments/${state.series_id}/select`, json('POST', {
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
    <p>每张卡片对应 H3 一次输出。段内剪辑切点共同生成，不能分别重拍。完整成片在下方。</p>
    {!state.segments.length && <p>尚无制作系列。选定生产范围并开拍后，这里显示每个生成分段。</p>}
    <div className="media-grid">
      {state.segments.map((segment) => <article key={segment.shot_id} className="media-card">
        <b>生成段 {segment.position} · {segment.duration_s ?? '—'} 秒</b>
        <p>{segment.story_beat}</p>
        {segment.editorial_cuts?.length > 0 && <small>段内剪辑范围：{segment.editorial_cuts.map((cut) => `${cut.beat_id ?? '剪辑点'} @ ${cut.start_s ?? 0}s`).join('、')}（整体重拍）</small>}
        <small>镜头修订 {segment.shot_revision} · job {segment.job_id?.slice(0, 8) ?? '未创建'} · attempt {segment.attempt_no ?? '未开始'}</small>
        <span>{segment.status}</span>
        {segment.artifact_id && segment.job_id ? <>
          <LazyVideo key={segment.job_id} src={`${API}/jobs/${segment.job_id}/media`} label={`生成段 ${segment.position} 当前版本`} />
          <a href={`${API}/jobs/${segment.job_id}/media`} download>下载此段当前版本</a>
        </> : <p>尚无视频：等待前序通过、生成或质检完成。</p>}
        {segment.dependent_job_ids.length > 0 && <details><summary>影响 {segment.dependent_job_ids.length} 个真实依赖任务</summary>
          <p>选用新版本会使这些后继失效并要求复核，不会自动无限重拍。</p>
          <ul>{segment.dependent_job_ids.map((id) => <li key={id}>{id}</li>)}</ul>
        </details>}
        <button type="button" disabled={busy || !segment.can_regenerate} onClick={() => act(segment, false)}>重新生成此段</button>
        {segment.blocker && <small>当前不能重复排拍：{segment.replacement_job_id ? '新版本等待完成或选用' : '当前版本未通过、仍在执行或需要核对，请先查看制作进度'}（{segment.blocker}）</small>}
        {segment.replacement_job_id && <div>
          <b>新版本 · {segment.replacement_status}</b>
          {segment.replacement_status === 'PASSED' && <LazyVideo key={segment.replacement_job_id} src={`${API}/jobs/${segment.replacement_job_id}/media`} label={`生成段 ${segment.position} 新版本对照`} />}
          <button type="button" disabled={busy || !segment.can_select_replacement} onClick={() => act(segment, true)}>选为当前装配版本</button>
          {!segment.can_select_replacement && <small>新版本尚未通过最终门禁，不能装配成已接受成片。</small>}
        </div>}
      </article>)}
    </div>
    {message && <p role="status">{message}</p>}
  </section>;
}
