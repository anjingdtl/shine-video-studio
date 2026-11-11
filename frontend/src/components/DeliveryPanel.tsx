import { useCallback, useEffect, useRef, useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { API, api, json, type Job } from '../api';
import { failureLabels, label } from '../constants';
import GenerationSegmentsPanel, { LazyVideo } from './GenerationSegmentsPanel';
import { OpenVideoFolderButton } from './OpenVideoFolderButton';

type Proposal = { id: string; kind: string; stale: boolean; created_at?: string; payload_json: Record<string, unknown> };
type Delivery = { artifact_id: string; sha256: string; byte_size: number; created_at: string; deleted?: boolean;
  provenance: { review_state?: string; duration_s?: number; clips?: unknown[]; edl_source?: string } };
type DeliveryOrder = {
  id: string; series_id: string; version: number; status: string;
  edl_source: string | null; artifact_id: string | null; sha256?: string;
  attempt_count: number; max_attempts: number; failure_code: string | null;
  created_at: string; delivered_at: string | null; artifact_deleted?: boolean;
};

const orderText: Record<string, string> = {
  PENDING: '待装配',
  ASSEMBLING: '装配中',
  DELIVERED: '已自动交付',
  FAILED: '装配失败',
  BLOCKED: '已阻断（需人工处理）',
  CANCELLED: '已取消',
};

/** 后端时间戳是无时区的 UTC 字符串：必须显式按 UTC 解析，否则本地时区会错位。 */
function utcMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const normalised = value.includes('T') ? value : value.replace(' ', 'T');
  const parsed = new Date(normalised.endsWith('Z') ? normalised : normalised + 'Z').getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

type Props = {
  projectId: string;
  jobs: Job[];
  /** Whether the delivery view is currently the visible one; entering it reloads. */
  active: boolean;
  /** Bumped by the global topbar refresh so it covers delivery data too. */
  refreshEpoch: number;
};

export default function DeliveryPanel({ projectId, jobs, active, refreshEpoch }: Props) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [orders, setOrders] = useState<DeliveryOrder[]>([]);
  const [seriesActive, setSeriesActive] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState('');
  const [edl, setEdl] = useState('');
  const [busy, setBusy] = useState(false);
  /** 自动交付记录会长期累积：默认只展开最近 5 次，历史按需加载。 */
  const [orderLimit, setOrderLimit] = useState(5);
  const [message, setMessage] = useState('');
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  let clips: Array<{ job_id: string; in_s: number; out_s: number; transition?: string }> = [];
  try { const parsed = JSON.parse(edl); if (Array.isArray(parsed.clips)) clips = parsed.clips; } catch { /* editable draft */ }

  /** Visual edits write straight back into the EDL JSON state: one source of
   *  truth, the assembly POST contract stays byte-compatible. */
  function updateClip(index: number, key: 'in_s' | 'out_s', value: number) {
    try {
      const parsed = JSON.parse(edl) as { clips: Array<Record<string, unknown>> };
      if (!Array.isArray(parsed.clips) || !parsed.clips[index]) return;
      parsed.clips[index][key] = value;
      setEdl(JSON.stringify(parsed, null, 2));
    } catch { /* advanced JSON editing keeps its own draft */ }
  }

  const reload = useCallback(async () => {
    const requested = projectId;
    const [artifacts, rows, orderRows, seriesRows] = await Promise.all([
      api<Proposal[]>(`/projects/${projectId}/film-artifacts`),
      api<Delivery[]>(`/projects/${projectId}/deliveries`),
      api<DeliveryOrder[]>(`/projects/${projectId}/delivery-orders`),
      api<Array<{ id: string; status: string }>>(`/projects/${projectId}/production-series`),
    ]);
    if (projectRef.current !== requested) return; // a stale response from a previous project
    setLoadError('');
    setProposals(artifacts.filter(a => a.kind === 'EditDecisionList' && !a.stale));
    setDeliveries(rows);
    setOrders(orderRows.filter((row, index, all) => !row.artifact_id
      || all.findIndex((other) => other.artifact_id === row.artifact_id) === index));
    setSeriesActive(seriesRows.some(row => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status)));
  }, [projectId]);

  useEffect(() => {
    // Initial load, project switch, entering the delivery view, and the global
    // topbar refresh all go through here, so new orders surface without the
    // user having to find the local refresh button.
    reload().catch((error: Error) => {
      setLoadError(String(error.message || error));
      setMessage('');
    });
  }, [reload, active, refreshEpoch]);

  /** Production progress: any change in the shot jobs (a new series job, a
   *  status transition, a finished render) may produce delivery output, so
   *  reload on the change — without polling while nothing changes. */
  const jobFingerprint = jobs.map((job) => `${job.id}:${job.status}:${job.video_created ? 1 : 0}`).join('|');
  const lastFingerprintRef = useRef(jobFingerprint);
  useEffect(() => {
    if (lastFingerprintRef.current === jobFingerprint) return;
    lastFingerprintRef.current = jobFingerprint;
    reload().catch(() => undefined);
  }, [jobFingerprint, reload]);

  // While a production series is live (or about to deliver), keep polling so
  // the automatically assembled reel appears without a manual refresh.
  useEffect(() => {
    if (!seriesActive) return;
    const timer = window.setInterval(() => { reload().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [seriesActive, reload]);
  // After a delivery completes, poll a little longer so the player appears.
  const deliveredRecently = orders.some((order) => order.status === 'ASSEMBLING'
    || (order.status === 'DELIVERED' && order.delivered_at
      && Date.now() - new Date(order.delivered_at).getTime() < 120000));

  const deliveredJobIds = new Set(deliveries.flatMap((delivery) =>
    (delivery.provenance.clips ?? [])
      .filter((clip): clip is { job_id: string } => !!clip && typeof clip === 'object'
        && 'job_id' in clip && typeof clip.job_id === 'string')
      .map((clip) => clip.job_id)));
  const deliveredJobs = jobs.filter((job) => deliveredJobIds.has(job.id));
  const latestAsrReview = (job: Job) => (job.advisory_reviews ?? [])
    .filter((review) => review.kind === 'ASR'
      && (!job.artifact_id || review.artifact_id === job.artifact_id))
    .at(-1);
  const asrReviewByJob = new Map(deliveredJobs.map((job) => [job.id, latestAsrReview(job)]));
  const asrUnavailableCount = [...asrReviewByJob.values()].filter((review) =>
    review?.status === 'UNAVAILABLE').length;
  const asrRouteMissingCount = [...asrReviewByJob.values()].filter((review) =>
    review?.error_code === 'ASR_ROUTE_NOT_CONFIGURED').length;
  const asrMissingCount = [...asrReviewByJob.values()].filter((review) => !review).length;
  const asrUnverifiedCount = [...asrReviewByJob.values()].filter((review) =>
    !review || review.status !== 'COMPLETED' || review.is_mock !== false || !review.outcome).length;
  const asrFlaggedCount = [...asrReviewByJob.values()].filter((review) =>
    review?.status === 'COMPLETED' && review.is_mock === false && review.outcome !== 'passed').length;

  useEffect(() => {
    if (seriesActive || !deliveredRecently) return;
    const timer = window.setInterval(() => { reload().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [seriesActive, deliveredRecently, reload]);

  async function assemble() {
    setBusy(true); setMessage('');
    try {
      const result = await api<{ review_state: string }>(`/projects/${projectId}/deliveries`, json('POST', JSON.parse(edl)));
      setMessage(result.review_state === 'all_passed'
        ? '装配完成，来源镜头均已通过审查。请播放复核成片。'
        : '已生成待审样片，来源镜头尚未全部通过审查。');
      await reload();
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  async function retryOrder(orderId: string) {
    setBusy(true);
    try {
      await api(`/delivery-orders/${orderId}/retry`, json('POST', {}));
      await reload();
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  return <section className="card delivery-panel" id="delivery">
    <div className="panel-header"><h2>成片与导出</h2><button type="button" className="btn-secondary" disabled={busy} onClick={() => reload().catch(e => setMessage(String(e)))}>刷新交付列表</button></div>
    <div className="notice">
      <HelpCircle size={13} />
      <div>
        <p><b>镜头全部通过后，这里自动出现成片；没出现就回上一步看卡住的镜头。</b></p>
      </div>
    </div>

    {(asrUnverifiedCount > 0 || asrFlaggedCount > 0) && <div className="blocked-callout" role="status" aria-label="音频语言核对未完成">
      <HelpCircle size={15} />
      <span>
        音频语言/禁声核对未完整：{asrUnverifiedCount} 个交付镜头没有可用的真实 ASR 结论（{asrUnavailableCount} 个 ASR 不可用，其中 {asrRouteMissingCount} 个未配置语音识别路由；{asrMissingCount} 个没有 ASR 记录）；{asrFlaggedCount > 0 && `另有 ${asrFlaggedCount} 个镜头的 ASR 结果需复核；` }技术音轨可解码不代表对白或旁白语言正确。
      </span>
    </div>}

    {loadError && <p className="failure" role="alert">交付列表读取失败：{loadError}（显示的不是最新数据，可点击「刷新交付列表」重试）</p>}

    <GenerationSegmentsPanel key={projectId} projectId={projectId} fingerprint={`${jobFingerprint}:${refreshEpoch}`} />

    {orders.length > 0 && <div className="auto-delivery" aria-label="自动交付">
      {[...orders].sort((a, b) => (utcMs(b.delivered_at ?? b.created_at) ?? 0) - (utcMs(a.delivered_at ?? a.created_at) ?? 0))
        .slice(0, orderLimit).map((order, index) => {
        const deliveredAt = utcMs(order.delivered_at ?? order.created_at);
        const timeLabel = deliveredAt != null
          ? new Date(deliveredAt).toLocaleString('zh-CN', { hour12: false })
          : null;
        const head = (
          <>
            <b>自动交付 v{order.version} · {orderText[order.status] ?? order.status}{index === 0 && <em className="latest-badge">最新</em>}</b>
            <p>{timeLabel && <>交付于 {timeLabel} · </>}{order.edl_source === 'frozen_edit_intent' ? '剪辑来源：冻结剪辑意图' : '剪辑来源：默认剪辑规则'}
              {order.attempt_count > 0 && <> · 尝试 {order.attempt_count}/{order.max_attempts}</>}
              {order.failure_code && <span className="failure"> · {label(failureLabels, order.failure_code)}</span>}
            </p>
            {order.artifact_id && <OpenVideoFolderButton artifactId={order.artifact_id} />}
          </>
        );
        const body = <>
          {order.status === 'FAILED' && <button type="button" className="btn-secondary" disabled={busy} onClick={() => retryOrder(order.id)}>重新排队装配</button>}
          {order.status === 'DELIVERED' && order.artifact_id && (order.artifact_deleted
            ? <p className="failure">这份历史成片的文件已在「成片库」删除；交付回执仍保留，但不能再播放。</p>
            : <LazyVideo src={`${API}/deliveries/${order.artifact_id}/media`} label={`完整成片 v${order.version}`} />)}
        </>;
        // 最新交付直接展开；历史交付折叠成一行，避免旧样片占据页面误导。
        return index === 0
          ? <article className="settings-block latest" key={order.id}>{head}{body}</article>
          : <details className="history-order" key={order.id}><summary>{head}</summary>{body}</details>;
      })}
      {orders.length > orderLimit && <button type="button" className="list-more"
        onClick={() => setOrderLimit((limit) => limit + 5)}>
        查看更早的交付记录（还有 {orders.length - orderLimit} 条）
      </button>}
    </div>}

    <p>连续制作会自动装配成片；下面的手工装配只用于补剪。</p>
    <label htmlFor="delivery-proposal">剪辑方案</label><select id="delivery-proposal" value={selected} disabled={proposals.length === 0} onChange={e => {
      setSelected(e.target.value);
      const proposal = proposals.find(a => a.id === e.target.value);
      setEdl(proposal ? JSON.stringify(proposal.payload_json, null, 2) : '');
    }}><option value="">选择剪辑方案</option>{proposals.map(a => <option key={a.id} value={a.id}>{a.id.slice(0, 10)}…（{new Date(a.created_at ?? Date.now()).toLocaleString()}）</option>)}</select>
    <div className="edl-visual" aria-label="装配镜头顺序（可编辑）">
      {clips.length === 0 && <p className="empty-inline">还没有片段；选择剪辑方案或展开高级编辑。</p>}
      {clips.map((clip, index) => (
        <div className="edl-clip-row" key={index}>
          <b>{index + 1}. {jobs.find(job => job.id === clip?.job_id)?.shot_name ?? '未知镜头（装配时校验）'}</b>
          <label>入点（秒）<input type="number" min={0} step={0.1} value={clip?.in_s ?? 0}
            onChange={e => updateClip(index, 'in_s', Number(e.target.value))} /></label>
          <label>出点（秒）<input type="number" min={0} step={0.1} value={clip?.out_s ?? 0}
            onChange={e => updateClip(index, 'out_s', Number(e.target.value))} /></label>
          <span className="edl-clip-note">{clip?.transition ?? 'CUT'}</span>
        </div>
      ))}
    </div>
    <details className="edl-advanced"><summary>高级：剪辑数据编辑（技术格式）</summary>
      <label>剪辑数据<textarea rows={9} value={edl} onChange={e => { setSelected(''); setEdl(e.target.value); }} /></label>
    </details>
    {clips.length > 0 && <ol aria-label="装配镜头顺序">{clips.map((clip, index) => <li key={index}>
      {jobs.find(job => job.id === clip?.job_id)?.shot_name ?? '未知镜头（提交时会校验）'} · {String(clip?.in_s)}–{String(clip?.out_s)} 秒
    </li>)}</ol>}
    <button type="button" className="btn-secondary" disabled={busy || !edl.trim()} onClick={assemble}>{busy ? '正在装配…' : '校验并装配成片（人工修订）'}</button>
    {message && <p role="status">{message}</p>}
    {!loadError && !deliveries.length && !orders.some(o => o.status === 'DELIVERED') && <p>暂无交付成片。</p>}
    <div className="delivery-list">
      {deliveries.filter(row => !orders.some(order => order.artifact_id === row.artifact_id)).map(row => <details className="settings-block" key={row.artifact_id}><summary>成片历史 · {row.created_at}</summary><article>
        <b>{row.provenance.review_state === 'all_passed' ? '来源审查通过' : '待审样片'}</b>
        <p>{row.created_at} · {row.provenance.duration_s?.toFixed(2)} 秒 · {(row.byte_size / 1024 / 1024).toFixed(1)} MB</p>
        {row.deleted
          ? <p className="failure">文件已在「成片库」删除，历史记录保留。</p>
          : <LazyVideo src={`${API}/deliveries/${row.artifact_id}/media`} label="完整成片历史版本" />}
        <p><OpenVideoFolderButton artifactId={row.artifact_id} /></p>
        <small>校验指纹（SHA-256）：<code>{row.sha256.slice(0, 16)}…</code>，用于核对文件未被改动</small>
        <details><summary>镜头来源与装配记录</summary><pre>{JSON.stringify(row.provenance, null, 2)}</pre></details>
      </article></details>)}
    </div>
  </section>;
}
