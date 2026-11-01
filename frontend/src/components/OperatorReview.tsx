import { useEffect, useState } from 'react';
import { api, json } from '../api';
import { label, qcResultLabels, reviewOptionLabels, statusLabels } from '../constants';

const labels: Record<string, string> = {
  identity: '角色身份一致性', costume: '服装与造型', scene_props: '场景及道具',
  camera_action: '动作与运镜', style: '影视风格', dialogue: '中文对白内容', voice: '声音及音色',
  lip_sync: '口型同步', continuity: '画面连续性', other: '其他异常',
};
const reviewOptions = ['NOT_REVIEWED', 'PASS', 'FAIL', 'NOT_APPLICABLE'];
type Context = { snapshot: string; binding: Record<string, unknown>; operator_id: string };

export default function OperatorReview({ jobId }: { jobId: string }) {
  const [context, setContext] = useState<Context | null>(null);
  const [items, setItems] = useState<Record<string, { status: string; evidence: string }>>({});
  const [watched, setWatched] = useState(false);
  const [listened, setListened] = useState(false);
  const [media, setMedia] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { return () => { if (media) URL.revokeObjectURL(media); }; }, [media]);
  async function load() {
    setBusy(true); setContext(null); setMessage('');
    try {
      const headers = { 'X-Shine-Local-Action': '1' };
      const value = await api<Context>(`/operator/jobs/${jobId}`, { headers });
      const response = await fetch(`/api/v1/operator/jobs/${jobId}/media`, { headers });
      if (!response.ok) throw new Error('媒体完整性或授权检查失败');
      setMedia(URL.createObjectURL(await response.blob()));
      setContext(value); setWatched(false); setListened(false);
      setItems(Object.fromEntries(Object.keys(labels).map(k => [k, { status: 'NOT_REVIEWED', evidence: '' }])));
    } catch (error) { setMessage(String(error)); } finally { setBusy(false); }
  }
  async function submit() {
    if (!context) return;
    setBusy(true);
    try {
      const result = await api<{ status: string; result: string }>(`/operator/jobs/${jobId}/review`, {
        ...json('POST', { snapshot: context.snapshot, request_id: crypto.randomUUID(), items, watched, listened }),
      });
      setMessage(`已记录人工审片：${label(qcResultLabels, result.result)} · 任务状态 ${label(statusLabels, result.status)}。回到任务列表可看到更新。`);
      setContext(null);
    } catch (error) { setMessage(String(error)); setContext(null); } finally { setBusy(false); }
  }
  return <section className="settings-block operator-review">
    <h3>本地操作员分项审片</h3>
    <p>直接打开本机视频进行审片。普通话对白须逐句试听；音轨存在不能代替对白审核。</p>
    <button disabled={busy} onClick={load}>加载当前视频</button>
    {context && <>
      <p>人工审核者：{context.operator_id} · 当前 Attempt / 媒体绑定</p>
      <p style={{ overflowWrap: 'anywhere' }}>Attempt：{String(context.binding.attempt_id)}<br />媒体 SHA-256：{String(context.binding.artifact_sha256)}</p>
      <details><summary>查看修订、风格、规则包、技术 QC 与完整快照</summary>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(context.binding, null, 2)}</pre>
      </details>
      <video controls src={media} style={{ width: '100%', maxHeight: 480 }} />
      <label><input type="checkbox" checked={watched} onChange={e => setWatched(e.target.checked)} />我已完整观看当前视频</label>
      <label><input type="checkbox" checked={listened} onChange={e => setListened(e.target.checked)} />我已人工试听对白、音色及声画同步</label>
      {Object.entries(labels).map(([key, text]) => <div key={key}>
        <label>{text}<select value={items[key]?.status} onChange={e => setItems({ ...items, [key]: { ...items[key], status: e.target.value } })}>
          {reviewOptions.map(s => <option key={s} value={s}>{label(reviewOptionLabels, s)}</option>)}
        </select></label>
        <textarea aria-label={`${text}的证据`} placeholder="具体画面/时间点/实际听到的台词；选「不适用」也要写清理由" value={items[key]?.evidence} onChange={e => setItems({ ...items, [key]: { ...items[key], evidence: e.target.value } })} />
      </div>)}
      <p>未审项保持缺失；不适用不能替代必需覆盖；FAIL 保持待处理，不会放行。</p>
      <button disabled={busy} onClick={submit}>提交人工审片结论</button>
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
