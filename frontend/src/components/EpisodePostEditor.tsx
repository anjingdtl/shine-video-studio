import { useEffect, useState } from 'react';
import { Scissors } from 'lucide-react';
import {
  ApiError, approveSubtitleRevision, listSubtitleTracks, setEpisodePostContract,
  type DeliveryOrderProgress, type RunEpisode, type SubtitleTrack,
} from '../api';
import { label, subtitleStatusLabels } from '../constants';

/**
 * 一集的后期修订：入出点与字幕版本。
 *
 * 只改变已经拍好的片段怎样装配——服务端据此生成新的交付版本，旧版保留，
 * 不重新渲染 H3。留空的入出点表示整条使用；音量混音尚未实现，这里不提供。
 * 字幕版本在这里选择；优先共用父级字幕折叠卡的同一份轨道台账，避免生成
 * 字幕后下拉仍停在旧快照上；草稿版在提交时经你的明确动作先批准再烧录。
 */
export function EpisodePostEditor({
  projectId, runId, episode, tracks: parentTracks, suggestedRevision, assembly, onTracksRefresh, onDone,
}: {
  projectId: string;
  runId: string;
  episode: RunEpisode;
  tracks?: SubtitleTrack[];
  suggestedRevision?: number | null;
  assembly?: DeliveryOrderProgress | null;
  onTracksRefresh?: () => void;
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
  const [localTracks, setLocalTracks] = useState<SubtitleTrack[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  const tracks = (parentTracks ?? localTracks).filter(
    (row) => !row.episode_id || row.episode_id === episode.episode_id,
  );

  const loadTracks = () => {
    if (onTracksRefresh) {
      onTracksRefresh();
      return;
    }
    listSubtitleTracks(projectId).then(setLocalTracks).catch(() => setLocalTracks([]));
  };

  useEffect(() => {
    if (suggestedRevision != null) {
      setSubtitle(String(suggestedRevision));
    }
  }, [suggestedRevision]);

  const selectedTrack = tracks.find((row) => String(row.revision) === subtitle) ?? null;
  const needsApproval = Boolean(selectedTrack && selectedTrack.status !== 'APPROVED');

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
      if (subtitle.trim() && needsApproval) {
        await approveSubtitleRevision(projectId, Number(subtitle));
      }
      const result = await setEpisodePostContract(runId, episode.episode_id, {
        cuts: chosen,
        subtitle_revision: subtitle.trim() ? Number(subtitle) : null,
      });
      loadTracks();
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

  const assemblyRunning = Boolean(assembly && ['PENDING', 'ASSEMBLING'].includes(assembly.status));
  const assemblyFailed = Boolean(assembly && ['FAILED', 'BLOCKED'].includes(assembly.status));
  const assemblyPct = Math.max(0, Math.min(100, Number(assembly?.percent ?? 0)));

  return <details className="episode-post-editor" onToggle={(event) => {
    if (event.currentTarget.open) loadTracks();
  }}>
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
      <label className="run-inline-field">烧录字幕版本
        <select value={subtitle} onChange={(event) => setSubtitle(event.target.value)}>
          <option value="">不烧录字幕</option>
          {tracks.map((row) => <option key={row.id} value={String(row.revision)}>
            第 {row.revision} 版 · {label(subtitleStatusLabels, row.status)} · {row.cues.length} 行
          </option>)}
        </select>
      </label>
      <p className="run-hint">
        {needsApproval
          ? '所选字幕还是草稿：提交会先经你这一次点击批准，再烧录装配。'
          : '字幕的时间轴必须对应这一集剪辑后的长度；超出时服务端会拒绝装配，而不是把字幕压到错误的画面上。音量混音尚未实现。'}
      </p>
      <button type="button" className="btn-secondary" disabled={busy || assemblyRunning} onClick={() => { void submit(); }}>
        {busy ? '提交中…' : assemblyRunning ? '正在烧录装配…'
          : needsApproval ? '批准该版字幕并按新合同重新装配' : '按新合同重新装配'}
      </button>
      {assemblyRunning && assembly && <div className="episode-assembly-progress">
        <div className="run-progress-bar" role="progressbar" aria-label="字幕烧录与装配进度"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={assemblyPct}>
          <i style={{ width: `${Math.max(4, assemblyPct)}%` }} className="is-working" />
        </div>
        <p className="run-progress-note">{assemblyPct}% · {assembly.detail}</p>
      </div>}
      {assemblyFailed && assembly && <p className="form-error">装配受阻：{assembly.detail}</p>}
      {problem && <p className="form-error">{problem}</p>}
    </div>
  </details>;
}
