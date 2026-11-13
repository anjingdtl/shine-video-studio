/**
 * 导演模式收束 ②③④ 的三个面板（docs/SHINE_DIRECTOR_JOURNEY_STREAMLINE_PLAN_20261113.md）。
 *
 * ② StoryboardBookPanel：四个输入（导演风格、影片类型、集数、每集秒数）→ 分镜书预览
 *    → 一次确认（分集计划 + 风格冻结成对提交）。
 * ③ EpisodeStoryboardPanel：一次生成分镜 → 按集可编辑镜头表 → 按集一次落镜；被阻塞的
 *    一集可单独重试，其余集不受影响。
 * ④ ShotMaterialsPanel：从已确认镜头生成去重清单 → 每项选「复用 / AI 制作 / 跳过」→
 *    一次「确认并送素材中心」（复用既有确认与生成接口，只有明确选中的才送出）。
 *
 * 三个面板都只做显示与意图提交：执行全部由常驻 Worker 完成，页面刷新或切换后从
 * 已持久化状态恢复。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Film, ListChecks, Play, RefreshCw, Sparkles, TriangleAlert, Upload } from 'lucide-react';
import {
  ApiError,
  api,
  confirmEpisodeShots,
  confirmStoryboardBook,
  controlDirectorStoryboardTask,
  editEpisodeShotRow,
  fetchDirectorStoryboardState,
  fetchEpisodeShots,
  generateStoryboardBook,
  deriveShotMaterials,
  queueDirectorStoryboard,
  retryEpisodeStoryboard,
  API,
  fetchDirectorRequirements,
  commitDirectorMaterials,
  uploadProjectAsset,
  approveProjectAsset,
  lockProjectAsset,
  type DirectorMaterialRequirement,
  type DirectorMaterialsView,
  type EpisodeShotRow,
  type EpisodeStoryboardState,
  type StoryboardBook,
} from '../api';
import { useRailPresent, useStageRail } from './StageRail';
import type { RailAction } from './ActionRail';

const FILM_TYPES = [
  { id: 'live_action', label: '真人实拍' },
  { id: 'animation', label: '动画' },
];

type StyleCatalogEntry = { id: string; name_zh: string; summary_zh: string; version: string };

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

/* ============================ ② 分镜书 ============================ */

