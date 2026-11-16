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
import { Check, Film, ListChecks, Play, RefreshCw, ShieldCheck, Sparkles, TriangleAlert, Upload } from 'lucide-react';
import { RequiredActionNotice } from './RequiredActionNotice';
import type { ErrorActionTarget } from '../errorActions';
import {
  ApiError,
  api,
  compileEpisodeSubtitles,
  confirmEpisodeShots,
  confirmStoryboardBook,
  controlDirectorStoryboardTask,
  editEpisodeShotRow,
  fetchDirectorStoryboardState,
  fetchEpisodeShots,
  fetchEpisodeSubtitles,
  generateStoryboardBook,
  deriveShotMaterials,
  queueDirectorStoryboard,
  reconcileFilmTask,
  resendUnknownFilmTask,
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
  type ShotSubtitleLine,
  type StoryboardBook,
} from '../api';
import { useRailPresent, useStageRail } from './StageRail';
import type { RailAction } from './ActionRail';
import { MaterialPromptModal, ShotAssetRequestModal, ShotPromptModal, ShotSubtitleModal } from './ShotEditors';
import { MaterialGenerationProgress } from './MaterialGenerationProgress';

const FILM_TYPES = [
  { id: 'live_action', label: '真人实拍' },
  { id: 'animation', label: '动画' },
];

/** 对白密度是建议值：它决定分镜书里多少节拍带台词，不是渲染门槛。 */
const DIALOGUE_LEVELS = [
  { id: 'high', label: '多对白', ratio: 70 },
  { id: 'balanced', label: '平衡', ratio: 45 },
  { id: 'low', label: '少对白', ratio: 20 },
] as const;

/** 旁白只出声音、不出画面；官方 H3 提示词会明确声明这一点。 */
const NARRATION_LEVELS = [
  { id: 'none', label: '无旁白' },
  { id: 'low', label: '少旁白' },
  { id: 'high', label: '多旁白' },
] as const;

type DialogueLevelId = (typeof DIALOGUE_LEVELS)[number]['id'];
type NarrationLevelId = (typeof NARRATION_LEVELS)[number]['id'];

type StyleCatalogEntry = { id: string; name_zh: string; summary_zh: string; version: string };

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

/* ============================ ② 分镜书 ============================ */

