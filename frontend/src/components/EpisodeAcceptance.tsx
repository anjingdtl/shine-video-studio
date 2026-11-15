import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, ShieldAlert } from 'lucide-react';
import { API, decideDeliveryAcceptance, type DeliveryAcceptance } from '../api';

type Props = {
  artifactId: string;
  sha256: string;
  title: string;
  acceptance: DeliveryAcceptance | null;
  onDecided: (acceptance: DeliveryAcceptance) => void;
  onClose: () => void;
};

/**
 * 「待你确认」的真实动作：完整看过这一版成片之后，明确接受或拒绝。
 *
 * 技术交付不等于艺术验收。这里记录的是人的决定：接受必须声明看完，
 * 拒绝必须写清问题；决定绑定当前看到的 SHA-256，历史版本不被追改。
 */
export function EpisodeAcceptance({ artifactId, sha256, title, acceptance, onDecided, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [watched, setWatched] = useState(false);
  const [evidence, setEvidence] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (typeof node.showModal === 'function') node.showModal();
    else node.setAttribute('open', '');
  }, []);

  async function submit(decision: 'accepted' | 'rejected') {
    setBusy(true); setMessage('');
    try {
      const result = await decideDeliveryAcceptance(artifactId, {
        decision, watched, evidence: evidence.trim(), expected_sha256: sha256,
      });
      onDecided(result.acceptance);
      onClose();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className="media-preview-dialog acceptance-dialog" aria-label={`验收 ${title}`}
    onClose={onClose}>
    <div className="acceptance-head">
      <h3>观看并确认 · {title}</h3>
      <button type="button" className="btn-secondary" onClick={() => { dialog.current?.close(); onClose(); }}>关闭</button>
    </div>
    {acceptance && <p className={acceptance.decision === 'accepted' ? 'acceptance-current ok' : 'acceptance-current bad'}>
      {acceptance.decision === 'accepted'
        ? <><CheckCircle2 size={14} /> 你已确认接受这一版（{acceptance.actor}）</>
        : <><ShieldAlert size={14} /> 你已标记问题：{acceptance.evidence || '未写原因'}</>}
    </p>}
    <video controls preload="metadata" src={`${API}/deliveries/${artifactId}/media`}
      style={{ width: '100%', maxHeight: '52vh' }} aria-label={title} />
    <p className="module-guide">
      自动交付只代表文件可播放、技术检查通过。画面、表演、声音是否符合你的意图，
      只有你看过之后才能决定。确认接受不会改写任何镜头或历史版本。
    </p>
    <label className="acceptance-watched">
      <input type="checkbox" checked={watched} onChange={(event) => setWatched(event.target.checked)} />
      我已完整观看这一版成片（含声音）
    </label>
    <label>备注 / 问题说明（拒绝时必填）
      <textarea rows={3} value={evidence} onChange={(event) => setEvidence(event.target.value)}
        placeholder="例如：第 3 镜口型与对白不符；整体通过，无需修改" />
    </label>
    <div className="row acceptance-actions">
      <button type="button" className="btn-primary must-button" disabled={busy || !watched}
        onClick={() => void submit('accepted')}>确认接受这一版</button>
      <button type="button" className="btn-secondary" disabled={busy || !evidence.trim()}
        onClick={() => void submit('rejected')}>有问题，拒绝这一版</button>
    </div>
    {!watched && <small>勾选「已完整观看」后才能接受；这是人工验收，不是自动放行。</small>}
    {message && <p role="alert" className="blocked-callout">{message}</p>}
  </dialog>;
}

export default EpisodeAcceptance;