import { useEffect, useState } from 'react';
import { Captions } from 'lucide-react';
import {
  compileEpisodeSubtitles, getSubtitleStyle, listSubtitleTracks, putSubtitleStyle,
  type DeliveryOrderProgress, type DirectorEpisodeOverview, type RunEpisode, type SubtitleTrack,
} from '../api';
import { label, subtitleStatusLabels } from '../constants';
import { EpisodePostEditor } from './EpisodePostEditor';

/**
 * ⑦ 每集预览卡内折叠的「字幕与台词核对」。
 *
 * 单一选集：只服务于父级选中的这一集。生成读对白事实、批准与烧录都走既有
 * 唯一执行者（compile / approve / post-contract），应用后立即刷新预览集。
 */
export function EpisodeSubtitleFold({ projectId, episode, runId, runEpisode, assembly, onMessage, onApplied }: {
  projectId: string;
  episode: DirectorEpisodeOverview;
  runId: string;
  runEpisode: RunEpisode;
  assembly?: DeliveryOrderProgress | null;
  onMessage: (text: string, kind: 'success' | 'error') => void;
  onApplied: () => void;
}) {
  const [tracks, setTracks] = useState<SubtitleTrack[]>([]);
  const [busy, setBusy] = useState(false);
  const [fontSize, setFontSize] = useState(40);
  const [problem, setProblem] = useState('');
  const [compiledRevision, setCompiledRevision] = useState<number | null>(null);

  const load = () => {
    listSubtitleTracks(projectId)
      .then((rows) => setTracks(rows.filter((row) => !row.episode_id || row.episode_id === episode.episode_id)))
      .catch(() => undefined);
    getSubtitleStyle(projectId).then((style) => setFontSize(style.font_size)).catch(() => undefined);
  };
  const latest = tracks[0] ?? null;

  // 折叠态也显示当前字幕版本，展开时再核对明细。
  useEffect(() => {
    setTracks([]);
    setProblem('');
    setCompiledRevision(null);
    load();
  }, [projectId, episode.episode_id]); // eslint-disable-line react-hooks/exhaustive-deps

  function reportError(exc: unknown) {
    const text = exc instanceof Error ? exc.message : String(exc);
    setProblem(text);
    onMessage(text, 'error');
  }

  async function saveFont() {
    setBusy(true);
    setProblem('');
    try {
      const style = await putSubtitleStyle(projectId, fontSize);
      setFontSize(style.font_size);
      onMessage(`字幕字号已设为 ${style.font_size}；点下方「按新合同重新装配」即按新字号烧录。`, 'success');
      onApplied();
    } catch (exc) {
      reportError(exc);
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    setBusy(true);
    setProblem('');
    try {
      const body = await compileEpisodeSubtitles(projectId, episode.episode_id);
      setCompiledRevision(body.revision);
      onMessage(`本集字幕第 ${body.revision} 版已按${body.basis === 'actual' ? '真实成片' : '计划'}时长生成；在下方选择并应用即烧录。`, 'success');
      load();
      onApplied();
    } catch (exc) {
      reportError(exc);
    } finally {
      setBusy(false);
    }
  }

  return <details className="episode-subtitle-fold" onToggle={(event) => {
    if (event.currentTarget.open) load();
  }}>
    <summary><Captions size={12} /> 字幕与台词核对
      {latest ? ` · 第 ${latest.revision} 版 · ${label(subtitleStatusLabels, latest.status)}` : ' · 尚无字幕轨'}
    </summary>
    <div className="episode-subtitle-body">
      <label className="run-inline-field">字幕字号（12–96）
        <input type="number" min={12} max={96} value={fontSize}
          onChange={(event) => setFontSize(Number(event.target.value) || 40)} />
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => { void saveFont(); }}>保存字号</button>
      </label>
      <p className="run-hint">字号是这一集烧录的单一设置；保存后点下方「按新合同重新装配」即按新字号烧进预览。</p>
      <button type="button" className="btn-secondary" disabled={busy} onClick={() => { void generate(); }}>
        {busy ? '生成中…' : '按真实成片时长生成 / 更新字幕轨'}
      </button>
      {problem && <p className="form-error">{problem}</p>}
      {latest && <div className="subtitle-cue-preview" aria-label="字幕核对">
        {latest.cues.map((cue) => <div key={cue.index}>
          <code>{cue.start_s.toFixed(1)}–{cue.end_s.toFixed(1)}s</code> {cue.text}
        </div>)}
      </div>}
      <EpisodePostEditor projectId={projectId} runId={runId} episode={runEpisode}
        tracks={tracks} suggestedRevision={compiledRevision} assembly={assembly} onTracksRefresh={load}
        onDone={(text, kind) => {
          onMessage(text, kind);
          if (kind === 'success') { load(); onApplied(); }
        }} />
    </div>
  </details>;
}

export default EpisodeSubtitleFold;
