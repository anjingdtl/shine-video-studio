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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { imageStyleInput, StyleLoraAssemblyPanel, type StyleLoraConfig } from '../shared/StyleLoraAssemblyPanel';
import { CharacterSheetToggle } from '../shared/CharacterSheetToggle';
import { Check, Film, History, ListChecks, Play, RefreshCw, ShieldCheck, Sparkles, TriangleAlert, Upload } from 'lucide-react';
import { RequiredActionNotice } from './RequiredActionNotice';
import type { ErrorActionTarget } from '../errorActions';
import {
  ApiError,
  api,
  json,
  confirmEpisodeShots,
  confirmStoryboardBook,
  controlDirectorStoryboardTask,
  editEpisodeShotRow,
  fetchDirectorStoryboardState,
  fetchEpisodeShots,
  fetchEpisodeSubtitles,
  fetchLatestStoryboardBook,
  generateStoryboardBook,
  deriveShotMaterials,
  queueDirectorStoryboard,
  reconcileFilmTask,
  resendUnknownFilmTask,
  retryEpisodeStoryboard,
  API,
  applyShotAdoption,
  fetchDirectorRequirements,
  commitDirectorMaterials,
  previewShotAdoption,
  updateMaterialPreferences,
  uploadProjectAsset,
  approveProjectAsset,
  lockProjectAsset,
  type AdoptionPreviewResponse,
  type DirectorMaterialRequirement,
  type DirectorMaterialsView,
  type EpisodeShotRow,
  type EpisodeStoryboardState,
  type ShotSubtitleLine,
  type StoryboardBook,
  type Style,
  type StyleDimension,
  type StyleVersion,
} from '../api';
import { DEFAULT_MAX_SHOT_SECONDS, MIN_SHOT_SECONDS, styleFields } from '../constants';
import { AssetCenterPicker } from '../shared/AssetCenterPicker';
import { assignLibraryVersion, searchLibraryForProject, type LibraryAsset, type LibraryVersion } from '../asset-center/api';
import {
  matchStyleOption, presetForStyle, STYLE_DIMENSION_OPTIONS, STYLE_PRESETS,
} from '../styleCatalog';
import { useRailPresent, useStageRail } from './StageRail';
import type { RailAction } from './ActionRail';
import { MaterialPromptModal, ShotAssetRequestModal, ShotPromptModal, ShotSubtitleModal } from './ShotEditors';
import { MaterialGenerationProgress } from './MaterialGenerationProgress';

const FILM_TYPES = [
  { id: 'live_action', label: '真人实拍' },
  { id: 'animation', label: '动画' },
];

/** 旁白只出声音、不出画面；官方 H3 提示词会明确声明这一点。 */
const NARRATION_LEVELS = [
  { id: 'none', label: '无旁白' },
  { id: 'low', label: '少旁白' },
  { id: 'high', label: '多旁白' },
] as const;

type NarrationLevelId = (typeof NARRATION_LEVELS)[number]['id'];

type StyleCatalogEntry = { id: string; name_zh: string; summary_zh: string; version: string };

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

/* ============================ ② 分镜书 ============================ */

type StyleStrength = 'subtle' | 'balanced' | 'strong';

