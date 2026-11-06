import { useState } from 'react';
import { Scissors } from 'lucide-react';
import { ApiError, setEpisodePostContract, type RunEpisode } from '../api';

/**
 * 一集的后期修订：入出点与字幕版本。
 *
 * 只改变已经拍好的片段怎样装配——服务端据此生成新的交付版本，旧版保留，
 * 不重新渲染 H3。留空的入出点表示整条使用；音量混音尚未实现，这里不提供。
 */
export function EpisodePostEditor({ runId, episode, onDone }: {
  runId: string;
  episode: RunEpisode;
  onDone: (message: string, kind: 'success' | 'error') => void;
}) {
  const current = episode.post_contract ?? null;
  const initialCuts = Object.fromEntries((current?.cuts ?? []).map((cut) => [cut.shot_id, cut]));
  const [cuts, setCuts] = useState<Record<string, { in_s: string; out_s: string }>>(() => Object.fromEntries(
    (episode.shot_ids ?? []).map((shotId) => [shotId, {
      in_s: initialCuts[shotId] ? String(initialCuts[shotId].in_s) : '',
      out_s: initialCuts[shotId] ? String(initialCuts[shotId].out_s) : '',
    }]),
  ));
  const [subtitle, setSubtitle] = useState(current?.subtitle_revision ? String(current.subtitle_revision) : '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  async function submit() {
    setBusy(true);
    setProblem('');
    try {
      const chosen = Object.entries(cuts)
        .filter(([, value]) => value.in_s.trim() !== '' || value.out_s.trim() !== '')
        .map(([shotId, value]) => ({ shot_id: shotId, in_s: Number(value.in_s || 0), out_s: Number(value.out_s) }));
      if (chosen.some((cut) => !Number.isFinite(cut.out_s) || cut.out_s <= cut.in_s)) {
        throw new Error('每个填写了的镜头都需要出点，且出点必须大于入点。');
      }
      const result = await setEpisodePostContract(runId, episode.episode_id, {
        cuts: chosen,
        subtitle_revision: subtitle.trim() ? Number(subtitle) : null,
      });
      onDone(result.delivery_order_id
        ? '已按新的剪辑/字幕合同排队重新装配；旧版成片保留，不会重拍。'
        : '剪辑/字幕合同已保存；这一集的镜头齐了之后按它装配。', 'success');
    } catch (exc) {
      const text = exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc);
      setProblem(text);
      onDone(text, 'error');
    } finally {
      setBusy(false);
    }
  }

  return <details className="episode-post-editor">
    <summary><Scissors size={12} /> 修订剪辑 / 字幕（只重装配，不重拍）</summary>
    <div className="episode-post-body">
      <table className="episode-post-cuts">
        <thead><tr><th>镜头</th><th>入点（秒）</th><th>出点（秒）</th></tr></thead>
        <tbody>
          {(episode.shot_ids ?? []).map((shotId, index) => <tr key={shotId}>
            <td>第 {index + 1} 镜 <code>{shotId.slice(0, 8)}</code></td>
            <td><input type="number" min={0} step={0.1} placeholder="整条" aria-label={`第 ${index + 1} 镜入点`}
              value={cuts[shotId]?.in_s ?? ''}
              onChange={(event) => setCuts((rows) => ({ ...rows, [shotId]: { ...rows[shotId], in_s: event.target.value } }))} /></td>
            <td><input type="number" min={0} step={0.1} placeholder="整条" aria-label={`第 ${index + 1} 镜出点`}
              value={cuts[shotId]?.out_s ?? ''}
              onChange={(event) => setCuts((rows) => ({ ...rows, [shotId]: { ...rows[shotId], out_s: event.target.value } }))} /></td>
          </tr>)}
        </tbody>
      </table>
      <label className="run-inline-field">已批准的字幕版本
        <input type="number" min={1} value={subtitle} placeholder="不烧录字幕"
          onChange={(event) => setSubtitle(event.target.value)} />
      </label>
      <p className="run-hint">字幕的时间轴必须对应这一集剪辑后的长度；超出时服务端会拒绝装配，而不是把字幕压到错误的画面上。音量混音尚未实现。</p>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => { void submit(); }}>
        {busy ? '提交中…' : '按新合同重新装配'}
      </button>
      {problem && <p className="form-error">{problem}</p>}
    </div>
  </details>;
}