export function StoryboardBookPanel({ projectId, currentStyle, onMessage, onConfirmed }: {
  projectId: string;
  currentStyle?: { version: number } | null;
  onMessage?: (message: string) => void;
  onConfirmed?: () => void;
}) {
  const [catalog, setCatalog] = useState<StyleCatalogEntry[]>([]);
  const [profileId, setProfileId] = useState('');
  const [filmType, setFilmType] = useState('live_action');
  const [episodeCount, setEpisodeCount] = useState(2);
  const [targetSeconds, setTargetSeconds] = useState(60);
  const [book, setBook] = useState<StoryboardBook | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [confirmedPlan, setConfirmedPlan] = useState<{ version: number } | null>(null);
  /** 比已确认版本更新的一版草稿：需要重新生成预览并确认。 */
  const [pendingDraft, setPendingDraft] = useState<number | null>(null);

  useEffect(() => {
    api<StyleCatalogEntry[]>('/director-styles')
      .then((rows) => {
        setCatalog(rows);
        if (rows.length && !profileId) setProfileId(rows[0].id);
      })
      .catch(() => setCatalog([]));
    api<Array<{ version: number; status: string }>>(`/projects/${projectId}/episode-plans`)
      .then((rows) => {
        const confirmed = rows.find((row) => row.status === 'CONFIRMED');
        // 重新生成过分镜书时，最新一版是 DRAFT：它必须重新确认，
        // 不能让旧的「已确认」挡住新版本的确认入口。
        const latestDraft = rows
          .filter((row) => row.status === 'DRAFT')
          .sort((a, b) => b.version - a.version)[0];
        if (latestDraft && (!confirmed || latestDraft.version > confirmed.version)) {
          setConfirmedPlan(null);
          setPendingDraft(latestDraft.version);
          return;
        }
        if (confirmed) setConfirmedPlan({ version: confirmed.version });
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function generate() {
    setBusy(true); setProblem('');
    try {
      const body = await generateStoryboardBook(projectId, {
        episode_count: episodeCount, target_seconds: targetSeconds, tolerance_seconds: 30,
      });
      setBook(body);
      // 新生成的一版尚未确认：旧的确认态不能继续挡住确认按钮。
      setConfirmedPlan(null);
      setPendingDraft(null);
      onMessage?.('分镜书已生成；确认后即冻结分集与风格。');
    } catch (error) { setProblem(describeError(error)); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!book) return;
    setBusy(true); setProblem('');
    try {
      await confirmStoryboardBook(projectId, {
        plan_version: book.plan_version,
        expected_sha256: book.plan_sha256,
        style: { profile_id: profileId, film_type: filmType },
      });
      setConfirmedPlan({ version: book.plan_version });
      setPendingDraft(null);
      onMessage?.('分镜书已确认：分集计划与导演风格已一起定稿。');
      onConfirmed?.();
    } catch (error) { setProblem(describeError(error)); }
    finally { setBusy(false); }
  }

  const railPresent = useRailPresent();
  const primary: RailAction | null = !confirmedPlan
    ? (book
        ? { label: '确认分镜书（分集 + 风格）', onClick: () => void confirm(), loading: busy, icon: <Check size={14} /> }
        : { label: '生成分镜书', onClick: () => void generate(), loading: busy, icon: <Sparkles size={14} /> })
    : null;
  useStageRail('storyboard-book', { priority: 1, primary }, [book, busy, confirmedPlan, profileId, filmType]);

  return <section className="card section-card" id="storyboard-book">
    <div className="section-heading">
      <div>
        <div className="section-kicker">第 2 阶段 · 分镜书</div>
        <h2>四个决定，一次确认</h2>
        <p>导演风格、影片类型、集数和每集目标秒数。集数优先：场次只按自然边界切分，
          每集预计时长与目标的差值会如实写在预览里，不会为了凑数删场或编集。</p>
      </div>
      {confirmedPlan && <span className="head-chip ok">
        <Check size={11} /> 第 {confirmedPlan.version} 版已确认
      </span>}
      {currentStyle?.version ? <span className="head-chip">当前冻结风格第 {currentStyle.version} 版</span> : null}
    </div>

    <div className="book-settings">
      <label>导演风格
        <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
          {catalog.length === 0 && <option value="">（目录未加载）</option>}
          {catalog.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name_zh}（{entry.summary_zh.slice(0, 18)}…）
            </option>
          ))}
        </select>
      </label>
      <label>影片类型
        <select value={filmType} onChange={(event) => setFilmType(event.target.value)}>
          {FILM_TYPES.map((type) => <option key={type.id} value={type.id}>{type.label}</option>)}
        </select>
      </label>
      <label>集数
        <input type="number" min={1} max={200} value={episodeCount}
          onChange={(event) => setEpisodeCount(Math.max(1, Number(event.target.value) || 1))} />
      </label>
      <label>每集目标秒数
        <input type="number" min={8} max={3600} value={targetSeconds}
          onChange={(event) => setTargetSeconds(Math.max(8, Number(event.target.value) || 60))} />
      </label>
      <button type="button" className="btn-secondary" disabled={busy || !profileId} onClick={() => void generate()}>
        <Sparkles size={13} /> 生成分镜书
      </button>
    </div>

    {problem && <p role="alert" className="form-error">{problem}</p>}

    {book && <div className="book-preview">
      <div className="book-preview-head">
        <b>{book.preview.episode_count} 集 · 计划总时长 {Math.round(book.preview.total_planned_seconds)} 秒</b>
        {!confirmedPlan && !railPresent && <button type="button" className="btn-primary" disabled={busy} onClick={() => void confirm()}>
          <Check size={13} /> 确认分镜书（分集 + 风格）
        </button>}
      </div>
      <ol className="run-episode-plan">
        {book.preview.episodes.map((episode) => (
          <li key={episode.episode_id}>
            <span className="run-ep-no">{episode.order}</span>
            <div>
              <b>{episode.title}</b>
              <span>{episode.section_count} 场 · 计划 {Math.round(episode.planned_seconds)} 秒
                （与目标差 {episode.deviation_seconds > 0 ? '+' : ''}{Math.round(episode.deviation_seconds)} 秒）</span>
              {episode.boundary_note && <em>{episode.boundary_note}</em>}
              <details><summary>{episode.section_count} 场的节拍与对白</summary>
                {episode.sections.map((section) => <div key={section.production_section_id} className="book-section">
                  <b>{section.title} · 约 {Math.round(section.estimated_seconds)} 秒</b>
                  {section.beats.length > 0
                    ? <ul>{section.beats.slice(0, 8).map((beat, beatIndex) => (
                        <li key={beatIndex}>{beat.description || '（无描述）'}</li>))}</ul>
                    : <p className="empty-inline">这一场还没有分场剧本，节拍将来自大纲。</p>}
                  {section.dialogue.length > 0 && <p className="book-dialogue">
                    {section.dialogue.slice(0, 4).map((line) =>
                      `${line.speaker ?? '?'}：「${line.text ?? ''}」`).join('　')}
                  </p>}
                </div>)}
              </details>
            </div>
          </li>
        ))}
      </ol>
    </div>}
    {pendingDraft !== null && !book && <div className="blocked-callout">
      <TriangleAlert size={14} />
      <span>有一版未确认的分镜书（第 {pendingDraft} 版）。点上方「生成分镜书」重新预览，再确认。</span>
    </div>}
    {!book && !confirmedPlan && <p className="empty-inline">
      先确认大纲（第 1 阶段），再在这里生成分镜书。
    </p>}
  </section>;
}

/* ============================ ③ 按集镜头表 ============================ */

const EPISODE_STATUS_LABELS: Record<string, string> = {
  PENDING: '待生成', IN_PROGRESS: '生成中', APPLIED: '可确认', BLOCKED: '被阻塞',
};

export function EpisodeStoryboardPanel({ projectId, onMessage, onJourneyRefresh }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onJourneyRefresh?: () => void;
}) {
  const [state, setState] = useState<EpisodeStoryboardState | null>(null);
  const [tables, setTables] = useState<Record<string, { shots: EpisodeShotRow[]; total: number }>>({});
  const [editing, setEditing] = useState<{ shotId: string; duration: string } | null>(null);
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [confirmedEpisodes, setConfirmedEpisodes] = useState<Record<string, number>>({});
  const submitting = useRef(false);

  const load = useCallback(async () => {
    const rows = await fetchDirectorStoryboardState(projectId);
    setState(rows);
    if (rows.episodes.length) {
      const next: Record<string, { shots: EpisodeShotRow[]; total: number }> = {};
      await Promise.all(rows.episodes.map(async (episode) => {
        try {
          const table = await fetchEpisodeShots(projectId, episode.episode_id);
          next[episode.episode_id] = { shots: table.shots, total: table.total_editorial_seconds };
        } catch { next[episode.episode_id] = { shots: [], total: 0 }; }
      }));
      setTables(next);
    }
    try {
      type PlanRow = { version: number; status: string; payload?: {
        episodes?: Array<{ episode_id: string; shot_ids?: string[] }> } };
      const plans = await api<PlanRow[]>(`/projects/${projectId}/episode-plans`);
      const confirmed = plans.find((row) => row.status === 'CONFIRMED');
      const map: Record<string, number> = {};
      if (confirmed?.payload?.episodes) {
        for (const episode of confirmed.payload.episodes) {
          if (episode.shot_ids?.length) map[episode.episode_id] = confirmed.version;
        }
      }
      setConfirmedEpisodes(map);
    } catch { /* keep prior */ }
  }, [projectId]);

  useEffect(() => { void load().catch(() => undefined); }, [load]);
  const running = state?.status === 'QUEUED' || state?.status === 'RUNNING';
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 4000);
    return () => window.clearInterval(timer);
  }, [load, running]);

  async function act(key: string, call: () => Promise<void>, success?: string) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(key); setProblem('');
    try {
      await call();
      await load();
      if (success) onMessage?.(success);
    } catch (error) {
      setProblem(describeError(error));
      await load().catch(() => undefined);
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  const generate = () => act('generate', async () => {
    await queueDirectorStoryboard(projectId);
    onMessage?.('分镜任务已排队；关掉页面也会由常驻 Worker 继续逐场拆镜。');
  });
  const confirmEpisode = (episodeId: string) => act(`confirm-${episodeId}`, async () => {
    await confirmEpisodeShots(projectId, episodeId);
    onJourneyRefresh?.();
  }, `第 ${episodeId} 集镜头已落定。`);
  const retryEpisode = (episodeId: string) => {
    if (!state?.state_version) return;
    const version = state.state_version;
    act(`retry-${episodeId}`, async () => {
      await retryEpisodeStoryboard(projectId, episodeId, version);
    }, `第 ${episodeId} 集已重新排队。`);
  };
  const saveDuration = (shot: EpisodeShotRow) => act(`edit-${shot.shot_id}`, async () => {
    const value = Number(editing?.duration);
    if (!Number.isFinite(value)) return;
    await editEpisodeShotRow(projectId, shot.shot_id, {
      expected_revision: shot.revision, duration_target_s: value,
    });
    setEditing(null);
  });

  const episodes = state?.episodes ?? [];
  const railPresent = useRailPresent();
  const readyCount = episodes.filter((row) => confirmedEpisodes[row.episode_id]).length;
  const primary: RailAction | null = running
    ? null
    : { label: state?.task_id ? '再跑一遍（未确认的场次）' : '生成分镜', onClick: generate,
        loading: busy === 'generate', icon: <Sparkles size={14} /> };
  useStageRail('episode-storyboard', {
    priority: 1,
    primary,
    running: running ? {
      label: '分镜生成中', done: episodes.reduce((sum, row) =>
        sum + row.units.filter((unit) => unit.state === 'APPLIED').length, 0),
      total: episodes.reduce((sum, row) => sum + row.units.length, 0) || undefined,
    } : null,
  }, [state, busy, running]);

  return <section className="card section-card" id="episode-storyboard">
    <div className="section-heading">
      <div>
        <div className="section-kicker">第 3 阶段 · 镜头拆解</div>
        <h2>一次生成，按集落镜</h2>
        <p>后台逐场调用五个分镜工种并自动落镜；每一集独立推进——
          {episodes.length > 0
            ? `${readyCount}/${episodes.length} 集已确认。`
            : '先生成，再按集浏览、修改并确认镜头表。'}</p>
      </div>
      {episodes.length > 0 && <button type="button" className="btn-secondary" onClick={() => void load().catch(() => undefined)}>
        <RefreshCw size={12} /> 刷新
      </button>}
    </div>

    <div className="episode-actions">
      {!railPresent && <button type="button" className="btn-primary" disabled={busy !== '' || running}
        onClick={generate}>
        <Play size={13} /> {running ? '分镜生成中…' : (state?.task_id ? '再跑一遍（未确认的场次）' : '生成分镜')}
      </button>}
      {state?.status === 'NEEDS_REVIEW' && <span className="run-hint">
        <TriangleAlert size={12} /> 有集被阻塞；每一集可单独重试，其余集不受影响。
      </span>}
      {state?.task_id && running && (
        <button type="button" className="btn-secondary" disabled={busy !== ''}
          onClick={() => {
            const id = state.task_id; const version = state.state_version ?? 0;
            if (!id) return;
            void act('pause', async () => {
              await controlDirectorStoryboardTask(projectId, id, 'pause', version);
            }, '后台分镜已暂停；已落镜的集不受影响。');
          }}>
          暂停后台分镜
        </button>)}
      {state?.task_id && state.status === 'PAUSED' && (
        <button type="button" className="btn-secondary" disabled={busy !== ''}
          onClick={() => {
            const id = state.task_id; const version = state.state_version ?? 0;
            if (!id) return;
            void act('resume', async () => {
              await controlDirectorStoryboardTask(projectId, id, 'resume', version);
            }, '后台分镜已继续。');
          }}>
          继续后台分镜
        </button>)}
      {state?.status === 'PAUSED' && <span className="run-hint">
        后台分镜已暂停；可以继续，或先处理已完成的集。
      </span>}
    </div>
    {problem && <p role="alert" className="form-error">{problem}</p>}

    {episodes.map((episode) => {
      const table = tables[episode.episode_id];
      const confirmedVersion = confirmedEpisodes[episode.episode_id];
      const applied = episode.status === 'APPLIED';
      const blocked = episode.status === 'BLOCKED';
      return <details key={episode.episode_id} className="stage-dock episode-dock" open={!confirmedVersion}>
        <summary className="produce-focus-summary">
          <span className="focus-summary-label">
            {confirmedVersion
              ? <><Check size={13} className="stage-done-mark" /> 第 {episode.order ?? episode.episode_id} 集 · 已确认（v{confirmedVersion}，{table?.shots.length ?? 0} 镜）</>
              : blocked
                ? <><TriangleAlert size={13} /> 第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}</>
                : <>第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}{table?.shots.length ? `（${table.shots.length} 镜）` : ''}</>}
          </span>
        </summary>
        <div className="episode-body">
          {blocked && <div className="blocked-callout">
            <TriangleAlert size={14} />
            <span>
              {episode.blocker?.rejections?.[0]
                ? `最近一次失败：${episode.blocker.rejections[0].role} · ${episode.blocker.rejections[0].rejection_reason_code
                   ?? episode.blocker.rejections[0].code}`
                : '这一集的分镜被拒绝。'}
              {episode.blocker?.rejections?.[0]?.external_outcome === 'UNKNOWN'
                ? ' 结果不确定：先在专业面板核对，不会自动重发。' : ''}
            </span>
            {episode.blocker?.rejections?.[0]?.external_outcome !== 'UNKNOWN' && (
              <button type="button" className="btn-secondary" disabled={busy !== ''}
                onClick={() => retryEpisode(episode.episode_id)}>
                <RefreshCw size={12} /> 重试这一集
              </button>)}
          </div>}
          {table && table.shots.length > 0 && <>
            <div className="shot-table-wrap">
            <table className="shot-table shot-table-fixed">
              <colgroup>
                <col style={{ width: '4%' }} /><col style={{ width: '7%' }} />
                <col style={{ width: '27%' }} /><col style={{ width: '22%' }} />
                <col style={{ width: '8%' }} /><col style={{ width: '7%' }} />
                <col style={{ width: '7%' }} /><col style={{ width: '13%' }} />
                <col style={{ width: '5%' }} />
              </colgroup>
              <thead><tr>
                <th>#</th><th>场次</th><th>画面动作</th><th>对白</th>
                <th>成片秒</th><th>衔接</th><th>切点</th><th>H3 生成</th><th></th>
              </tr></thead>
              <tbody>
                {table.shots.map((shot) => <tr key={shot.shot_id} className="shot-table-row">
                  <td>{shot.position}</td>
                  <td>{shot.section_id ?? '—'}</td>
                  <td className="shot-beat">{shot.story_beat.split('\n')[0].slice(0, 60)}</td>
                  <td>{shot.dialogue.length
                    ? shot.dialogue.map((line) => `${line.speaker_id}：「${line.text}」`).join(' ')
                    : <span className="muted">无对白</span>}</td>
                  <td>
                    {editing?.shotId === shot.shot_id
                      ? <input type="number" min={4} max={15} step={0.5}
                          value={editing.duration}
                          onChange={(event) => setEditing({ shotId: shot.shot_id, duration: event.target.value })} />
                      : shot.duration_target_s}
                  </td>
                  <td>{shot.continuity_mode === 'last_frame' ? '接拍' : '切开'}</td>
                  <td>{shot.internal_cuts.length
                    ? shot.internal_cuts.map((cut) => `${cut.start_s}s`).join(' / ')
                    : <span className="muted">—</span>}</td>
                  <td>{shot.h3_segment_count} 段 · {shot.h3_generation_seconds}s</td>
                  <td className="shot-row-actions">
                    {editing?.shotId === shot.shot_id
                      ? <button type="button" className="btn-text" disabled={busy !== ''}
                          onClick={() => void saveDuration(shot)}>保存</button>
                      : <button type="button" className="btn-text" disabled={busy !== '' || !!confirmedVersion}
                          onClick={() => setEditing({ shotId: shot.shot_id, duration: String(shot.duration_target_s) })}>
                          改</button>}
                  </td>
                </tr>)}
              </tbody>
            </table>
            </div>
            <p className="run-hint">
              本集合计成片时长约 {Math.round(table.total)} 秒；每镜由 {table.shots.length} 个 4–15 秒的 H3 生成片段承载。
            </p>
          </>}
          {applied && !confirmedVersion && <button type="button" className="btn-primary"
            disabled={busy !== '' || !table?.shots.length}
            onClick={() => confirmEpisode(episode.episode_id)}>
            <Check size={13} /> 确认本集镜头（{table?.shots.length ?? 0} 镜）
          </button>}
          {!table?.shots.length && !blocked && <p className="empty-inline">
            这一集的分镜还没落镜；{running ? '后台正在生成。' : '点击上方按钮生成。'}
          </p>}
        </div>
      </details>;
    })}
  </section>;
}