export function StoryboardBookPanel({ projectId, currentStyle, onMessage, onConfirmed, onGoStage }: {
  projectId: string;
  currentStyle?: { version: number } | null;
  onMessage?: (message: string) => void;
  onConfirmed?: () => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const [catalog, setCatalog] = useState<StyleCatalogEntry[]>([]);
  const [profileId, setProfileId] = useState('');
  const [filmType, setFilmType] = useState('live_action');
  const [episodeCount, setEpisodeCount] = useState(2);
  const [targetSeconds, setTargetSeconds] = useState(60);
  const [maxShotSeconds, setMaxShotSeconds] = useState(15);
  const [dialogueLevel, setDialogueLevel] = useState<DialogueLevelId>('balanced');
  const [narrationLevel, setNarrationLevel] = useState<NarrationLevelId>('none');
  const [book, setBook] = useState<StoryboardBook | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  /** 最近一次失败：必做块据此给出原因与可点的下一步。 */
  const [failure, setFailure] = useState<unknown>(null);
  const [confirmedPlan, setConfirmedPlan] = useState<{ version: number } | null>(null);
  /** 比已确认版本更新的一版草稿：需要重新生成预览并确认。 */
  const [pendingDraft, setPendingDraft] = useState<number | null>(null);
  /** 计划时长超出目标容差窗：服务端具名拒绝后，由用户显式接受或调整目标/分集。 */
  const [planDeviation, setPlanDeviation] = useState<string | null>(null);

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
    setBusy(true); setProblem(''); setFailure(null);
    try {
      const body = await generateStoryboardBook(projectId, {
        episode_count: episodeCount, target_seconds: targetSeconds, tolerance_seconds: 30,
      });
      setBook(body);
      // 新生成的一版尚未确认：旧的确认态不能继续挡住确认按钮。
      setConfirmedPlan(null);
      setPendingDraft(null);
      onMessage?.('分镜书已生成；确认后即冻结分集与风格。');
    } catch (error) { setFailure(error); setProblem(describeError(error)); }
    finally { setBusy(false); }
  }

  async function confirm(acknowledge = false) {
    if (!book) return;
    setBusy(true); setProblem('');
    try {
      await confirmStoryboardBook(projectId, {
        plan_version: book.plan_version,
        expected_sha256: book.plan_sha256,
        style: {
          profile_id: profileId,
          film_type: filmType,
          dialogue_ratio_percent: DIALOGUE_LEVELS.find((row) => row.id === dialogueLevel)?.ratio ?? 45,
          narration_level: narrationLevel,
          max_shot_seconds: maxShotSeconds,
        },
        acknowledge_deviation: acknowledge,
      });
      setConfirmedPlan({ version: book.plan_version });
      setPendingDraft(null);
      setPlanDeviation(null);
      onMessage?.('分镜书已确认：分集计划与导演风格已一起定稿。');
      onConfirmed?.();
    } catch (error) {
      if (error instanceof ApiError && error.code === 'EPISODE_PLAN_DURATION_DEVIATION') {
        setPlanDeviation(error.message);
      }
      setProblem(describeError(error));
    }
    finally { setBusy(false); }
  }

  const railPresent = useRailPresent();
  const primary: RailAction | null = !confirmedPlan
    ? (book
        ? (planDeviation
            ? { label: '接受偏差并确认分镜书', onClick: () => void confirm(true), loading: busy, icon: <Check size={14} /> }
            : { label: '确认分镜书（分集 + 风格）', onClick: () => void confirm(), loading: busy, icon: <Check size={14} /> })
        : { label: '生成分镜书', onClick: () => void generate(), loading: busy, icon: <Sparkles size={14} /> })
    : null;
  useStageRail('storyboard-book', { priority: 1, primary },
    [book, busy, confirmedPlan, profileId, filmType, dialogueLevel, narrationLevel,
      episodeCount, targetSeconds, maxShotSeconds, planDeviation]);

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
      <label>单镜秒数上限
        <input type="number" min={4} max={15} step={0.5} value={maxShotSeconds}
          title="第③步镜头拆解里每个渲染镜头的时长上限；H3 单镜最短 4 秒，故可调范围 4–15 秒"
          onChange={(event) => setMaxShotSeconds(Math.min(15, Math.max(4, Number(event.target.value) || 15)))} />
      </label>
      <button type="button" className="btn-secondary" disabled={busy || !profileId} onClick={() => void generate()}>
        <Sparkles size={13} /> 生成分镜书
      </button>
    </div>

    <div className="book-tone">
      <div className="tone-group" role="group" aria-label="对白设定">
        <span>对白设定</span>
        {DIALOGUE_LEVELS.map((row) => <button key={row.id} type="button"
          className={dialogueLevel === row.id ? 'active' : ''} aria-pressed={dialogueLevel === row.id}
          onClick={() => setDialogueLevel(row.id)}>{row.label}</button>)}
      </div>
      <div className="tone-group" role="group" aria-label="旁白设定">
        <span>旁白设定</span>
        {NARRATION_LEVELS.map((row) => <button key={row.id} type="button"
          className={narrationLevel === row.id ? 'active' : ''} aria-pressed={narrationLevel === row.id}
          onClick={() => setNarrationLevel(row.id)}>{row.label}</button>)}
      </div>
      <p className="run-hint">
        这两项会随分镜书一起冻结进导演风格：对白决定多少节拍带台词；旁白决定是否允许画外旁白。
        旁白只出声、不出画面，不需要人物参考图。
      </p>
    </div>

    {problem && <p role="alert" className="form-error">{problem}</p>}
    {failure != null && <RequiredActionNotice error={failure} onGoStage={onGoStage} />}
    {planDeviation && !confirmedPlan && <div className="notice" role="alert">
      <p>{planDeviation} 可调整目标时长/分集后重新生成，或显式接受当前偏差继续。</p>
      {!railPresent && <button type="button" className="btn-secondary" disabled={busy}
        onClick={() => void confirm(true)}>接受偏差并确认分镜书</button>}
    </div>}

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

/** 分镜链的五个工种在页面上的人话名（与专家面板同词）。 */
const CREW_ROLE_LABELS: Record<string, string> = {
  scriptwriter: '编剧', director: '导演', cinematographer: '摄影',
  continuity_supervisor: '场记', editor: '剪辑', editor_edit_intent: '剪辑',
  director_arbitration: '导演裁定',
  // 五个工种都成功、落镜闸门拒绝时的归属：它不是一个模型工种。
  landing: '落镜',
};

/** 一次拒绝在页面上的人话：LLM_FORMAT 的截断证据单独说，常见策略拒绝码给一句话。 */
const REFUSAL_LABELS: Record<string, string> = {
  UNAPPROVED_ASSET_OR_UNKNOWN_BEAT: '镜头引用了未批准的主素材，或剧本里不存在的情节拍',
  CHARACTER_MASTER_BINDING_REQUIRED: '人物还没有绑定已确认的主素材',
  VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE: '画面描述里混入了中文，需要按提示词语言重写',
  DIALOGUE_NOT_IN_USER_SCRIPT: '对白不是剧本原文，需要按原文重写',
};

function refusalReason(rejection: { code?: string; rejection_reason_code?: string;
                                    provider_finish_reason?: string | null;
                                    external_outcome?: string | null;
                                    network_timeout_s?: number | null }): string {
  const code = rejection.rejection_reason_code ?? rejection.code ?? '';
  if (code === 'LLM_FORMAT') {
    return rejection.provider_finish_reason === 'length'
      ? '模型输出被截断，格式解析失败（LLM_FORMAT）'
      : '模型返回的格式无法解析（LLM_FORMAT）';
  }
  // 超时/流中断且结果未知：用记录自带的死线事实说清「超过 N 秒无回应」，
  // 而不是笼统的「连不上远端模型」（连接其实已建立，是模型迟迟不应答）。
  if (['LLM_TIMEOUT', 'LLM_CONNECTION', 'LLM_SERVER'].includes(code)
      && rejection.external_outcome === 'UNKNOWN'
      && typeof rejection.network_timeout_s === 'number' && rejection.network_timeout_s > 0) {
    return `模型超过 ${Math.round(rejection.network_timeout_s)} 秒无回应，结果未知（${code}）`;
  }
  const label = REFUSAL_LABELS[code];
  return label ? `${label}（${code}）` : (code || '分镜被拒绝');
}

export function EpisodeStoryboardPanel({ projectId, onMessage, onJourneyRefresh, onGoStage }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onJourneyRefresh?: () => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const [state, setState] = useState<EpisodeStoryboardState | null>(null);
  const [tables, setTables] = useState<Record<string, {
    shots: EpisodeShotRow[]; total: number;
    stale?: boolean; confirmedVersion?: number | null; confirmedCount?: number;
  }>>({});
  const [editing, setEditing] = useState<{ shotId: string; duration: string } | null>(null);
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  /** 最近一次失败：必做块据此给出原因与可点的下一步。 */
  const [failure, setFailure] = useState<unknown>(null);
  const [confirmedEpisodes, setConfirmedEpisodes] = useState<Record<string, number>>({});
  const [promptTarget, setPromptTarget] = useState<EpisodeShotRow | null>(null);
  const [assetTarget, setAssetTarget] = useState<EpisodeShotRow | null>(null);
  const [subtitleTarget, setSubtitleTarget] = useState<{
    shot: EpisodeShotRow; episodeId: string; lines: ShotSubtitleLine[];
  } | null>(null);
  const [subtitleResult, setSubtitleResult] = useState('');
  /** 落镜总时长超出目标容差窗：服务端具名拒绝后，由用户显式接受偏差或改时长。 */
  const [durationDeviation, setDurationDeviation] = useState<{ episodeId: string; text: string } | null>(null);
  const submitting = useRef(false);

  const load = useCallback(async () => {
    const rows = await fetchDirectorStoryboardState(projectId);
    setState(rows);
    if (rows.episodes.length) {
      const next: Record<string, { shots: EpisodeShotRow[]; total: number;
        stale?: boolean; confirmedVersion?: number | null; confirmedCount?: number }> = {};
      await Promise.all(rows.episodes.map(async (episode) => {
        try {
          const table = await fetchEpisodeShots(projectId, episode.episode_id);
          next[episode.episode_id] = {
            shots: table.shots, total: table.total_editorial_seconds,
            stale: table.confirmation_stale, confirmedVersion: table.confirmed_version,
            confirmedCount: table.confirmed_shot_ids?.length,
          };
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
    setBusy(key); setProblem(''); setFailure(null);
    try {
      await call();
      await load();
      if (success) onMessage?.(success);
    } catch (error) {
      setFailure(error);
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
  const confirmEpisode = (episodeId: string, acknowledge = false) => act(`confirm-${episodeId}`, async () => {
    try {
      await confirmEpisodeShots(projectId, episodeId, acknowledge);
      setDurationDeviation(null);
      onJourneyRefresh?.();
    } catch (error) {
      if (error instanceof ApiError && error.code === 'EPISODE_DURATION_OUT_OF_TARGET') {
        setDurationDeviation({ episodeId, text: error.message });
      }
      throw error;
    }
  }, `第 ${episodeId} 集镜头已落定。`);
  const retryEpisode = (episodeId: string) => {
    if (!state?.state_version) return;
    const version = state.state_version;
    act(`retry-${episodeId}`, async () => {
      await retryEpisodeStoryboard(projectId, episodeId, version);
    }, `第 ${episodeId} 集已重新排队。`);
  };
  /** 结果未知的拒绝没有直接重试权：核对 →（如仍未知）显式授权一次新请求 → 该集重试。 */
  const reconcileAndRetryEpisode = (episodeId: string, taskId: string | null, taskVersion: number) => {
    if (!taskId || !state?.state_version) return;
    const version = state.state_version;
    act(`reconcile-${episodeId}`, async () => {
      const checked = await reconcileFilmTask(projectId, taskId, taskVersion);
      if (checked.outcome === 'UNKNOWN_REQUIRES_NEW_ATTEMPT') {
        await resendUnknownFilmTask(projectId, taskId, checked.state_version);
      }
      await retryEpisodeStoryboard(projectId, episodeId, version);
    }, '已核对上次请求并授权重发，该集已重新排队。');
  };
  const saveDuration = (shot: EpisodeShotRow) => act(`edit-${shot.shot_id}`, async () => {
    const value = Number(editing?.duration);
    if (!Number.isFinite(value)) return;
    await editEpisodeShotRow(projectId, shot.shot_id, {
      expected_revision: shot.revision, duration_target_s: value,
    });
    setEditing(null);
  });
  /** 衔接由用户确认：接拍延续上一镜末帧（连贯），切开为独立镜头。后端早已支持
   *  continuity_mode 修订，这里把确认入口暴露到界面，不再只读。 */
  const toggleContinuity = (shot: EpisodeShotRow) => act(`continuity-${shot.shot_id}`, async () => {
    await editEpisodeShotRow(projectId, shot.shot_id, {
      expected_revision: shot.revision,
      continuity_mode: shot.continuity_mode === 'last_frame' ? 'cut' : 'last_frame',
    });
  }, shot.continuity_mode === 'last_frame' ? '已改为切开（独立镜头）。' : '已改为接拍（延续上一镜末帧）。');

  /** 字幕按集读取一次，找到这一镜现有的行；保存后由子面板回调刷新。 */
  const openSubtitle = async (episodeId: string, shot: EpisodeShotRow) => {
    setBusy(`subtitle-${shot.shot_id}`); setProblem(''); setSubtitleResult('');
    try {
      const body = await fetchEpisodeSubtitles(projectId, episodeId);
      const row = body.shots.find((item) => item.shot_id === shot.shot_id);
      setSubtitleTarget({ shot, episodeId, lines: row?.lines ?? [] });
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  };

  const compileSubtitles = (episodeId: string) => act(`subtitles-${episodeId}`, async () => {
    const body = await compileEpisodeSubtitles(projectId, episodeId);
    setSubtitleResult(
      `已生成字幕第 ${body.revision} 版（${body.cue_count} 行，`
      + `${body.basis === 'actual' ? '按真实成片时长' : '按计划时长'}）。`
      + (body.basis === 'planned' ? ' 出片后建议再生成一次以对齐真实时长。' : ''));
    onMessage?.(`本集字幕轨第 ${body.revision} 版已生成；到后期批准并合成。`);
  }, '本集字幕轨已生成。');

  const episodes = state?.episodes ?? [];
  const railPresent = useRailPresent();
  const readyCount = episodes.filter((row) => confirmedEpisodes[row.episode_id]).length;
  // 操作栏必须名实一致：全部集都已确认且没有待重新确认的新拆镜时，没有「未确认
  // 的场次」可再跑，主操作应是去旅程的下一步（拍摄素材准备），而不是挂一个空跑
  // 的再跑按钮，也不能跳过素材准备直接去导演包。
  const allConfirmed = episodes.length > 0 && readyCount === episodes.length
    && !episodes.some((row) => tables[row.episode_id]?.stale);
  const primary: RailAction | null = running
    ? null
    : allConfirmed
      ? { label: '去素材准备', onClick: () => onGoStage?.('assets-stage'), icon: <Film size={14} /> }
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
  }, [state, busy, running, tables, confirmedEpisodes, allConfirmed]);

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
        onClick={allConfirmed ? () => onGoStage?.('assets-stage') : generate}>
        <Play size={13} /> {running ? '分镜生成中…' : allConfirmed ? '去素材准备' : (state?.task_id ? '再跑一遍（未确认的场次）' : '生成分镜')}
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
    {failure != null && <RequiredActionNotice error={failure} onGoStage={onGoStage} />}
    {subtitleResult && <p className="run-hint ok-hint"><Check size={12} /> {subtitleResult}</p>}

    {episodes.map((episode) => {
      const table = tables[episode.episode_id];
      const confirmedVersion = confirmedEpisodes[episode.episode_id];
      const applied = episode.status === 'APPLIED';
      const blocked = episode.status === 'BLOCKED';
      return <details key={episode.episode_id} className="stage-dock episode-dock" open={!confirmedVersion || !!table?.stale}>
        <summary className="produce-focus-summary">
          <span className="focus-summary-label">
            {confirmedVersion
              ? table?.stale
                ? <><RefreshCw size={13} /> 第 {episode.order ?? episode.episode_id} 集 · 最新拆镜 {table.shots.length} 镜 · 待重新确认</>
                : <><Check size={13} className="stage-done-mark" /> 第 {episode.order ?? episode.episode_id} 集 · 已确认（v{confirmedVersion}，{table?.shots.length ?? 0} 镜）</>
              : blocked
                ? <><TriangleAlert size={13} /> 第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}</>
                : <>第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}{table?.shots.length ? `（${table.shots.length} 镜）` : ''}</>}
          </span>
        </summary>
        <div className="episode-body">
          {blocked && (() => {
            const rejection = episode.blocker?.rejections?.[0];
            const blockedUnit = episode.units.find((unit) => unit.index === episode.blocker?.unit_index)
              ?? episode.units.find((unit) => unit.state === 'BLOCKED');
            const order = blockedUnit?.section_order ?? (blockedUnit ? blockedUnit.index + 1 : null);
            const rawTitle = blockedUnit?.section_title?.trim() ?? '';
            // 标题常自带「第N场」前缀（来自剧本小标题），避免「第 2 场：第2场…」重复。
            const title = order === null ? rawTitle
              : rawTitle.replace(new RegExp(`^第\\s*${order}\\s*场[：:·,，、]?`), '');
            const scene = blockedUnit
              ? `第 ${order} 场${title ? `：${title}` : ''}`
              : '这一集';
            const roleName = CREW_ROLE_LABELS[rejection?.role ?? ''] ?? rejection?.role ?? '分镜工种';
            const uncertain = rejection?.external_outcome === 'UNKNOWN';
            // 落镜拒绝不是一次模型调用：重试只复用已完成的五个工种结果重新落镜。
            const landingOnly = rejection?.role === 'landing';
            const failedTaskId = rejection?.task_id ?? episode.blocker?.failed_task_id ?? null;
            const failedTaskVersion = rejection?.state_version ?? 0;
            return <div className="blocked-callout">
              <TriangleAlert size={14} />
              <span>
                卡在{scene}：{roleName} · {refusalReason(rejection ?? {})}。
                {uncertain
                  ? ' 这次请求的结果不确定，不会自动重发；核对并确认后才会发起一次新请求。'
                  : landingOnly
                    ? ' 不会自动重发；重试只复用这一场已完成的工种结果重新落镜，已落镜的场次保留。'
                    : ' 不会自动重发；重试只从这一场重新发起一次模型请求，已落镜的场次保留。'}
              </span>
              {uncertain
                ? <button type="button" className="btn-secondary" disabled={busy !== ''}
                    onClick={() => reconcileAndRetryEpisode(episode.episode_id, failedTaskId, failedTaskVersion)}>
                    <ShieldCheck size={12} /> 核对这次请求并重试
                  </button>
                : <button type="button" className="btn-secondary" disabled={busy !== ''}
                    onClick={() => retryEpisode(episode.episode_id)}>
                    <RefreshCw size={12} /> {blockedUnit ? '重试这一场' : '重试这一集'}
                  </button>}
            </div>;
          })()}
          {table && table.shots.length > 0 && <>
            <div className="shot-table-wrap">
            <table className="shot-table shot-table-fixed">
              <colgroup>
                <col style={{ width: '4%' }} /><col style={{ width: '6%' }} />
                <col style={{ width: '24%' }} /><col style={{ width: '17%' }} />
                <col style={{ width: '7%' }} /><col style={{ width: '6%' }} />
                <col style={{ width: '10%' }} /><col style={{ width: '8%' }} />
                <col style={{ width: '18%' }} />
              </colgroup>
              <thead><tr>
                <th>#</th><th>场次</th><th>画面动作</th><th>对白</th>
                <th>成片秒</th><th>衔接</th><th>提示词</th><th>字幕</th><th>操作</th>
              </tr></thead>
              <tbody>
                {table.shots.map((shot) => <tr key={shot.shot_id} className="shot-table-row">
                  <td>{shot.position}</td>
                  <td>{shot.section_id ?? '—'}</td>
                  <td className="shot-beat">
                    {shot.story_beat.split('\n')[0].slice(0, 56)}
                    {shot.internal_cuts.length > 0 && <em className="shot-cut-note">
                      镜内切点 {shot.internal_cuts.map((cut) => `${cut.start_s}s`).join(' / ')}
                    </em>}
                  </td>
                  <td>{shot.dialogue.length
                    ? shot.dialogue.map((line) => `${line.speaker_id}：「${line.text}」`).join(' ')
                    : <span className="muted">无对白</span>}</td>
                  <td title={`H3：${shot.h3_segment_count} 段 · ${shot.h3_generation_seconds}s`}>
                    {editing?.shotId === shot.shot_id
                      ? <input type="number" min={4} max={15} step={0.5}
                          value={editing.duration}
                          onChange={(event) => setEditing({ shotId: shot.shot_id, duration: event.target.value })} />
                      : shot.duration_target_s}
                  </td>
                  <td>
                    <button type="button" className="btn-text" disabled={busy !== '' || (!!confirmedVersion && !table?.stale)}
                      title="点击切换衔接：接拍延续上一镜末帧（画面连贯），切开为独立镜头"
                      onClick={() => void toggleContinuity(shot)}>
                      {shot.continuity_mode === 'last_frame' ? '接拍' : '切开'}
                    </button>
                  </td>
                  <td>
                    <span className={`head-chip ${shot.prompt?.status === 'frozen' ? 'ok'
                      : shot.prompt?.status === 'inactive' ? 'warn' : ''}`}
                      title={shot.prompt?.reason ?? undefined}>
                      {shot.prompt?.status === 'frozen' ? `人工冻结 v${shot.prompt.revision}`
                        : shot.prompt?.status === 'inactive' ? '冻结版暂不生效'
                          : '编译器'}
                    </span>
                  </td>
                  <td>
                    {shot.subtitle && shot.subtitle.line_count > 0
                      ? <span className={`head-chip ${shot.subtitle.status === 'FROZEN' ? 'ok' : ''}`}>
                          {shot.subtitle.line_count} 行
                        </span>
                      : <span className="muted">—</span>}
                  </td>
                  <td className="shot-row-actions">
                    {editing?.shotId === shot.shot_id
                      ? <button type="button" className="btn-text" disabled={busy !== ''}
                          onClick={() => void saveDuration(shot)}>保存</button>
                      : <button type="button" className="btn-text" disabled={busy !== '' || (!!confirmedVersion && !table?.stale)}
                          onClick={() => setEditing({ shotId: shot.shot_id, duration: String(shot.duration_target_s) })}>
                          改时长</button>}
                    <button type="button" className="btn-text" disabled={busy !== ''}
                      onClick={() => setPromptTarget(shot)}>提示词</button>
                    <button type="button" className="btn-text"
                      disabled={busy !== '' || busy === `subtitle-${shot.shot_id}`}
                      onClick={() => void openSubtitle(episode.episode_id, shot)}>字幕</button>
                    <button type="button" className="btn-text" disabled={busy !== ''}
                      onClick={() => setAssetTarget(shot)}>+素材</button>
                  </td>
                </tr>)}
              </tbody>
            </table>
            </div>
            <p className="run-hint">
              本集合计成片时长约 {Math.round(table.total)} 秒；每镜由 {table.shots.length} 个 4–15 秒的 H3 生成片段承载。
              提示词与字幕都可以逐镜人工接管；规范检查只给建议，不阻断。
            </p>
            <div className="episode-subtitle-bar">
              <button type="button" className="btn-secondary" disabled={busy !== ''}
                onClick={() => void compileSubtitles(episode.episode_id)}>
                <ListChecks size={12} /> 生成 / 更新本集字幕轨
              </button>
              <span className="run-hint">
                只使用已冻结的镜头字幕；未出片前按计划时长排布，出片后可再生成一次对齐真实时长。
                本项目只走 Ref2VA：没有参考素材的镜头不会被提交渲染。
              </span>
            </div>
          </>}
          {applied && !confirmedVersion && <button type="button" className="btn-primary"
            disabled={busy !== '' || !table?.shots.length}
            onClick={() => confirmEpisode(episode.episode_id)}>
            <Check size={13} /> 确认本集镜头（{table?.shots.length ?? 0} 镜）
          </button>}
          {confirmedVersion && table?.stale && <div className="notice" role="alert">
            <p>最新拆镜落了 {table.shots.length} 镜，与已确认的 v{confirmedVersion}（{table.confirmedCount ?? 0} 镜）不同。
              重新确认本集才会用新镜头替换旧确认；旧镜头与已出片产物作为历史保留，不会被改写。</p>
            <button type="button" className="btn-primary" disabled={busy !== '' || !table.shots.length}
              onClick={() => confirmEpisode(episode.episode_id)}>
              <Check size={13} /> 重新确认本集（采用最新 {table.shots.length} 镜）
            </button>
          </div>}
          {durationDeviation?.episodeId === episode.episode_id && <div className="notice" role="alert">
            <p>{durationDeviation.text} 可用每镜「改时长」把总时长调进目标窗，或显式接受当前偏差。</p>
            <button type="button" className="btn-secondary" disabled={busy !== ''}
              onClick={() => confirmEpisode(episode.episode_id, true)}>接受偏差并确认本集</button>
          </div>}
          {!table?.shots.length && !blocked && <p className="empty-inline">
            这一集的分镜还没落镜；{running ? '后台正在生成。' : '点击上方按钮生成。'}
          </p>}
        </div>
      </details>;
    })}

    {promptTarget && <ShotPromptModal
      projectId={projectId}
      shot={{ shot_id: promptTarget.shot_id, name: promptTarget.name,
        revision: promptTarget.revision, duration_target_s: promptTarget.duration_target_s }}
      onClose={() => setPromptTarget(null)}
      onChanged={(message) => { onMessage?.(message); void load().catch(() => undefined); }}
    />}
    {assetTarget && <ShotAssetRequestModal
      projectId={projectId}
      shot={{ shot_id: assetTarget.shot_id, name: assetTarget.name }}
      onClose={() => setAssetTarget(null)}
      onChanged={(message) => {
        onMessage?.(message);
        onJourneyRefresh?.();
        void load().catch(() => undefined);
      }}
    />}
    {subtitleTarget && <ShotSubtitleModal
      projectId={projectId}
      shot={{ shot_id: subtitleTarget.shot.shot_id, name: subtitleTarget.shot.name,
        duration_target_s: subtitleTarget.shot.duration_target_s }}
      initialLines={subtitleTarget.lines}
      onClose={() => setSubtitleTarget(null)}
      onChanged={(message) => { onMessage?.(message); void load().catch(() => undefined); }}
    />}
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

export function ShotMaterialsPanel({ projectId, onMessage, onGoStage }: {
  projectId: string;
  onMessage?: (message: string) => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const [view, setView] = useState<DirectorMaterialsView | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  /** 最近一次失败：必做块据此给出原因与可点的下一步。 */
  const [failure, setFailure] = useState<unknown>(null);
  const [result, setResult] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [promptTarget, setPromptTarget] = useState<DirectorMaterialRequirement | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [batchTarget, setBatchTarget] = useState<Decision>('generate');
  const [progressKey, setProgressKey] = useState(0);
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
    setBusy('derive'); setProblem(''); setResult(null); setFailure(null);
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
      setFailure(error);
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
      setProgressKey((key) => key + 1);
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
      setProgressKey((key) => key + 1);
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
  const selectedIds = requirements.filter((row) => selected[row.requirement_id]).map((row) => row.requirement_id);
  const allSelected = requirements.length > 0 && selectedIds.length === requirements.length;
  /** 批量设定处理方式：不可用的选项按真实事实降级（缺主素材不能复用、必需项不能跳过），
   *  与逐项下拉用的是同一份 decisions，提交时仍由 commit() 唯一送出。 */
  function applyBatch(target: Decision, ids: string[]) {
    if (busy !== '' || ids.length === 0) return;
    setDecisions((current) => {
      const next = { ...current };
      for (const row of requirements) {
        if (!ids.includes(row.requirement_id)) continue;
        next[row.requirement_id] = target === 'reuse_master'
          ? (row.matched_master ? 'reuse_master' : 'generate')
          : target === 'skip'
            ? (row.critical ? 'generate' : 'skip')
            : target;
      }
      return next;
    });
    onMessage?.(`已把 ${ids.length} 项素材的处理方式批量更新，确认后一次送出。`);
  }
  function toggleAll(checked: boolean) {
    setSelected(Object.fromEntries(requirements.map((row) => [row.requirement_id, checked])));
  }
  const pendingChoice = requirements.some((row) => !decisions[row.requirement_id]);
  const missingCritical = requirements.filter(
    (row) => row.critical && !row.matched_master).length;
  const primary: RailAction | null = requirements.length
    ? { label: busy === 'commit' ? '正在提交…' : '确认决策并开始准备素材',
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
    {failure != null && <RequiredActionNotice error={failure} onGoStage={onGoStage} />}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}</p>}

    <MaterialGenerationProgress projectId={projectId} refreshKey={progressKey}
      onPromoted={() => { void load(); }} />

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
      <div className="material-batch-bar">
        <label className="material-select-cell" title="全选/取消全选">
          <input type="checkbox" checked={allSelected} disabled={busy !== ''}
            aria-label="全选素材需求"
            onChange={(event) => toggleAll(event.target.checked)} />
        </label>
        <span className="mgp-counts">已选 {selectedIds.length} 项</span>
        <select value={batchTarget} disabled={busy !== ''} aria-label="批量处理方式"
          onChange={(event) => setBatchTarget(event.target.value as Decision)}>
          <option value="generate">AI 制作</option>
          <option value="reuse_master">复用现有主素材（缺的自动改 AI 制作）</option>
          <option value="skip">跳过（必需项自动改 AI 制作）</option>
        </select>
        <button type="button" className="btn-secondary" disabled={busy !== '' || selectedIds.length === 0}
          onClick={() => applyBatch(batchTarget, selectedIds)}>应用到所选</button>
        <button type="button" className="btn-secondary" disabled={busy !== ''}
          title="一键把全部素材设为 AI 制作"
          onClick={() => { toggleAll(true); applyBatch('generate', requirements.map((row) => row.requirement_id)); }}>
          全部 AI 制作</button>
        <button type="button" className="btn-text" disabled={busy !== '' || selectedIds.length === 0}
          onClick={() => setSelected({})}>清空选择</button>
      </div>
      <table className="shot-table material-table">
        <thead><tr>
          <th className="material-select-cell">选</th><th>预览</th><th>条目</th><th>说明</th><th>用于</th><th>当前主素材</th><th>你的决定</th><th>单项操作</th>
        </tr></thead>
        <tbody>
          {requirements.map((row) => {
            const rowBusy = busy === `row-${row.requirement_id}`;
            return <tr key={row.requirement_id}>
            <td className="material-select-cell">
              <input type="checkbox" checked={!!selected[row.requirement_id]} disabled={busy !== ''}
                aria-label={`选择 ${row.name ?? row.entity_id}`}
                onChange={(event) => setSelected((current) => (
                  { ...current, [row.requirement_id]: event.target.checked }))} />
            </td>
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
              <span className="material-flags">
                {row.user_specified && <span className="head-chip">用户指定</span>}
                {row.manual_prompt && <span className="head-chip ok">提示词已冻结</span>}
              </span>
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
                aria-label={`处理方式：${row.name ?? row.entity_id}`}
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
                title="直接编辑这一项出图用的提示词；保存并冻结后，下一次出图原样使用"
                onClick={() => setPromptTarget(row)}>人工修订</button>
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

    {promptTarget && <MaterialPromptModal
      projectId={projectId}
      requirement={{
        requirement_id: promptTarget.requirement_id,
        name: promptTarget.name ?? promptTarget.entity_id ?? '这一项素材',
        family: promptTarget.family,
        description: promptTarget.description,
      }}
      onClose={() => setPromptTarget(null)}
      onChanged={(message) => { onMessage?.(message); void load(); }}
    />}
  </section>;
}
