import { useCallback, useEffect, useState } from 'react';
import { API, api, json, type Job } from '../api';
import { failureLabels, label } from '../constants';

type Proposal = { id: string; kind: string; stale: boolean; payload_json: Record<string, unknown> };
type Delivery = { artifact_id: string; sha256: string; byte_size: number; created_at: string;
  provenance: { review_state?: string; duration_s?: number; clips?: unknown[]; edl_source?: string } };
type DeliveryOrder = {
  id: string; series_id: string; version: number; status: string;
  edl_source: string | null; artifact_id: string | null; sha256?: string;
  attempt_count: number; max_attempts: number; failure_code: string | null;
  delivered_at: string | null;
};

const orderText: Record<string, string> = {
  PENDING: '待装配',
  ASSEMBLING: '装配中',
  DELIVERED: '已自动交付',
  FAILED: '装配失败',
  BLOCKED: '已阻断（需人工处理）',
  CANCELLED: '已取消',
};

export default function DeliveryPanel({ projectId, jobs }: { projectId: string; jobs: Job[] }) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [orders, setOrders] = useState<DeliveryOrder[]>([]);
  const [seriesActive, setSeriesActive] = useState(false);
  const [selected, setSelected] = useState('');
  const [edl, setEdl] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  let clips: Array<{ job_id: string; in_s: number; out_s: number }> = [];
  try { const parsed = JSON.parse(edl); if (Array.isArray(parsed.clips)) clips = parsed.clips; } catch { /* editable draft */ }

  const reload = useCallback(async () => {
    const [artifacts, rows, orderRows, seriesRows] = await Promise.all([
      api<Proposal[]>(`/projects/${projectId}/film-artifacts`),
      api<Delivery[]>(`/projects/${projectId}/deliveries`),
      api<DeliveryOrder[]>(`/projects/${projectId}/delivery-orders`),
      api<Array<{ id: string; status: string }>>(`/projects/${projectId}/production-series`),
    ]);
    setProposals(artifacts.filter(a => a.kind === 'EditDecisionList' && !a.stale));
    setDeliveries(rows);
    setOrders(orderRows);
    setSeriesActive(seriesRows.some(row => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status)));
  }, [projectId]);

  useEffect(() => { reload().catch(e => setMessage(String(e))); }, [reload]);
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
    <div className="panel-header"><h2>成片与导出</h2><button disabled={busy} onClick={() => reload().catch(e => setMessage(String(e)))}>刷新交付列表</button></div>

    {orders.length > 0 && <div className="auto-delivery" aria-label="自动交付">
      {orders.map((order) => <article className="settings-block" key={order.id}>
        <b>自动交付 v{order.version} · {orderText[order.status] ?? order.status}</b>
        <p>{order.edl_source === 'frozen_edit_intent' ? '剪辑来源：冻结剪辑意图' : '剪辑来源：默认剪辑规则'}
          {order.artifact_id && <> · <a href={`${API}/deliveries/${order.artifact_id}/media`} download>下载 MP4</a></>}
          {order.attempt_count > 0 && <> · 尝试 {order.attempt_count}/{order.max_attempts}</>}
          {order.failure_code && <span className="failure"> · {label(failureLabels, order.failure_code)}</span>}
        </p>
        {order.status === 'FAILED' && <button disabled={busy} onClick={() => retryOrder(order.id)}>重新排队装配</button>}
        {order.status === 'DELIVERED' && order.artifact_id
          && <video controls preload="metadata" src={`${API}/deliveries/${order.artifact_id}/media`} style={{ width: '100%', maxHeight: 480 }} />}
      </article>)}
    </div>}

    <p>生产序列完成后，常驻 Worker 会自动生成 EDL 并装配成片（无需人工填写）。以下手工装配保留给人工修订与补剪。</p>
    <label htmlFor="delivery-proposal">剪辑提案</label><select id="delivery-proposal" value={selected} onChange={e => {
      setSelected(e.target.value);
      const proposal = proposals.find(a => a.id === e.target.value);
      setEdl(proposal ? JSON.stringify(proposal.payload_json, null, 2) : '');
    }}><option value="">手动填写 EDL / 选择提案</option>{proposals.map(a => <option key={a.id} value={a.id}>{a.id}</option>)}</select>
    <label>交付 EDL JSON<textarea rows={9} value={edl} onChange={e => { setSelected(''); setEdl(e.target.value); }} /></label>
    {clips.length > 0 && <ol aria-label="装配镜头顺序">{clips.map((clip, index) => <li key={index}>
      {jobs.find(job => job.id === clip?.job_id)?.shot_name ?? '未知镜头（提交时会校验）'} · {String(clip?.in_s)}–{String(clip?.out_s)} 秒
    </li>)}</ol>}
    <button disabled={busy || !edl.trim()} onClick={assemble}>{busy ? '正在装配…' : '校验并装配成片（人工修订）'}</button>
    {message && <p role="status">{message}</p>}
    {!deliveries.length && !orders.some(o => o.status === 'DELIVERED') && <p>暂无交付成片。</p>}
    {deliveries.map(row => <article className="settings-block" key={row.artifact_id}>
      <b>{row.provenance.review_state === 'all_passed' ? '来源审查通过' : '待审样片'}</b>
      <p>{row.created_at} · {row.provenance.duration_s?.toFixed(2)} 秒 · {(row.byte_size / 1024 / 1024).toFixed(1)} MB</p>
      <video controls preload="metadata" src={`${API}/deliveries/${row.artifact_id}/media`} style={{ width: '100%', maxHeight: 480 }} />
      <p><a href={`${API}/deliveries/${row.artifact_id}/media`} download>下载 MP4</a></p>
      <small>校验指纹（SHA-256）：<code>{row.sha256.slice(0, 16)}…</code>，用于核对文件未被改动</small>
      <details><summary>镜头来源与装配记录</summary><pre>{JSON.stringify(row.provenance, null, 2)}</pre></details>
    </article>)}
  </section>;
}