export function StoryboardBookPanel({
  projectId, currentStyle, styles = [], outlineConfirmed = false,
  onApplyStyleToAllShots, onRefreshFreeze, onMessage, onConfirmed, onGoStage,
}: {
  projectId: string;
  currentStyle?: { version: number; id?: string; snapshot?: Record<string, unknown> | null } | null;
  styles?: StyleVersion[];
  outlineConfirmed?: boolean;
  onApplyStyleToAllShots?: () => void;
  onRefreshFreeze?: () => void;
  onMessage?: (message: string) => void;
  onConfirmed?: () => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const [catalog, setCatalog] = useState<StyleCatalogEntry[]>([]);
  const [profileId, setProfileId] = useState('');
  const [strength, setStrength] = useState<StyleStrength>('balanced');
  const [filmType, setFilmType] = useState('live_action');
  const [episodeCount, setEpisodeCount] = useState(2);
  const [targetSeconds, setTargetSeconds] = useState(60);
  // 新分镜书的单镜上限初始值与上限：与后端 schemas.DEFAULT_MAX_SHOT_SECONDS 同源同值（4–10 秒）。
  const [maxShotSeconds, setMaxShotSeconds] = useState(DEFAULT_MAX_SHOT_SECONDS);
  const [narrationLevel, setNarrationLevel] = useState<NarrationLevelId>('none');
  const [dimensions, setDimensions] = useState<Style>(() => ({ ...STYLE_PRESETS[0].values }));
  const [dimensionsTouched, setDimensionsTouched] = useState(false);
  const [customFields, setCustomFields] = useState<Set<StyleDimension>>(new Set());
  const [book, setBook] = useState<StoryboardBook | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  /** 最近一次失败：必做块据此给出原因与可点的下一步。 */
  const [failure, setFailure] = useState<unknown>(null);
  const [confirmedPlan, setConfirmedPlan] = useState<{ version: number } | null>(null);
  /** 已确认计划的版本与哈希：确认后改风格时经原确认入口重新冻结，无需重新生成。 */
  const [basisPlan, setBasisPlan] = useState<{ version: number; sha256: string } | null>(null);
  /** 服务端已持久化的风格决定：表单与之不同即「改了但还没冻结」。 */
  const [persistedStyle, setPersistedStyle] = useState<Record<string, unknown> | null>(null);
  const [styleLoaded, setStyleLoaded] = useState(false);
  /** 比已确认版本更新的一版草稿：需要重新生成预览并确认。 */
  const [pendingDraft, setPendingDraft] = useState<number | null>(null);
  /** 计划时长超出目标容差窗：服务端具名拒绝后，由用户显式接受或调整目标/分集。 */
  const [planDeviation, setPlanDeviation] = useState<string | null>(null);

  const loadProfileDimensions = useCallback(async (id: string, isCancelled?: () => boolean) => {
    if (!id) return;
    try {
      const detail = await api<{ profile?: { style_bible?: Partial<Style> } }>(`/director-styles/${id}`);
      if (isCancelled?.()) return;
      const bible = detail?.profile?.style_bible;
      if (!bible) return;
      setDimensions((current) => ({
        ...current,
        ...Object.fromEntries(styleFields.map(([field]) => [field, String(bible[field] ?? '')])),
      }));
      setCustomFields(new Set());
      setDimensionsTouched(false);
    } catch {
      // Minimal stubs may not mount /director-styles/{id}; backend still resolves by profileId.
    }
  }, []);

  // 表单的唯一事实是服务端已持久化的决定：最新计划（草稿优先于旧确认）的
  // 集数/秒数 + director-style 草稿（无草稿时回退冻结快照）的风格参数。
  // 挂载/换作品/冻结版本变化时读回，切页回来不再掉回硬编码默认值。
  useEffect(() => {
    let cancelled = false;
    let resolvedServerProfileId = '';
    let hasServerDimensions = false;
    const isCancelled = () => cancelled;

    const maybeSyncDefaultDimensions = () => {
      if (cancelled || hasServerDimensions) return;
      if (resolvedServerProfileId) void loadProfileDimensions(resolvedServerProfileId, isCancelled);
    };

    api<StyleCatalogEntry[]>('/director-styles')
      .then((rows) => {
        if (cancelled) return;
        setCatalog(rows);
      })
      .catch(() => setCatalog([]));
    fetchLatestStoryboardBook(projectId)
      .then((latest) => {
        if (cancelled || !latest || !latest.preview) return;
        setBook((current) => current ?? latest);
      })
      .catch(() => undefined);
    api<Array<{ version: number; status: string; sha256?: string; target_seconds?: number;
      payload?: { episodes?: unknown[]; target_seconds?: number; source?: { max_shot_seconds?: number } } }>>(
      `/projects/${projectId}/episode-plans`,
    )
      .then((rows) => {
        if (cancelled) return;
        const confirmed = rows.find((row) => row.status === 'CONFIRMED');
        if (confirmed?.sha256) setBasisPlan({ version: confirmed.version, sha256: confirmed.sha256 });
        // 重新生成过分镜书时，最新一版是 DRAFT：它必须重新确认，
        // 不能让旧的「已确认」挡住新版本的确认入口。
        const latestDraft = rows
          .filter((row) => row.status === 'DRAFT')
          .sort((a, b) => b.version - a.version)[0];
        const basis = latestDraft && (!confirmed || latestDraft.version > confirmed.version)
          ? latestDraft : confirmed;
        if (basis) {
          const count = basis.payload?.episodes?.length;
          if (count) setEpisodeCount(count);
          const seconds = basis.payload?.target_seconds ?? basis.target_seconds;
          if (seconds) setTargetSeconds(seconds);
          const planMaxShot = basis.payload?.source?.max_shot_seconds;
          if (typeof planMaxShot === 'number' && Number.isFinite(planMaxShot)) {
            setMaxShotSeconds(Math.min(DEFAULT_MAX_SHOT_SECONDS, Math.max(MIN_SHOT_SECONDS, planMaxShot)));
          }
        }
        if (latestDraft && (!confirmed || latestDraft.version > confirmed.version)) {
          setConfirmedPlan(null);
          setPendingDraft(latestDraft.version);
          return;
        }
        if (confirmed) setConfirmedPlan({ version: confirmed.version });
      })
      .catch(() => undefined);
    api<{ style_draft?: Record<string, unknown> | null;
      draft_source?: { profile_id?: string; strength?: StyleStrength } | null;
      frozen_source?: { profile_id?: string; strength?: StyleStrength } | null }>(`/projects/${projectId}/director-style`)
      .then((view) => {
        if (cancelled) return;
        const source = view.draft_source ?? view.frozen_source;
        if (source?.profile_id) {
          resolvedServerProfileId = source.profile_id;
          setProfileId(source.profile_id);
        }
        if (source?.strength && ['subtle', 'balanced', 'strong'].includes(source.strength)) {
          setStrength(source.strength);
        }
        const facts = view.style_draft ?? currentStyle?.snapshot ?? null;
        setPersistedStyle(facts
          ? { ...facts, profile_id: source?.profile_id ?? '', strength: source?.strength ?? 'balanced' }
          : null);
        setStyleLoaded(true);
        if (!facts) {
          maybeSyncDefaultDimensions();
          return;
        }
        const loadedAllSeven = styleFields.every(([field]) => String(facts[field] ?? '').trim().length > 0);
        if (loadedAllSeven) {
          hasServerDimensions = true;
          setDimensions((current) => ({
            ...current,
            ...Object.fromEntries(styleFields.map(([field]) => [field, String(facts[field] ?? '')])),
          }));
          setDimensionsTouched(false);
        } else {
          maybeSyncDefaultDimensions();
        }
        const filmTypeValue = facts.film_type;
        if (filmTypeValue === 'live_action' || filmTypeValue === 'animation') setFilmType(filmTypeValue);
        const narration = facts.narration_level;
        if (narration === 'none' || narration === 'low' || narration === 'high') setNarrationLevel(narration);
        const maxSeconds = Number(facts.max_shot_seconds);
        if (Number.isFinite(maxSeconds) && maxSeconds > 0) {
          setMaxShotSeconds(Math.min(DEFAULT_MAX_SHOT_SECONDS, Math.max(MIN_SHOT_SECONDS, maxSeconds)));
        }
      })
      .catch(() => setStyleLoaded(true));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentStyle?.id]);

  const allSevenFilled = styleFields.every(([field]) => String(dimensions[field] ?? '').trim().length > 0);
  const matchedPreset = presetForStyle(dimensions);

  function handleProfileChange(nextId: string) {
    setProfileId(nextId);
    if (!nextId) {
      setDimensions({ ...STYLE_PRESETS[0].values });
      setCustomFields(new Set());
      setDimensionsTouched(false);
      return;
    }
    void loadProfileDimensions(nextId);
  }

  function applyPreset(presetId: string) {
    const preset = STYLE_PRESETS.find((row) => row.id === presetId);
    if (!preset) return;
    setDimensions((value) => ({ ...value, ...preset.values }));
    setCustomFields(new Set());
    setDimensionsTouched(true);
    onMessage?.(`已套用预设「${preset.label}」；点击确认分镜书后一并冻结。`);
  }

  function chooseOption(field: StyleDimension, optionId: string) {
    if (optionId === 'custom') {
      setCustomFields((current) => new Set(current).add(field));
      setDimensionsTouched(true);
      return;
    }
    const option = STYLE_DIMENSION_OPTIONS[field].find((row) => row.id === optionId);
    if (!option) return;
    setDimensions((value) => ({ ...value, [field]: option.text }));
    setCustomFields((current) => { const next = new Set(current); next.delete(field); return next; });
    setDimensionsTouched(true);
  }

  async function generate() {
    setBusy(true); setProblem(''); setFailure(null);
    try {
      // Scale episode duration tolerance with targetSeconds so short targets (e.g. 20-30s)
      // never swallow multiple extra scenes up to +30s.
      const toleranceSeconds = Math.min(30, Math.max(4, Math.round(targetSeconds * 0.25)));
      const body = await generateStoryboardBook(projectId, {
        episode_count: episodeCount,
        target_seconds: targetSeconds,
        tolerance_seconds: toleranceSeconds,
        max_shot_seconds: maxShotSeconds,
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
    // 确认后改风格：复用同一确认入口与已确认计划，不另开链路。
    const plan = book
      ? { version: book.plan_version, sha256: book.plan_sha256 }
      : basisPlan;
    if (!plan || !styleLoaded) return;
    if (dimensionsTouched && !allSevenFilled) {
      setProblem('请先把「逐项微调七个风格维度（高级）」里的七项内容填完整后再确认。');
      return;
    }
    setBusy(true); setProblem('');
    const sevenDimensions = allSevenFilled
      ? {
          photography_language: dimensions.photography_language.trim(),
          shot_blocking: dimensions.shot_blocking.trim(),
          color_palette: dimensions.color_palette.trim(),
          lighting_tone: dimensions.lighting_tone.trim(),
          lens_texture: dimensions.lens_texture.trim(),
          editing_rhythm: dimensions.editing_rhythm.trim(),
          sound_performance: dimensions.sound_performance.trim(),
        }
      : undefined;
    try {
      await confirmStoryboardBook(projectId, {
        plan_version: plan.version,
        expected_sha256: plan.sha256,
        style: {
          profile_id: profileId,
          strength,
          film_type: filmType,
          narration_level: narrationLevel,
          max_shot_seconds: maxShotSeconds,
          ...(sevenDimensions ? { dimensions: sevenDimensions } : {}),
        },
        acknowledge_deviation: acknowledge,
      });
      setConfirmedPlan({ version: plan.version });
      setBasisPlan(plan);
      setPendingDraft(null);
      setPlanDeviation(null);
      setDimensionsTouched(false);
      setPersistedStyle({
        ...(sevenDimensions ?? {}),
        profile_id: profileId,
        strength,
        film_type: filmType,
        narration_level: narrationLevel,
        max_shot_seconds: maxShotSeconds,
      });
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

  // 表单与已持久化决定的差异：只有服务端写过的字段才参与比较，
  // 旧快照缺某个设置时不把默认值当成「用户改了」。
  const persistedHasSeven = Boolean(
    persistedStyle && styleFields.every(([field]) => String(persistedStyle[field] ?? '').trim().length > 0),
  );
  const styleDirty = Boolean(confirmedPlan && persistedStyle && (
    (typeof persistedStyle.profile_id === 'string' && profileId !== persistedStyle.profile_id)
    || (typeof persistedStyle.strength === 'string' && persistedStyle.strength !== ''
      && strength !== persistedStyle.strength)
    || (persistedStyle.film_type === 'live_action' || persistedStyle.film_type === 'animation'
      ? filmType !== persistedStyle.film_type : false)
    || (persistedStyle.narration_level === 'none' || persistedStyle.narration_level === 'low'
      || persistedStyle.narration_level === 'high'
      ? narrationLevel !== persistedStyle.narration_level : false)
    || (typeof persistedStyle.max_shot_seconds === 'number'
      && maxShotSeconds !== Math.min(DEFAULT_MAX_SHOT_SECONDS, Math.max(MIN_SHOT_SECONDS, persistedStyle.max_shot_seconds)))
    || (persistedHasSeven && allSevenFilled
      && styleFields.some(([field]) => dimensions[field].trim() !== String(persistedStyle[field] ?? '').trim()))
    || (dimensionsTouched && !allSevenFilled)
  ));

  const railPresent = useRailPresent();
  const primary: RailAction | null = !confirmedPlan
    ? (book
        ? (planDeviation
            ? { label: '接受偏差并确认分镜书', onClick: () => void confirm(true), loading: busy, icon: <Check size={14} /> }
            : { label: '确认分镜书（分集 + 风格）', onClick: () => void confirm(), loading: busy, icon: <Check size={14} /> })
        : { label: '生成分镜书', onClick: () => void generate(), loading: busy, icon: <Sparkles size={14} /> })
    : (styleDirty && basisPlan
        ? { label: planDeviation ? '接受偏差并重新确认（冻结新风格）' : '重新确认分镜书（冻结新风格）',
            onClick: () => void confirm(Boolean(planDeviation)), loading: busy, icon: <Check size={14} /> }
        : null);
  useStageRail('storyboard-book', { priority: 1, primary },
    [book, busy, confirmedPlan, profileId, strength, filmType, narrationLevel,
      episodeCount, targetSeconds, maxShotSeconds, dimensions, dimensionsTouched, planDeviation,
      styleDirty, basisPlan, styleLoaded]);

  return <section className="card section-card" id="storyboard-book">
    <div className="section-heading">
      <div>
        <div className="section-kicker">第 2 阶段 · 分镜书</div>
        <h2>设定范围与风格，一次确认</h2>
        <p>导演风格、影片类型、集数、每集目标秒数与单镜上限共用这一套决定。场次按自然边界
          整场装入并严格控制在设定集数内；超出部分不排入本次计划，可在下方预览看到当前覆盖到大纲的哪一章以及后续还需拍多少集。</p>
      </div>
      {confirmedPlan && <span className="head-chip ok">
        <Check size={11} /> 第 {confirmedPlan.version} 版已确认
      </span>}
      {currentStyle?.version ? <span className="head-chip">当前冻结风格第 {currentStyle.version} 版</span> : null}
    </div>

    <div className="book-settings">
      <label>导演风格
        <select value={profileId} onChange={(event) => handleProfileChange(event.target.value)}>
          <option value="">电影专业 Skills 自动（默认，不绑名导模板）</option>
          {catalog.map((entry) => (
            <option key={entry.id} value={entry.id}>
              历史模板：{entry.name_zh}（{entry.summary_zh.slice(0, 14)}…）
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
        <input type="number" min={MIN_SHOT_SECONDS} max={DEFAULT_MAX_SHOT_SECONDS} step={0.5} value={maxShotSeconds}
          title="第③步镜头拆解里每个渲染镜头的时长上限；本地安全生产可调范围 4–10 秒"
          onChange={(event) => setMaxShotSeconds(Math.min(DEFAULT_MAX_SHOT_SECONDS, Math.max(MIN_SHOT_SECONDS,
            Number(event.target.value) || DEFAULT_MAX_SHOT_SECONDS)))} />
      </label>
      <button type="button" className="btn-secondary" disabled={busy} onClick={() => void generate()}>
        <Sparkles size={13} /> 生成分镜书
      </button>
    </div>

    <div className="book-tone">
      <div className="tone-group" role="group" aria-label="旁白设定">
        <span>旁白设定</span>
        {NARRATION_LEVELS.map((row) => <button key={row.id} type="button"
          className={narrationLevel === row.id ? 'active' : ''} aria-pressed={narrationLevel === row.id}
          onClick={() => setNarrationLevel(row.id)}>{row.label}</button>)}
      </div>
      <p className="run-hint">
        旁白设定会随分镜书一起冻结进导演风格，决定是否允许画外旁白；旁白只出声、不出画面，不需要人物参考图。
      </p>
    </div>

    <details className="stage-dock book-style-advanced" id="style-detail">
      <summary className="produce-focus-summary">
        <span className="focus-summary-label">逐项微调七个风格维度（高级）</span>
      </summary>
      <div className="book-style-advanced-body">
        <p className="run-hint">
          上方选择的导演风格已直接带入这七个维度；这里调整的任何内容都会在点击「确认分镜书」或「重新确认分镜书」时一并冻结，不需要另外采用到草稿或再走第二处冻结。
        </p>
        <div className="style-preset-row">
          <label className="style-preset-label" htmlFor="book-style-strength">风格强度
            <select
              id="book-style-strength"
              value={strength}
              onChange={(event) => setStrength(event.target.value as StyleStrength)}
              disabled={busy}
            >
              <option value="subtle">轻</option>
              <option value="balanced">均衡</option>
              <option value="strong">鲜明</option>
            </select>
          </label>
          <label className="style-preset-label" htmlFor="book-style-preset">整套预设
            <select
              id="book-style-preset"
              value={matchedPreset?.id ?? ''}
              onChange={(event) => applyPreset(event.target.value)}
              disabled={busy}
            >
              <option value="">跟随当前导演风格 / 自定义</option>
              {STYLE_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
            </select>
          </label>
        </div>
        <div className="style-grid">
          {styleFields.map(([field, label, hint], index) => {
            const matched = matchStyleOption(field, dimensions[field]);
            const customActive = customFields.has(field) || (!matched && !!String(dimensions[field] ?? '').trim());
            const selectValue = matched?.id ?? (String(dimensions[field] ?? '').trim() || customActive ? 'custom' : '');
            return (
              <div className="style-field" key={field}>
                <span><i>0{index + 1}</i>{label}</span>
                <select
                  aria-label={label}
                  value={selectValue}
                  onChange={(event) => chooseOption(field, event.target.value)}
                  disabled={busy}
                >
                  <option value="">请选择…</option>
                  {STYLE_DIMENSION_OPTIONS[field].map((option) => (
                    <option key={option.id} value={option.id}>{option.label}</option>
                  ))}
                  <option value="custom">自定义…</option>
                </select>
                {customActive && (
                  <textarea
                    aria-label={`${label}（自定义）`}
                    value={dimensions[field]}
                    placeholder={hint}
                    rows={2}
                    disabled={busy}
                    onChange={(event) => {
                      setCustomFields((current) => new Set(current).add(field));
                      setDimensions((value) => ({ ...value, [field]: event.target.value }));
                      setDimensionsTouched(true);
                    }}
                  />
                )}
                {!customActive && !String(dimensions[field] ?? '').trim() && (
                  <small className="style-field-hint">{hint}</small>
                )}
                {!customActive && String(dimensions[field] ?? '').trim() && (
                  <small className="style-field-hint">{dimensions[field]}</small>
                )}
              </div>
            );
          })}
        </div>
        {currentStyle && (onApplyStyleToAllShots || onRefreshFreeze) && (
          <div className="style-freeze-tools">
            <div>
              <b>已冻结风格维护</b>
              <span>旧风格、旧镜头和旧成片保留为历史；有镜头正在拍摄时会拒绝。</span>
            </div>
            {onApplyStyleToAllShots && (
              <button type="button" className="btn-secondary" disabled={busy} onClick={onApplyStyleToAllShots}>
                应用当前风格到全部镜头
              </button>
            )}
            {onRefreshFreeze && (
              <button type="button" className="btn-secondary" disabled={busy} onClick={onRefreshFreeze}>
                解锁并应用新冻结
              </button>
            )}
          </div>
        )}
        {styles.length > 0 && (
          <div className="version-row">
            <span className="version-label"><History size={12} /> 版本记录</span>
            {styles.map((version) => (
              <span className={`version-chip ${version.id === currentStyle?.id ? 'current' : ''}`} key={version.id}>
                V{version.version} · {version.sha256.slice(0, 8)}
              </span>
            ))}
          </div>
        )}
      </div>
    </details>

    {styleDirty && <div className="notice" role="status">
      <p>风格选择已修改。素材出图与下游只读<b>已冻结</b>的风格：点「重新确认分镜书」后新风格才会冻结生效。</p>
      {!railPresent && <button type="button" className="btn-secondary" disabled={busy || !styleLoaded}
        onClick={() => void confirm(Boolean(planDeviation))}>
        <Check size={13} /> 重新确认分镜书（冻结新风格）
      </button>}
    </div>}
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {failure != null && <RequiredActionNotice error={failure} onGoStage={onGoStage} />}
    {planDeviation && (!confirmedPlan || styleDirty) && <div className="notice" role="alert">
      <p>{planDeviation} 可调整目标时长/分集后重新生成，或显式接受当前偏差继续。</p>
      {!railPresent && <button type="button" className="btn-secondary" disabled={busy}
        onClick={() => void confirm(true)}>接受偏差并确认分镜书</button>}
    </div>}

    {book && <div className="book-preview">
      <div className="book-preview-head">
        <b>
          {book.preview.episode_count} 集 · 每集目标 {book.preview.target_seconds} 秒 · 单镜上限{' '}
          {book.preview.max_shot_seconds ?? maxShotSeconds} 秒 · 计划总时长 {Math.round(book.preview.total_planned_seconds)} 秒
        </b>
        {!confirmedPlan && !railPresent && <button type="button" className="btn-primary" disabled={busy} onClick={() => void confirm()}>
          <Check size={13} /> 确认分镜书（分集 + 风格）
        </button>}
      </div>
      {book.preview.covered_scope && <p className="run-hint">
        <Check size={12} /> {book.preview.covered_scope.note}
      </p>}
      {book.preview.out_of_scope && <div className="notice" role="status">
        <TriangleAlert size={13} />
        <p>{book.preview.out_of_scope.note}</p>
      </div>}
      {book.preview.scope_warning && <div className="notice" role="status">
        <TriangleAlert size={13} />
        <p>{book.preview.scope_warning}</p>
      </div>}
      <ol className="run-episode-plan">
        {book.preview.episodes.map((episode) => (
          <li key={episode.episode_id}>
            <span className="run-ep-no">{episode.order}</span>
            <div>
              <b>{episode.title}</b>
              <span>{episode.section_count} 场 · 计划 {Math.round(episode.planned_seconds)} 秒</span>
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
      {outlineConfirmed
        ? '大纲已确认。设定好集数、每集秒数与风格后，点击「生成分镜书」。'
        : '先确认大纲（第 1 阶段），再在这里生成分镜书。'}
    </p>}
  </section>;
}

/* ============================ ③ 按集镜头表 ============================ */

const EPISODE_STATUS_LABELS: Record<string, string> = {
  PENDING: '待生成', IN_PROGRESS: '生成中', APPLIED: '可确认', BLOCKED: '被阻塞',
};

/** 分镜链的各工种在页面上的人话名（与专家面板同词）。
 *  V4 在剧组之前多了两步，都由编剧一路模型应答：集纲架构、戏剧改编。 */
const CREW_ROLE_LABELS: Record<string, string> = {
  series_architecture: '集纲架构', drama_scene: '戏剧改编', scene_board: '场景导演',
  scriptwriter: '编剧', director: '导演', cinematographer: '摄影',
  continuity_supervisor: '场记', editor: '剪辑', editor_edit_intent: '剪辑',
  director_arbitration: '导演裁定',
  // 各工种都成功、落镜闸门拒绝时的归属：它不是一个模型工种。
  landing: '落镜',
};

/** 一次拒绝在页面上的人话：LLM_FORMAT 的截断证据单独说，常见策略拒绝码给一句话。 */
const REFUSAL_LABELS: Record<string, string> = {
  UNAPPROVED_ASSET_OR_UNKNOWN_BEAT: '镜头引用了未批准的主素材，或剧本里不存在的情节拍',
  CHARACTER_MASTER_BINDING_REQUIRED: '人物还没有绑定已确认的主素材',
  VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE: '画面描述里混入了中文，需要按提示词语言重写',
  DIALOGUE_NOT_IN_USER_SCRIPT: '对白不是剧本原文，需要按原文重写',
  SHOT_LOCATION_UNKNOWN: '镜头的地点不在已登记的场景里',
  SHOT_DIALOGUE_LONGER_THAN_SHOT: '这一镜的台词比镜头还长，按原速说不完',
  SHOT_CONTINUITY_PREDECESSOR_UNKNOWN: '接拍依赖的上一镜不存在或不属于本作品',
  // 戏剧层：小说先改编成可拍的戏，再进分镜。
  ARCHITECTURE_SCENE_COVERAGE: '集纲架构没有给每一场各排一个场次槽位',
  ARCHITECTURE_EPISODE_COVERAGE: '集纲架构没有逐集覆盖已确认的分集计划',
  DRAMA_LINE_TOO_LONG: '有一句台词太长，按正常语速一个镜头说不完，需要拆句',
  DRAMA_BEAT_SPEECH_TOO_LONG: '一个动作拍的台词太多，一个镜头装不下',
  STORY_ARCHITECTURE_SUPERSEDED: '集纲架构已过期（大纲或分集计划变了），需要重新生成分镜',
  // 场景导演 / 连续性引擎（V4）：
  BOARD_WITHOUT_SHOTS: '镜头板里没有任何镜头',
  DUPLICATE_SHOT_ID: '镜头板里有两个镜头用了同一个编号',
  SHOT_WITHOUT_BEAT: '有镜头没有说明拍的是哪个动作拍',
  UNKNOWN_BEAT: '镜头引用了这一场里不存在的动作拍',
  CUT_WITHOUT_BEAT: '镜内切没有说明切到哪个动作拍',
  BEAT_NOT_COVERED: '有戏剧动作拍没有被任何镜头拍到',
  BEAT_COVERED_TWICE: '同一个动作拍被两个镜头重复拍到',
  BEAT_OUT_OF_ORDER: '镜头顺序和这一场戏的顺序不一致',
  DIALOGUE_NOT_IN_SCENE: '镜头里有台词不是这一场戏写出来的',
  DIALOGUE_DROPPED: '这一场戏的台词被漏掉了',
  DIALOGUE_OUT_OF_ORDER: '台词顺序和这一场戏不一致',
  DIALOGUE_SPEAKER_CHANGED: '台词的说话人被换了',
  DIALOGUE_OUTSIDE_SUB_SHOT: '有台词落在了不属于它的画面之外',
  DIALOGUE_OVERLAP: '两句台词在时间上重叠',
  DIALOGUE_TOO_FAST_TO_SPEAK: '台词分到的时间按正常语速说不完',
  PLACEMENT_NOT_IN_CAST: '镜头里站了这场戏里没有的人',
  SHOT_LOCATION_CONFLICT: '镜头拍的不是这一场戏的地点',
  INTERNAL_CUT_COUNT: '一个渲染段里有不止一次镜内切',
  INTERNAL_CUT_WINDOW: '镜内切离镜头开头或结尾太近，两边都看不清',
  CAMERA_MOVE_WITHOUT_MOTIVE: '运镜没有说明理由',
  CAMERA_PROSE_MISMATCH: '运镜的文字描述和声明的机位运动对不上',
  CAMERA_MOVE_IN_CUT_SEGMENT: '带镜内切的镜头里用了过大的运镜',
  AXIS_ORDER_FLIPPED: '人物的左右站位跨过了 180° 轴线',
  AXIS_FACING_REVERSED: '人物的视线背离了对话对象，轴线错位',
  AXIS_CHANGED_WITHOUT_BREAK: '人物站位在没有转场理由的地方变了',
  EXCLUDED_REFERENCE_USED: '镜头把风格图或末帧图当成了参考图',
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

export function EpisodeStoryboardPanel({ projectId, active = true, onMessage, onJourneyRefresh, onGoStage }: {
  projectId: string;
  /** ② 与 ③ 保持挂载时，切回 ③ 页自动刷新最新风格/分集状态，避免停留在旧冻结视图。 */
  active?: boolean;
  onMessage?: (message: string) => void;
  onJourneyRefresh?: () => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const [state, setState] = useState<EpisodeStoryboardState | null>(null);
  const [tables, setTables] = useState<Record<string, {
    shots: EpisodeShotRow[]; total: number;
    target?: number | null; tolerance?: number | null;
    verdict?: { deviation_seconds: number } | null;
    confirmedSeconds?: number | null;
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
        target?: number | null; tolerance?: number | null;
        verdict?: { deviation_seconds: number } | null;
        confirmedSeconds?: number | null;
        stale?: boolean; confirmedVersion?: number | null; confirmedCount?: number }> = {};
      await Promise.all(rows.episodes.map(async (episode) => {
        try {
          const table = await fetchEpisodeShots(projectId, episode.episode_id);
          next[episode.episode_id] = {
            shots: table.shots, total: table.total_editorial_seconds,
            target: table.target_seconds, tolerance: table.tolerance_seconds,
            verdict: table.duration_verdict,
            confirmedSeconds: table.confirmed_seconds,
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

  useEffect(() => {
    if (active) void load().catch(() => undefined);
  }, [active, load]);
  const running = state?.status === 'QUEUED' || state?.status === 'RUNNING';
  // 右轨读 journey：任务推进时跟读同一刷新执行者，投影不留在旧事实上。
  // 回调用 ref 持有：App 每次渲染都新建回调，进依赖会不断重置定时器。
  const journeyRefreshRef = useRef(onJourneyRefresh);
  journeyRefreshRef.current = onJourneyRefresh;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      void load().then(() => journeyRefreshRef.current?.()).catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [load, running]);

  async function act(key: string, call: () => Promise<void>, success?: string) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(key); setProblem(''); setFailure(null);
    try {
      await call();
      await load();
      onJourneyRefresh?.();
      if (success) onMessage?.(success);
    } catch (error) {
      setFailure(error);
      setProblem(describeError(error));
      await load().catch(() => undefined);
      onJourneyRefresh?.();
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  const generate = () => act('generate', async () => {
    await queueDirectorStoryboard(projectId);
    onMessage?.('分镜任务已排队；关掉页面也会由常驻 Worker 继续逐场拆镜。');
  });
  // 一集确认的是它全部落下的镜头。时长超出目标窗时由用户逐镜改时长或显式接受偏差，
  // 不再按目标秒数只取「装得下的前缀」——那会把一集的结尾砍掉。
  const confirmEpisode = (episodeId: string, acknowledge = false) =>
    act(`confirm-${episodeId}`, async () => {
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

  const episodes = state?.episodes ?? [];
  const railPresent = useRailPresent();
  const episodeShotCount = (row: EpisodeStoryboardState['episodes'][number]) =>
    tables[row.episode_id]?.shots.length ?? row.landed_shot_count ?? row.shot_ids.length;
  const readyCount = episodes.filter((row) => confirmedEpisodes[row.episode_id]).length;
  // 操作栏必须名实一致：全部集都已确认且没有待重新确认的新拆镜时，没有「未确认
  // 的场次」可再跑，主操作应是去旅程的下一步（拍摄素材准备），而不是挂一个空跑
  // 的再跑按钮，也不能跳过素材准备直接去导演包。
  const allConfirmed = episodes.length > 0 && readyCount === episodes.length
    && !episodes.some((row) => tables[row.episode_id]?.stale);
  // 一套事实、一个裁决：整集的场次都已落镜（APPLIED）且尚未确认（或有新拆镜待重确认）的集，
  // 才属于待确认集；右边框体与每集按钮共享同一份可确认清单与同一个确认执行者。
  // 一集只在全部场次落镜后才算落定：不存在「达到目标秒数就停跑」的半集。
  const confirmableEpisodes = episodes.filter((row) => {
    if (episodeShotCount(row) <= 0) return false;
    if (tables[row.episode_id]?.stale) return true;
    if (confirmedEpisodes[row.episode_id]) return false;
    return row.status === 'APPLIED';
  });
  const confirmableCount = confirmableEpisodes.length;
  const landedTotal = episodes.reduce((sum, row) => sum + episodeShotCount(row), 0);
  const confirmAllReady = () =>
    act('confirm-all', async () => {
      for (const episode of confirmableEpisodes) {
        try {
          await confirmEpisodeShots(projectId, episode.episode_id, false);
        } catch (error) {
          if (error instanceof ApiError && error.code === 'EPISODE_DURATION_OUT_OF_TARGET') {
            setDurationDeviation({ episodeId: episode.episode_id, text: error.message });
          }
          throw error;
        }
      }
      setDurationDeviation(null);
      onJourneyRefresh?.();
    }, confirmableEpisodes.length > 1
      ? `已确认全部 ${confirmableEpisodes.length} 集落镜。`
      : `第 ${confirmableEpisodes[0]?.order ?? confirmableEpisodes[0]?.episode_id} 集镜头已落定。`);
  const resumeRun = () => {
    const id = state?.task_id; const version = state?.state_version ?? 0;
    if (!id) return;
    act('resume', async () => {
      await controlDirectorStoryboardTask(projectId, id, 'resume', version);
    }, '后台分镜已继续。');
  };
  const blockedEpisode = episodes.find((row) => row.status === 'BLOCKED' && row.blocker);
  const basisUpdated = Boolean(state?.stale_for_basis && !running);
  // 主按钮与页内按钮共用同一执行者：暂停→继续；底盘风格/分集已更新→按新设定重新生成；
  // 全确认→去素材准备；已有可确认落镜→确认全部落镜（不误报「再跑一遍」）；被阻塞且未知→核对+授权重发+重试。
  const primary: RailAction | null = running
    ? null
    : state?.status === 'PAUSED'
      ? { label: '继续后台分镜', onClick: resumeRun, loading: busy === 'resume', icon: <Play size={14} /> }
      : basisUpdated
        ? { label: '按新设定重新生成分镜', onClick: generate, loading: busy === 'generate', icon: <Sparkles size={14} /> }
        : allConfirmed
        ? { label: '去素材准备', onClick: () => onGoStage?.('assets-stage'), icon: <Film size={14} /> }
        : blockedEpisode?.blocker?.uncertain
          ? { label: '核对并重试被阻塞的集',
              onClick: () => reconcileAndRetryEpisode(
                blockedEpisode.episode_id,
                blockedEpisode.blocker?.reconcile_task_id ?? blockedEpisode.blocker?.failed_task_id ?? null,
                blockedEpisode.blocker?.reconcile_state_version ?? 0),
              loading: busy === `reconcile-${blockedEpisode.episode_id}`, icon: <ShieldCheck size={14} /> }
          : confirmableEpisodes.length > 0 && !blockedEpisode
            ? { label: confirmableEpisodes.length > 1
                  ? `确认全部已落镜（${confirmableEpisodes.length} 集）`
                  : `确认全部已落镜（第 ${confirmableEpisodes[0].order ?? confirmableEpisodes[0].episode_id} 集）`,
                onClick: confirmAllReady, loading: busy === 'confirm-all', icon: <Check size={14} /> }
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
  }, [state, busy, running, tables, confirmedEpisodes, allConfirmed, basisUpdated]);

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
      {!railPresent && <button type="button" className="btn-primary" disabled={busy !== '' || running || !primary}
        onClick={primary?.onClick}>
        <Play size={13} /> {running ? '分镜生成中…' : primary?.label}
      </button>}
      {basisUpdated && <span className="run-hint">
        <TriangleAlert size={12} /> 已冻结的导演风格或分集方案已更新；建议点「按新设定重新生成分镜」按最新预算与风格拆镜。
      </span>}
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
        <button type="button" className="btn-secondary" disabled={busy !== ''} onClick={resumeRun}>
          继续后台分镜
        </button>)}
      {state?.status === 'PAUSED' && <span className="run-hint">
        后台分镜已暂停；{confirmableCount > 0
          ? '可以继续，或先确认已完成的集。'
          : allConfirmed
            ? '已确认的集不受暂停影响；范围外场次已声明超范围，继续不会把它们纳入。'
            : '可以继续。本集还没拆完，拆完才出现确认入口；分集范围与每集秒数由②的分镜书决定。'}
      </span>}
      {state?.status === 'PAUSED' && !allConfirmed && confirmableCount === 0 && landedTotal > 0 && (
        <button type="button" className="btn-secondary" disabled={busy !== ''}
          onClick={() => onGoStage?.('plan-stage')}>
          去②调整分镜书范围
        </button>)}
    </div>
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {failure != null && <RequiredActionNotice error={failure} onGoStage={onGoStage} />}
    {subtitleResult && <p className="run-hint ok-hint"><Check size={12} /> {subtitleResult}</p>}

    {episodes.map((episode) => {
      const table = tables[episode.episode_id];
      const confirmedVersion = confirmedEpisodes[episode.episode_id];
      const applied = episode.status === 'APPLIED';
      const blocked = episode.status === 'BLOCKED';
      // 当前推进 frontier：被挡的那一段（或正在生成的那一段）。它还没落镜，
      // 所以在镜头表里占「下一镜」一行；镜位按同一份 集→场→镜 join 累加。
      const frontierUnit = blocked
        ? episode.units.find((unit) => unit.index === episode.blocker?.unit_index)
        : running ? episode.units.find((unit) => unit.state !== 'APPLIED') : undefined;
      let frontierPosition = table?.shots.length ?? episode.shot_ids.length;
      if (frontierUnit && (episode.sections?.length ?? 0) > 0) {
        frontierPosition = 0;
        for (const sec of episode.sections ?? []) {
          frontierPosition += sec.shot_count;
          if (sec.production_section_id === frontierUnit.production_section_id) break;
        }
      }
      frontierPosition += 1;
      return <details key={episode.episode_id} className="stage-dock episode-dock" open={!confirmedVersion || !!table?.stale}>
        <summary className="produce-focus-summary">
          <span className="focus-summary-label">
            {confirmedVersion
              ? table?.stale
                ? <><RefreshCw size={13} /> 第 {episode.order ?? episode.episode_id} 集 · 最新拆镜 {table.shots.length} 镜 · 待重新确认</>
                : <><Check size={13} className="stage-done-mark" /> 第 {episode.order ?? episode.episode_id} 集 · 已确认（v{confirmedVersion}，{table?.stale ? table.shots.length : (table?.confirmedCount ?? table?.shots.length ?? 0)} 镜）</>
              : blocked
                ? <><TriangleAlert size={13} /> 第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}（{table?.shots.length ?? episode.shot_ids.length} 镜）</>
                : <>第 {episode.order ?? episode.episode_id} 集 · {EPISODE_STATUS_LABELS[episode.status] ?? episode.status}（{table?.shots.length ?? episode.shot_ids.length} 镜）</>}
          </span>
        </summary>
        <div className="episode-body">
          {blocked && episode.blocker && (() => {
            const blocker = episode.blocker;
            const rejection = blocker.rejections?.[0];
            // 核对类拒绝码（占用身份的旧请求未核对）与 UNKNOWN 同路由：
            // 核对 → 仍未知时由点击授权一次重发 → 重试；直接重试只会被门禁再拒。
            const uncertain = blocker.uncertain ?? rejection?.external_outcome === 'UNKNOWN';
            const reconcileId = blocker.reconcile_task_id ?? rejection?.blocking_task?.task_id
              ?? rejection?.task_id ?? blocker.failed_task_id ?? null;
            const reconcileVersion = blocker.reconcile_state_version ?? rejection?.state_version ?? 0;
            const blockedUnit = episode.units.find((unit) => unit.index === blocker.unit_index)
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
            // 落镜拒绝不是一次模型调用：重试只复用已完成的五个工种结果重新落镜。
            const landingOnly = rejection?.role === 'landing';
            const fallback = `卡在${blockedUnit ? `第 ${frontierPosition} 镜（${scene}）` : scene}：${roleName} · ${refusalReason(rejection ?? {})}。`
              + (uncertain
                ? ' 这次请求的结果不确定，不会自动重发；核对并确认后才会发起一次新请求。'
                : landingOnly
                  ? ' 不会自动重发；重试只复用这一场已完成的工种结果重新落镜，已落镜的场次保留。'
                  : ' 不会自动重发；重试只从这一场重新发起一次模型请求，已落镜的场次保留。');
            return <div className="blocked-callout">
              <TriangleAlert size={14} />
              <span>{blocker.summary ?? fallback}</span>
              {uncertain
                ? <button type="button" className="btn-secondary" disabled={busy !== ''}
                    title="先核对上次请求；结果仍未知时，你的点击授权一次新请求"
                    onClick={() => reconcileAndRetryEpisode(episode.episode_id, reconcileId, reconcileVersion)}>
                    <ShieldCheck size={12} /> 核对这次请求并重试
                  </button>
                : <button type="button" className="btn-secondary" disabled={busy !== ''}
                    onClick={() => retryEpisode(episode.episode_id)}>
                    <RefreshCw size={12} /> {blockedUnit ? '重试这一场' : '重试这一集'}
                  </button>}
            </div>;
          })()}
          {table && (table.shots.length > 0 || (frontierUnit && (blocked || running))) && <>
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
                {frontierUnit && (blocked || running) && <tr className="scene-blocked-row">
                  <td>{frontierPosition}</td>
                  <td>{frontierUnit.production_section_id}</td>
                  <td className="shot-beat">
                    {blocked ? '被阻塞 · 待落镜（原因与核对按钮见上方）' : '生成中 · 待落镜'}
                  </td>
                  <td><span className="muted">—</span></td>
                  <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
                </tr>}
              </tbody>
            </table>
            </div>
            {table.shots.length > 0 && <>
              <p className="run-hint">
                {confirmedVersion && !table.stale
                  ? <>本集已确认 {table.confirmedCount ?? table.shots.length} 镜 · {
                      Math.round(table.confirmedSeconds ?? table.total)} 秒
                      {table.target != null && <>（②目标 {table.target} 秒）</>}；
                      范围外另落 {Math.max(0, table.shots.length - (table.confirmedCount ?? 0))} 镜，保留为历史。</>
                  : <>本集已落 {table.shots.length} 镜 · 合计 {Math.round(table.total)} 秒
                      {table.target != null && <>；②确认目标 {table.target} 秒（容差 ±{table.tolerance ?? 0}）</>}
                      {table.verdict
                        ? <>。当前{table.verdict.deviation_seconds > 0 ? '超出' : '低于'}目标窗 {
                            Math.abs(Math.round(table.verdict.deviation_seconds))} 秒：
                            可逐镜「改时长」把总时长调进目标窗，或确认时显式接受偏差。</>
                        : table.target != null ? <>，在目标窗内。</> : <>。</>}</>}
                提示词与字幕都可以逐镜人工接管；规范检查只给建议，不阻断。
              </p>
              <p className="run-hint">
                字幕在这里逐镜撰写与冻结；字幕轨的生成与装配在「后期成片」按真实成片时长进行，
                未出片不提前装配。本项目只走 Ref2VA：没有参考素材的镜头不会被提交渲染。
              </p>
            </>}
          </>}
          {applied && !confirmedVersion && <button type="button"
            className="btn-primary"
            disabled={busy !== '' || episodeShotCount(episode) === 0}
            onClick={() => confirmEpisode(episode.episode_id, false)}>
            <Check size={13} /> 确认全部落镜（{episodeShotCount(episode)} 镜）
          </button>}
          {confirmedVersion && table?.stale && <div className="notice" role="alert">
            <p>最新拆镜落了 {episodeShotCount(episode)} 镜，与已确认的 v{confirmedVersion}（{table.confirmedCount ?? 0} 镜）不同。
              重新确认本集才会用新镜头替换旧确认；旧镜头与已出片产物作为历史保留，不会被改写。</p>
            <button type="button" className="btn-primary" disabled={busy !== '' || episodeShotCount(episode) === 0}
              onClick={() => confirmEpisode(episode.episode_id, false)}>
              <Check size={13} /> 重新确认本集（采用最新 {episodeShotCount(episode)} 镜）
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

export function ShotMaterialsPanel({ projectId, onMessage, onGoStage, assetsDone, onAdvance, onJourneyRefresh }: {
  projectId: string;
  onMessage?: (message: string) => void;
  /** 必做动作失败时，把「下一步」送到对应阶段。 */
  onGoStage?: (target: ErrorActionTarget) => void;
  /** 服务端 journey 判定素材阶段已完成：主操作变为「确认并进入下一步」。 */
  assetsDone?: boolean;
  /** 进入 journey.next_stage 对应页面（与阶段衔接同一导航执行者）。 */
  onAdvance?: () => void;
  /** 同步刷新外层 journey / director overview，使第 5 阶段等下游与台账保持同一事实。 */
  onJourneyRefresh?: () => void;
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
  const [styleLoras, setStyleLoras] = useState<StyleLoraConfig[]>([]);
  const [stylePreset, setStylePreset] = useState<string | null>(null);
  const [characterSheetMode, setCharacterSheetMode] = useState(false);
  const imageStyle = useMemo(() => imageStyleInput(styleLoras, stylePreset), [styleLoras, stylePreset]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [batchTarget, setBatchTarget] = useState<Decision>('generate');
  const [progressKey, setProgressKey] = useState(0);
  /** 「从素材中心导入」弹窗当前服务的需求行；点选后走既有分配+采纳执行者。 */
  const [centerPickerRow, setCenterPickerRow] = useState<DirectorMaterialRequirement | null>(null);
  /** 「按修订换图」弹窗当前服务的需求行与已勾选镜头。 */
  const [swapTarget, setSwapTarget] = useState<{
    row: DirectorMaterialRequirement;
    preview: AdoptionPreviewResponse | null;
    selectedShots: Record<string, boolean>;
    error: string;
    loading: boolean;
  } | null>(null);
  /** 后台是否有在途出图：据此锁定主操作并给出「生成中」反馈（进度条同一事实）。 */
  const [generating, setGenerating] = useState(false);
  const submitting = useRef(false);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const knownMasters = useRef<Record<string, string | null>>({});

  const apply = useCallback((body: DirectorMaterialsView) => {
    setView(body);
    if (body.preferences) {
      setCharacterSheetMode(Boolean(body.preferences.effective_character_sheet_mode));
    }
    const prevMasters = knownMasters.current;
    const nextMasters: Record<string, string | null> = {};
    for (const row of body.requirements) {
      nextMasters[row.requirement_id] = row.matched_master?.asset_id ?? null;
    }
    knownMasters.current = nextMasters;
    setDecisions((current) => {
      // 已经做过的选择不会被一次刷新覆盖；但当裁决变化使旧选择失效（必需项不能
      // 跳过、缺主素材不能复用）或该项刚上传/晋升并采用主素材时，跟随同一份
      // 权威事实切到 reuse_master，避免下一次整单提交把已上传图片重置回生成。
      const next: Record<string, Decision> = {};
      for (const row of body.requirements) {
        const prev = current[row.requirement_id];
        const hadMaster = prevMasters[row.requirement_id] ?? null;
        const nowMaster = row.matched_master?.asset_id ?? null;
        const newlyAdopted = nowMaster !== null && nowMaster !== hadMaster && row.strategy === 'reuse_master';
        const stale = prev === 'skip' && row.critical
          || prev === 'reuse_master' && !row.matched_master;
        if (newlyAdopted || (prev === 'upload' && row.matched_master)) {
          next[row.requirement_id] = 'reuse_master';
        } else {
          next[row.requirement_id] = prev && !stale ? prev : defaultDecision(row);
        }
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

  useEffect(() => {
    setCharacterSheetMode(false);
    setSwapTarget(null);
    void load();
  }, [load, projectId]);

  const handleCharacterSheetToggle = useCallback((next: boolean) => {
    setCharacterSheetMode(next);
    void updateMaterialPreferences(projectId, next)
      .then((prefs) => {
        if (activeProject.current === projectId) {
          setCharacterSheetMode(Boolean(prefs.effective_character_sheet_mode));
        }
      })
      .catch(() => undefined);
  }, [projectId]);

  async function openSwapDialog(row: DirectorMaterialRequirement) {
    const masterId = row.matched_master?.asset_id;
    const oldShots = row.shot_binding?.other_asset_shots ?? [];
    if (!masterId || !oldShots.length) return;
    const defaultSelected = Object.fromEntries(oldShots.map((item) => [item.shot_id, true]));
    setSwapTarget({ row, preview: null, selectedShots: defaultSelected, error: '', loading: true });
    try {
      const preview = await previewShotAdoption({
        project_id: projectId,
        project_asset_id: masterId,
        shot_ids: oldShots.map((item) => item.shot_id),
        role: row.family,
        entity_id: row.entity_id ?? undefined,
        allow_revise_frozen: true,
      });
      if (activeProject.current !== projectId) return;
      setSwapTarget((cur) => cur ? { ...cur, preview, loading: false } : cur);
    } catch (error) {
      setSwapTarget((cur) => cur ? { ...cur, error: describeError(error), loading: false } : cur);
    }
  }

  async function confirmSwapAdoption() {
    if (!swapTarget?.preview) return;
    const chosenIds = Object.entries(swapTarget.selectedShots)
      .filter(([, checked]) => checked)
      .map(([sid]) => sid);
    const usable = swapTarget.preview.shots.filter(
      (shot) => chosenIds.includes(shot.shot_id) && !shot.conflict && !shot.already_bound,
    );
    if (!usable.length) {
      setSwapTarget((cur) => cur ? { ...cur, error: '请至少选择一个可替换的镜头' } : cur);
      return;
    }
    setSwapTarget((cur) => cur ? { ...cur, loading: true, error: '' } : cur);
    try {
      const receipt = await applyShotAdoption({
        project_id: projectId,
        project_asset_id: swapTarget.preview.project_asset_id,
        shot_ids: usable.map((shot) => shot.shot_id),
        role: swapTarget.row.family,
        entity_id: swapTarget.row.entity_id ?? undefined,
        allow_revise_frozen: true,
        expected_shot_revisions: Object.fromEntries(usable.map((shot) => [shot.shot_id, shot.revision])),
        idempotency_key: crypto.randomUUID(),
        confirmed: true,
      });
      setSwapTarget(null);
      setResult(
        `已修订并替换 ${receipt.adopted_count} 个镜头的绑定（${swapTarget.row.name ?? swapTarget.row.entity_id}）；旧导演包与旧通过成片保留为历史，请到「⑦ 后期成片 / 生成分段」重拍对应镜头并装配新版成片。`,
      );
      onMessage?.(`已按修订替换 ${receipt.adopted_count} 个镜头的素材绑定。`);
      await load();
      onJourneyRefresh?.();
    } catch (error) {
      setSwapTarget((cur) => cur ? { ...cur, error: describeError(error), loading: false } : cur);
    }
  }

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
      onJourneyRefresh?.();
    } catch (error) {
      setFailure(error);
      setProblem(describeError(error));
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  async function commit(queueGeneration = true) {
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
        queue_generation: queueGeneration,
        ...(queueGeneration ? { ...imageStyle, character_sheet_mode: characterSheetMode } : {}),
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
      onJourneyRefresh?.();
      setProgressKey((key) => key + 1);
      return !outcome.batch_error;
    } catch (error) {
      setProblem(describeError(error));
      // 断连或超时：先读台账，确认是否其实已经生效，再让用户决定。
      await load().catch(() => undefined);
      onJourneyRefresh?.();
      return false;
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }

  /** 素材已就绪：确认决策后进入 journey 的下一阶段（导演包），不再重复备料。 */
  async function commitThenAdvance() {
    const ok = await commit(false);
    if (ok) onAdvance?.();
  }

  /** 重新生成素材：唯一 regenerate-all 执行者；有勾选用勾选集，否则只重生成尚未采用主素材的需求（全部已采用时全量）。 */
  async function regenerateAll() {
    if (busy !== '' || !requirements.length) return;
    const unadoptedIds = requirements
      .filter((row) => !(row.strategy === 'reuse_master' && row.matched_master))
      .map((row) => row.requirement_id);
    const ids = selectedIds.length
      ? selectedIds
      : (unadoptedIds.length ? unadoptedIds : requirements.map((row) => row.requirement_id));
    setBusy('commit'); setProblem(''); setResult(null);
    try {
      const outcome = await api<{ items?: string[] }>(
        `/projects/${projectId}/material-preparation/regenerate-all`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            idempotency_key: `director-regen-all-${Date.now()}`,
            requirement_ids: ids,
            ...imageStyle,
            character_sheet_mode: characterSheetMode,
          }) });
      setResult(`已为 ${outcome.items?.length ?? ids.length} 项重新排队出图；旧候选保留，出图后在上方选定并采用。`);
      await load();
      onJourneyRefresh?.();
      setProgressKey((key) => key + 1);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  /** 单条需求立即执行一个决策（重新生成 / 复用新素材），不影响其他行。 */
  async function commitOne(row: DirectorMaterialRequirement, strategy: Decision, assetId?: string | null) {
    if (!view?.plan_revision_id || !view.expected_snapshot) return;
    setBusy(`row-${row.requirement_id}`); setProblem(''); setResult(null);
    try {
      if (strategy === 'generate') {
        // 重生成走唯一 regenerate-all 执行者（收束在途项、建新修订）；commit 不收束
        // 在途项，已有批次时会被 MATERIAL_NOTHING_TO_GENERATE 拒绝。
        await api(`/projects/${projectId}/material-preparation/regenerate-all`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            idempotency_key: `director-regen-one-${Date.now()}`,
            requirement_ids: [row.requirement_id],
            ...imageStyle,
            character_sheet_mode: characterSheetMode,
          }),
        });
      } else {
        await commitDirectorMaterials(projectId, {
          plan_revision_id: view.plan_revision_id,
          expected_snapshot: view.expected_snapshot,
          decisions: [{
            requirement_id: row.requirement_id,
            strategy,
            entity_id: row.entity_id ?? row.name ?? 'unknown',
            asset_id: assetId ?? null,
          }],
          queue_generation: false,
        });
      }
      setDecisions((current) => ({ ...current, [row.requirement_id]: strategy }));
      setResult(strategy === 'generate'
        ? `「${row.name ?? row.entity_id}」已重新排队制作候选；出图后由你选定并采用。`
        : `「${row.name ?? row.entity_id}」已改用你上传的主素材。`);
      onMessage?.('这一项素材已更新。');
      await load();
      onJourneyRefresh?.();
      setProgressKey((key) => key + 1);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  /** 用户自行上传：上传 →（若尚未批准/锁定则补全批准与锁定）→ 直接把这一项回绑到该主素材。
   *  同一文件若此前已上传并批准锁定，直接复用同一 Asset 回绑，避免重复 approve 报 409。 */
  async function uploadFor(row: DirectorMaterialRequirement, file: File) {
    setBusy(`row-${row.requirement_id}`); setProblem(''); setResult(null);
    try {
      const targetEntity = row.entity_id ?? row.name ?? undefined;
      const asset = await uploadProjectAsset(projectId, file, {
        role: row.family, entityId: targetEntity,
      });
      if ((asset.role && asset.role !== row.family)
          || (targetEntity && asset.entity_id && asset.entity_id !== targetEntity)) {
        throw new Error(
          `这张图片已在本作品中登记为「${asset.entity_id ?? asset.filename}」，不能重复用于「${targetEntity ?? row.family}」，请换一张图片或去素材中心查看。`,
        );
      }
      if (targetEntity && !asset.entity_id && asset.locked) {
        throw new Error(
          `这张图片已锁定但未归属到「${targetEntity}」，请换一张图片或在素材中心调整。`,
        );
      }
      if (targetEntity && asset.role && !asset.entity_id && !asset.locked) {
        await api(`/assets/${asset.id}/metadata`, json('PATCH', { entity_id: targetEntity }));
      }
      if (!asset.approved) await approveProjectAsset(asset.id);
      if (!asset.locked) await lockProjectAsset(asset.id);
      await commitOne(row, 'reuse_master', asset.id);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  /** 从素材中心导入：分配为本项目主素材（既有 assignLibraryVersion 执行者），
   *  再走同一 commitOne 采纳执行者回绑镜头，不另起平行链路。 */
  async function importFromCenter(row: DirectorMaterialRequirement, item: { asset: LibraryAsset; version: LibraryVersion }) {
    setCenterPickerRow(null);
    setBusy(`row-${row.requirement_id}`); setProblem(''); setResult(null);
    let assignment: Record<string, any>;
    try {
      assignment = await assignLibraryVersion({
        project_id: projectId,
        library_version_id: item.version.id,
        role: row.family,
        entity_id: row.entity_id ?? row.name ?? undefined,
        idempotency_key: crypto.randomUUID(),
        confirmed: true,
      });
    } catch (error) {
      setProblem(describeError(error)); setBusy(''); return;
    }
    await commitOne(row, 'reuse_master', assignment.project_asset_id);
    setResult(`已从素材中心导入「${item.asset.title}」并绑定到该需求。`);
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
  /** 勾选后统一重新生成：复用唯一重生成执行者 regenerate-all（会收束在途项并建
   *  新修订），只作用于勾选集。commit 是“确认决策”执行者，不收束在途项，用它重生成
   *  会在已有批次时被 MATERIAL_NOTHING_TO_GENERATE 拒绝。 */
  async function regenerateSelected() {
    if (selectedIds.length === 0 || busy !== '') return;
    setBusy('commit'); setProblem(''); setResult(null);
    try {
      const outcome = await api<{ items?: string[]; run_id?: string }>(
        `/projects/${projectId}/material-preparation/regenerate-all`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            idempotency_key: `director-regen-${Date.now()}`,
            requirement_ids: selectedIds,
            ...imageStyle,
            character_sheet_mode: characterSheetMode,
          }) });
      setResult(`已为所选 ${outcome.items?.length ?? selectedIds.length} 项重新排队出图；旧候选保留，出图后在同一候选栏选定并采用。`);
      setSelected({});
      await load();
      onJourneyRefresh?.();
      setProgressKey((key) => key + 1);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }
  const pendingChoice = requirements.some((row) => !decisions[row.requirement_id]);
  const missingCritical = requirements.filter(
    (row) => row.critical && !row.matched_master).length;
  // 主操作跟随服务端 journey 的单一裁决：素材阶段已完成→「确认并进入下一步」；
  // 未完成→「确认决策并开始准备素材」。不在前端另算就绪（三原则）。
  const readyToAdvance = Boolean(assetsDone && onAdvance);
  const primary: RailAction | null = !requirements.length
    ? { label: '按已确认镜头生成素材清单', onClick: () => void derive(),
        loading: busy === 'derive', icon: <Film size={14} /> }
    : { label: busy === 'commit' ? '正在提交…'
        : readyToAdvance ? '确认并进入下一步'
        : generating ? '素材生成中…' : '确认决策并开始准备素材',
        onClick: () => (readyToAdvance ? void commitThenAdvance() : void commit()),
        loading: busy === 'commit' || (generating && !readyToAdvance),
        disabled: busy !== '' || pendingChoice || (generating && !readyToAdvance),
        icon: <ListChecks size={14} />,
        disabledReason: pendingChoice ? '每一项都要选一个处理方式（可选项可选「跳过」）'
          : (generating && !readyToAdvance) ? '后台正在出图，完成后可继续确认或进入下一步' : undefined };
  const secondary: RailAction[] = requirements.length
    ? [{ label: '重新生成素材', onClick: () => void regenerateAll(),
        loading: busy === 'commit', disabled: busy !== '', icon: <RefreshCw size={14} /> }]
    : [];
  useStageRail('shot-materials', { priority: 1, primary, secondary },
    [view, decisions, busy, assetsDone, onAdvance, generating, characterSheetMode, imageStyle]);

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
      onInFlightChange={setGenerating}
      masters={requirements.filter((row) => row.strategy === 'reuse_master' && row.matched_master)
        .map((row) => ({
          requirement_id: row.requirement_id, entity_id: row.entity_id, family: row.family,
          asset_id: row.matched_master!.asset_id,
          shotBinding: row.shot_binding ?? null,
        }))}
      onPromoted={() => { void load(); onJourneyRefresh?.(); }}
      onUnadopt={async (requirementId) => {
        const row = requirements.find((r) => r.requirement_id === requirementId);
        if (!row || !view?.plan_revision_id || !view.expected_snapshot) return;
        // 取消采用＝改回「AI 制作」但不排队：同一决策执行者清绑定、解绑镜头。
        await commitDirectorMaterials(projectId, {
          plan_revision_id: view.plan_revision_id,
          expected_snapshot: view.expected_snapshot,
          decisions: [{ requirement_id: row.requirement_id, strategy: 'generate',
            entity_id: row.entity_id ?? row.name ?? 'unknown', asset_id: null }],
          queue_generation: false,
        });
        setDecisions((current) => ({ ...current, [requirementId]: 'generate' }));
        await load();
        onJourneyRefresh?.();
      }} />

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
        <button type="button" className="btn-secondary" disabled={busy !== '' || selectedIds.length === 0}
          title="勾选后点这里：只对所选需求统一重新出图，旧候选保留"
          onClick={() => void regenerateSelected()}>重新生成所选（{selectedIds.length}）</button>
        <button type="button" className="btn-secondary" disabled={busy !== ''}
          title="把尚未采用主素材的条目设为 AI 制作；已上传或已采用的主素材保留"
          onClick={() => {
            const unadoptedIds = requirements
              .filter((row) => !(row.strategy === 'reuse_master' && row.matched_master))
              .map((row) => row.requirement_id);
            const targetIds = unadoptedIds.length
              ? unadoptedIds
              : requirements.map((row) => row.requirement_id);
            setSelected(Object.fromEntries(targetIds.map((id) => [id, true])));
            applyBatch('generate', targetIds);
          }}>
          全部 AI 制作</button>
        <button type="button" className="btn-text" disabled={busy !== '' || selectedIds.length === 0}
          onClick={() => setSelected({})}>清空选择</button>
      </div>
      <CharacterSheetToggle
        checked={characterSheetMode}
        onChange={handleCharacterSheetToggle}
        disabled={busy !== ''}
        onGoSetup={() => {
          if (onGoStage) onGoStage('setup-env');
          else window.location.hash = '#d/setup';
        }}
      />
      <details className="advanced-fold">
        <summary>生图风格 LoRA{styleLoras.length ? ` · 已选 ${styleLoras.length} 枚` : ''}</summary>
        <StyleLoraAssemblyPanel baseModel="qwen_image_21" value={styleLoras} onChange={setStyleLoras}
          loraSupported disabled={busy !== ''} stylePreset={stylePreset} onPresetChange={setStylePreset} />
      </details>
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
            <td>{(() => {
              const sb = row.shot_binding;
              if (!row.matched_master) {
                return <span className={`head-chip ${row.critical ? 'warn' : ''}`}>
                  {row.critical ? '必需，缺' : '可选，缺'}
                </span>;
              }
              if (sb?.status === 'LIBRARY_ONLY') {
                return <div className="material-binding-status">
                  <span className="head-chip warn">
                    已入库·镜头仍用旧图（0/{sb.target_shot_count}）
                  </span>
                  <small className="muted">
                    仍在用旧图：{sb.other_asset_shots.map((s) => `${s.shot_number ? `第${s.shot_number}镜` : s.shot_name}(${s.asset_id.slice(0, 8)})`).join('、')}
                  </small>
                </div>;
              }
              if (sb?.status === 'BOUND_PARTIAL') {
                return <div className="material-binding-status">
                  <span className="head-chip warn">
                    部分镜头已绑定（{sb.bound_to_current_count}/{sb.target_shot_count}）
                  </span>
                  <small className="muted">
                    仍用旧图：{sb.other_asset_shots.map((s) => `${s.shot_number ? `第${s.shot_number}镜` : s.shot_name}(${s.asset_id.slice(0, 8)})`).join('、')}
                  </small>
                </div>;
              }
              if (sb && sb.target_shot_count > 0 && sb.status === 'BOUND_ALL') {
                return <span className="head-chip ok">
                  已绑定全部镜头（{sb.bound_to_current_count}/{sb.target_shot_count}）
                </span>;
              }
              return <span className="head-chip ok">已匹配，可复用</span>;
            })()}</td>
            <td>
              <select value={decisions[row.requirement_id] ?? ''} disabled={busy !== ''}
                aria-label={`处理方式：${row.name ?? row.entity_id}`}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === 'import_center') { setCenterPickerRow(row); return; }
                  setDecisions((current) => ({ ...current, [row.requirement_id]: value as Decision }));
                }}>
                {row.matched_master && <option value="reuse_master">复用现有主素材</option>}
                <option value="generate">AI 制作</option>
                <option value="upload">我自己上传</option>
                <option value="import_center">从素材中心导入…</option>
                {!row.critical && <option value="skip">跳过（可选项，不影响生成）</option>}
                {row.critical && !row.matched_master && <option value="">请选择…</option>}
              </select>
            </td>
            <td className="material-row-actions">
              {row.matched_master && (row.shot_binding?.other_asset_shots?.length ?? 0) > 0 && (
                <button type="button" className="btn-secondary" disabled={busy !== ''}
                  title="在保留旧导演包与旧通过成片的前提下，按明确修订将新主素材绑定到选定镜头"
                  onClick={() => void openSwapDialog(row)}>按修订换图</button>
              )}
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
      imageStyle={imageStyle}
      requirement={{
        requirement_id: promptTarget.requirement_id,
        name: promptTarget.name ?? promptTarget.entity_id ?? '这一项素材',
        family: promptTarget.family,
        description: promptTarget.description,
      }}
      onClose={() => setPromptTarget(null)}
      onChanged={(message) => { onMessage?.(message); void load(); }}
    />}

    {centerPickerRow && <AssetCenterPicker
      title={`从素材中心导入 · ${centerPickerRow.name ?? centerPickerRow.entity_id ?? '素材'}`}
      kicker="素材来源"
      emptyHint="素材中心还没有可分配的已晋升素材。先在素材中心生成、审核并晋升，再回来导入。"
      fetchItems={async (q) => (await searchLibraryForProject(projectId, q, undefined, 'library')).items}
      display={(item) => ({
        key: item.version.id,
        title: item.asset.title,
        subtitle: `V${item.version.ordinal} · ${item.version.content_sha256.slice(0, 8)} · ${item.asset.category}`,
        mediaUrl: item.version.media_url,
      })}
      onPick={(item) => void importFromCenter(centerPickerRow, item)}
      onClose={() => setCenterPickerRow(null)}
    />}

    {swapTarget && <dialog open className="media-preview-dialog" aria-label={`按修订换图 · ${swapTarget.row.name ?? swapTarget.row.entity_id}`}>
      <h3>按修订换图 · {swapTarget.row.name ?? swapTarget.row.entity_id}</h3>
      <p className="run-hint">
        新素材（{swapTarget.row.matched_master?.asset_id.slice(0, 8)}）已进入作品素材库；由于目标镜头曾冻结在已确认导演包或已有通过成片，默认保护不自动覆盖。勾选要换图的镜头并确认修订（旧导演包与已通过成片保留为历史）：
      </p>
      {swapTarget.loading && <p className="material-hint">正在检查镜头绑定与版本…</p>}
      {swapTarget.error && <p role="alert" className="form-error">{swapTarget.error}</p>}
      {swapTarget.preview && <ul>
        {swapTarget.preview.shots.map((shot) => {
          const other = swapTarget.row.shot_binding?.other_asset_shots.find((o) => o.shot_id === shot.shot_id);
          return <li key={shot.shot_id}>
            <label>
              <input type="checkbox"
                disabled={swapTarget.loading || !!shot.conflict}
                checked={!!swapTarget.selectedShots[shot.shot_id]}
                onChange={(event) => setSwapTarget((cur) => cur ? {
                  ...cur,
                  selectedShots: { ...cur.selectedShots, [shot.shot_id]: event.target.checked },
                } : cur)} />
              {' '}
              <b>{other?.shot_number ? `第 ${other.shot_number} 镜` : shot.shot_name}</b>
              {' '}（v{shot.revision} → v{shot.revision + 1}）
              {shot.replaces_asset_id && <span className="muted"> · 替换旧图 {shot.replaces_asset_id.slice(0, 8)}（{shot.replaces_asset_filename}）</span>}
              {shot.conflict && <span className="warn"> · 阻断：{shot.conflict}</span>}
            </label>
          </li>;
        })}
      </ul>}
      <div className="segment-card-actions">
        <button type="button" className="btn-primary" disabled={swapTarget.loading || !swapTarget.preview}
          onClick={() => void confirmSwapAdoption()}>确认按修订换图</button>
        <button type="button" className="btn-secondary" disabled={swapTarget.loading}
          onClick={() => setSwapTarget(null)}>取消</button>
      </div>
    </dialog>}
  </section>;
}
