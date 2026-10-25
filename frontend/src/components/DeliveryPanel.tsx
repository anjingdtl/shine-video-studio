import { useEffect, useState } from 'react';
import { API, api, json, type Job } from '../api';

type Proposal = { id: string; kind: string; stale: boolean; payload_json: Record<string, unknown> };
type Delivery = { artifact_id: string; sha256: string; byte_size: number; created_at: string;
  provenance: { review_state?: string; duration_s?: number; clips?: unknown[] } };

export default function DeliveryPanel({ projectId, jobs }: { projectId: string; jobs: Job[] }) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [selected, setSelected] = useState('');
  const [edl, setEdl] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  let clips: Array<{ job_id: string; in_s: number; out_s: number }> = [];
  try { const parsed = JSON.parse(edl); if (Array.isArray(parsed.clips)) clips = parsed.clips; } catch { /* editable draft */ }
  async function reload() {
    const [artifacts, rows] = await Promise.all([
      api<Proposal[]>(`/projects/${projectId}/film-artifacts`),
      api<Delivery[]>(`/projects/${projectId}/deliveries`),
    ]);
    setProposals(artifacts.filter(a => a.kind === 'EditDecisionList' && !a.stale));
    setDeliveries(rows);
  }
  useEffect(() => { reload().catch(e => setMessage(String(e))); }, [projectId]);
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
  return <section className="card delivery-panel" id="delivery">
    <div className="panel-header"><h2>成片与导出</h2><button disabled={busy} onClick={() => reload().catch(e => setMessage(String(e)))}>刷新交付列表</button></div>
    <p>选择剪辑提案或填写 EDL，检查镜头顺序及入出点后装配。系统会重新校验来源、技术门禁和文件哈希；待审样片仍需人工审阅。</p>
    <label htmlFor="delivery-proposal">剪辑提案</label><select id="delivery-proposal" value={selected} onChange={e => {
      setSelected(e.target.value);
      const proposal = proposals.find(a => a.id === e.target.value);
      setEdl(proposal ? JSON.stringify(proposal.payload_json, null, 2) : '');
    }}><option value="">手动填写 EDL / 选择提案</option>{proposals.map(a => <option key={a.id} value={a.id}>{a.id}</option>)}</select>
    <label>交付 EDL JSON<textarea rows={9} value={edl} onChange={e => { setSelected(''); setEdl(e.target.value); }} /></label>
    {clips.length > 0 && <ol aria-label="装配镜头顺序">{clips.map((clip, index) => <li key={index}>
      {jobs.find(job => job.id === clip?.job_id)?.shot_name ?? '未知镜头（提交时会校验）'} · {String(clip?.in_s)}–{String(clip?.out_s)} 秒
    </li>)}</ol>}
    <button disabled={busy || !edl.trim()} onClick={assemble}>{busy ? '正在装配…' : '校验并装配成片'}</button>
    {message && <p role="status">{message}</p>}
    {!deliveries.length && <p>暂无交付成片。</p>}
    {deliveries.map(row => <article className="settings-block" key={row.artifact_id}>
      <b>{row.provenance.review_state === 'all_passed' ? '来源审查通过' : '待审样片'}</b>
      <p>{row.created_at} · {row.provenance.duration_s?.toFixed(2)} 秒 · {(row.byte_size / 1024 / 1024).toFixed(1)} MB</p>
      <video controls preload="metadata" src={`${API}/deliveries/${row.artifact_id}/media`} style={{ width: '100%', maxHeight: 480 }} />
      <p><a href={`${API}/deliveries/${row.artifact_id}/media`} download>下载 MP4</a></p>
      <small>SHA-256 {row.sha256}</small>
      <details><summary>镜头来源与装配记录</summary><pre>{JSON.stringify(row.provenance, null, 2)}</pre></details>
    </article>)}
  </section>;
}