/* ============================ ④ 按镜头素材清单 ============================ */

type Decision = 'reuse_master' | 'generate' | 'upload' | 'skip';

/** 默认决策只由真实事实决定：已有主素材就复用，必需项缺就制作，可选项默认跳过。 */
function defaultDecision(row: DirectorMaterialRequirement): Decision {
  if (row.matched_master) return 'reuse_master';
  if (row.critical) return 'generate';
  return 'skip';
}

const FAMILY_LABEL: Record<string, string> = {
  character: '人物', scene: '场景', prop: '道具',
};

export function ShotMaterialsPanel({ projectId, onMessage, onOpenLibrary }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onOpenLibrary?: () => void;
}) {
  const [view, setView] = useState<DirectorMaterialsView | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const submitting = useRef(false);

  const apply = useCallback((body: DirectorMaterialsView) => {
    setView(body);
    setDecisions((current) => {
      // 已经做过的选择不会被一次刷新覆盖；新条目按其真实状态取默认值。
      const next: Record<string, Decision> = {};
      for (const row of body.requirements) {
        next[row.requirement_id] = current[row.requirement_id] ?? defaultDecision(row);
      }
      return next;
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const body = await fetchDirectorRequirements(projectId);
      apply(body);
    } catch {
      /* 读不到就保持现状：页面不因此谎称“没有需求”。 */
    } finally {
      setLoaded(true);
    }
  }, [apply, projectId]);

  useEffect(() => { void load(); }, [load]);

  async function derive() {
    if (submitting.current) return;
    submitting.current = true;
    setBusy('derive'); setProblem(''); setResult(null);
    try {
      const body = await deriveShotMaterials(projectId, null);
      apply({
        plan_revision_id: body.plan_revision_id,
        expected_snapshot: body.expected_snapshot,
        requirements: body.requirements.map((row) => ({
          ...row,
          variants: row.variants ?? [],
          cited_by_shot: row.cited_by_shot ?? false,
        })),
      });
      onMessage?.('素材清单已按已确认镜头生成；每一项都标了用在哪些镜头。');
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  async function commit() {
    if (!view?.plan_revision_id || !view.expected_snapshot || submitting.current) return;
    submitting.current = true;
    setBusy('commit'); setProblem(''); setResult(null);
    try {
      const rows = view.requirements
        .filter((row) => decisions[row.requirement_id])
        .map((row) => ({
          requirement_id: row.requirement_id,
          strategy: decisions[row.requirement_id],
          entity_id: row.entity_id ?? row.name ?? 'unknown',
          asset_id: decisions[row.requirement_id] === 'reuse_master'
            ? (row.matched_master?.asset_id ?? null) : null,
        }));
      const outcome = await commitDirectorMaterials(projectId, {
        plan_revision_id: view.plan_revision_id,
        expected_snapshot: view.expected_snapshot,
        decisions: rows,
        queue_generation: true,
      });
      const queued = outcome.batch?.item_count ?? outcome.generate_count;
      const parts = [
        outcome.replayed
          ? '这次提交之前已经生效，没有重复出图'
          : `已确认 ${rows.length} 项决策`,
        queued ? `已排队 ${queued} 项 AI 候选` : '没有需要 AI 制作的项',
      ];
      setResult(parts.join('；') + '。候选生成后由你在这里选定并采用，再回绑镜头。');
      if (outcome.batch_error) setProblem(outcome.batch_error.message);
      onMessage?.('素材决策已确认。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
      // 断连或超时：先读台账，确认是否其实已经生效，再让用户决定。
      await load().catch(() => undefined);
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  /** 单条需求立即执行一个决策（重新生成 / 复用新素材），不影响其他行。 */
  async function commitOne(row: DirectorMaterialRequirement, strategy: Decision, assetId?: string | null) {
    if (!view?.plan_revision_id || !view.expected_snapshot) return;
    setBusy(`row-${row.requirement_id}`); setProblem(''); setResult(null);
    try {
      await commitDirectorMaterials(projectId, {
        plan_revision_id: view.plan_revision_id,
        expected_snapshot: view.expected_snapshot,
        decisions: [{
          requirement_id: row.requirement_id,
          strategy,
          entity_id: row.entity_id ?? row.name ?? 'unknown',
          asset_id: assetId ?? null,
        }],
        queue_generation: strategy === 'generate',
      });
      setResult(strategy === 'generate'
        ? `「${row.name ?? row.entity_id}」已重新排队制作候选；出图后由你选定并采用。`
        : `「${row.name ?? row.entity_id}」已改用你上传的主素材。`);
      onMessage?.('这一项素材已更新。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  /** 用户自行上传：上传 → 确认 → 锁定 → 直接把这一项回绑到新素材。 */
  async function uploadFor(row: DirectorMaterialRequirement, file: File) {
    setBusy(`row-${row.requirement_id}`); setProblem(''); setResult(null);
    try {
      const asset = await uploadProjectAsset(projectId, file, {
        role: row.family, entityId: row.entity_id ?? row.name ?? undefined,
      });
      await approveProjectAsset(asset.id);
      await lockProjectAsset(asset.id);
      await commitOne(row, 'reuse_master', asset.id);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  const requirements = view?.requirements ?? [];
  const pendingChoice = requirements.some((row) => !decisions[row.requirement_id]);
  const missingCritical = requirements.filter(
    (row) => row.critical && !row.matched_master).length;
  const primary: RailAction | null = requirements.length
    ? { label: busy === 'commit' ? '正在提交…' : '确认决策并送素材中心',
        onClick: () => void commit(), loading: busy === 'commit',
        disabled: busy !== '' || pendingChoice,
        icon: <ListChecks size={14} />,
        disabledReason: pendingChoice ? '每一项都要选一个处理方式（可选项可选「跳过」）' : undefined }
    : { label: '按已确认镜头生成素材清单', onClick: () => void derive(),
        loading: busy === 'derive', icon: <Film size={14} /> };
  useStageRail('shot-materials', { priority: 1, primary }, [view, decisions, busy]);

  return <section className="card section-card" id="shot-materials">
    <div className="section-heading">
      <div>
        <div className="section-kicker">第 4 阶段 · 拍摄素材准备</div>
        <h2>镜头要什么，就准备什么</h2>
        <p>清单直接来自你确认过的镜头：人物、场景、道具各自的真实引用与使用镜头。
          已有主素材自动标为复用；缺的<b>必需项</b>只暂停用到它的镜头，可选项可以跳过。</p>
      </div>
      <button type="button" className="btn-secondary" disabled={busy !== ''}
        onClick={() => void derive()}>
        <RefreshCw size={12} /> {busy === 'derive' ? '正在生成…' : '重新按镜头生成清单'}
      </button>
    </div>

    {view?.capacity && <p className="run-hint">
      本机方案：{view.capacity.profile_name ?? '未登记'} · 人物槽位 {view.capacity.subjects} ·
      参考图槽位 {view.capacity.pictures}
      {view.capacity.audios ? ` · 音频槽位 ${view.capacity.audios}` : ''}
      {view.capacity.subjects === 0 ? '（没有人物槽位：人物主素材不计为必需）' : ''}
    </p>}

    {problem && <p role="alert" className="form-error">{problem}</p>}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}
      {onOpenLibrary && <button type="button" className="btn-text" onClick={onOpenLibrary}>去素材中心</button>}
    </p>}

    {loaded && !requirements.length && <p className="empty-inline">
      还没有按镜头的素材清单。先确认至少一集的镜头，再生成清单。
    </p>}

    {requirements.length > 0 && <>
      <div className="material-summary">
        <span>{requirements.length} 项需求</span>
        <span className={missingCritical ? 'warn' : 'ok'}>
          必需缺项 {missingCritical}
        </span>
        <span>{requirements.filter((row) => row.matched_master).length} 项可复用</span>
      </div>
      <table className="shot-table material-table">
        <thead><tr>
          <th>预览</th><th>条目</th><th>说明</th><th>用于</th><th>当前主素材</th><th>你的决定</th><th>单项操作</th>
        </tr></thead>
        <tbody>
          {requirements.map((row) => {
            const rowBusy = busy === `row-${row.requirement_id}`;
            return <tr key={row.requirement_id}>
            <td className="material-thumb-cell">
              {row.matched_master
                ? <img className="material-thumb" src={`${API}/assets/${row.matched_master.asset_id}/thumbnail`}
                    alt={row.name ?? row.entity_id ?? '素材预览'} loading="lazy" />
                : <span className={`material-thumb placeholder ${row.critical ? 'warn' : ''}`}>
                    {row.critical ? '缺' : '可选'}
                  </span>}
            </td>
            <td><b>{row.name ?? row.entity_id}</b>
              <span className="muted">（{FAMILY_LABEL[row.family] ?? row.family}
                {row.critical ? ' · 必需' : ' · 可选'}）</span>
              {row.variants.length > 0 && <em className="material-variants">
                变体：{row.variants.slice(0, 3).join('、')}</em>}
            </td>
            <td className="shot-beat">{(row.description || '—').slice(0, 80)}</td>
            <td>{row.used_shot_count} 镜
              {row.used_episodes.length ? ` · ${row.used_episodes.join('、')}` : ''}</td>
            <td>{row.matched_master
              ? <span className="head-chip ok">已匹配，可复用</span>
              : <span className={`head-chip ${row.critical ? 'warn' : ''}`}>
                  {row.critical ? '必需，缺' : '可选，缺'}
                </span>}</td>
            <td>
              <select value={decisions[row.requirement_id] ?? ''} disabled={busy !== ''}
                onChange={(event) => setDecisions((current) => (
                  { ...current, [row.requirement_id]: event.target.value as Decision }))}>
                {row.matched_master && <option value="reuse_master">复用现有主素材</option>}
                <option value="generate">AI 制作</option>
                <option value="upload">我自己上传</option>
                {!row.critical && <option value="skip">跳过（可选项，不影响生成）</option>}
                {row.critical && !row.matched_master && <option value="">请选择…</option>}
              </select>
            </td>
            <td className="material-row-actions">
              <button type="button" className="btn-text" disabled={busy !== ''}
                title="只对这一项重新排队制作候选，不影响其他素材和已通过的镜头"
                onClick={() => void commitOne(row, 'generate')}>
                <RefreshCw size={12} /> {rowBusy ? '处理中…' : '重新生成'}
              </button>
              <label className={`btn-text material-upload ${busy !== '' ? 'disabled' : ''}`}
                title="上传你已有的素材；确认并锁定后直接把这一项回绑到它">
                <Upload size={12} /> 上传
                <input type="file" accept="image/*,video/*,audio/*" hidden disabled={busy !== ''}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) void uploadFor(row, file);
                  }} />
              </label>
            </td>
          </tr>})}
        </tbody>
      </table>
    </>}
  </section>;
}
