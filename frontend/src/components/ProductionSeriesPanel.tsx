import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Clapperboard, Layers, Play, ShieldCheck, Square, Loader2 } from 'lucide-react';
import { api, json, type ProductionSeries, type Shot, type SystemStatus, type WorkflowProfile } from '../api';
import { continuityLabels, failureLabels, finalizationLabels, label, statusLabels } from '../constants';

type Props = {
  projectId: string;
  shots: Shot[];
  workflowProfiles: WorkflowProfile[];
  system: SystemStatus | null;
  busy: boolean;
  demoSelected?: boolean;
  onSelectReal?: () => void;
  reviewWidth: string;
  reviewHeight: string;
  onStarted: () => void;
  onMessage: (message: string, kind?: 'success' | 'error') => void;
  /** 模式入口动作：enable_real 打开启用向导，check_fix 打开检查修复。 */
  onModeAction: (action: 'enable_real' | 'check_fix') => void;
  /** 缺生成方案/宽高映射时，引导回基础配置。 */
  onGoSetup?: () => void;
};

const statusText: Record<string, string> = {
  QUEUED: '排队等待制作',
  RUNNING: '制作中',
  PAUSED: '已暂停',
  PAUSE_REQUESTED: '正在暂停',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  NEEDS_REVIEW: '需要复核',
  PENDING: '待生产',
};

const itemText: Record<string, string> = {
  PENDING: '待生产',
  QUEUED: '已入队',
  RUNNING: '渲染中',
  PASSED: '已通过',
  FAILED: '失败',
  BLOCKED: '已阻断',
  CANCELLED: '已取消',
};

/** H3 的实测规格：24fps、宽高 32 的倍数、单镜 4–15 秒。预设只给已经在
 *  本项目验收过的画幅，自定义仍受同一组约束。 */
const FRAME_PRESETS = [
  { id: 'default', label: '按方案默认（不改分辨率）', width: 0, height: 0 },
  { id: 'portrait', label: '竖屏 9:16 · 768×1344（手机短剧）', width: 768, height: 1344 },
  { id: 'landscape', label: '横屏 16:9 · 1344×768（横屏成片）', width: 1344, height: 768 },
  { id: 'square', label: '方形 1:1 · 960×960', width: 960, height: 960 },
  { id: 'custom', label: '自定义（宽高需为 32 的倍数）', width: 0, height: 0 },
] as const;

const CLIP_CHOICES = ['', '4', '5', '6', '8', '10', '12', '15'];

const FAMILY_ORDER: Record<string, number> = { subject: 0, picture: 1, video: 2, audio: 3 };

/** The reference labels this shot will compile to: the same family order and
 *  character order the server uses when it builds the manifest. */
function referenceLabels(shot: Shot) {
  const characters = [...new Set(shot.spec.characters ?? [])];
  const rank = new Map(characters.map((name, index) => [name, index]));
  const ordered = [...shot.bindings].sort((a, b) => {
    const family = (FAMILY_ORDER[a.use_as] ?? 9) - (FAMILY_ORDER[b.use_as] ?? 9);
    if (family !== 0) return family;
    if (a.use_as === 'subject' && b.use_as === 'subject') {
      const left = rank.get(a.entity_id ?? '') ?? 999;
      const right = rank.get(b.entity_id ?? '') ?? 999;
      if (left !== right) return left - right;
    }
    return a.ordinal - b.ordinal || a.asset_id.localeCompare(b.asset_id);
  });
  let image = 0;
  let subject = 0;
  const rows: Array<{ label: string; name: string; use_as: string; asset_id: string }> = [];
  for (const binding of ordered) {
    if (binding.use_as === 'subject' || binding.use_as === 'picture') image += 1;
    if (binding.use_as === 'subject') {
      subject += 1;
      rows.push({ label: `<Subject ${subject}>`, name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    } else if (binding.use_as === 'picture') {
      rows.push({ label: `<Picture ${image}>`, name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    } else if (binding.use_as === 'audio') {
      rows.push({ label: '音色参考', name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    }
  }
  return rows;
}

function isMultipleOf32(value: number): boolean {
  return Number.isInteger(value) && value >= 256 && value <= 2048 && value % 32 === 0;
}

export default function ProductionSeriesPanel({
  projectId, shots, workflowProfiles, system, busy, reviewWidth, reviewHeight,
  onStarted, onMessage, onModeAction, onGoSetup, demoSelected = false, onSelectReal,
}: Props) {
  const [series, setSeries] = useState<ProductionSeries[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [profileId, setProfileId] = useState('');
  const [scope, setScope] = useState<'full' | 'basic'>('full');
  const [editIntents, setEditIntents] = useState<Array<{ id: string; created_at: string }>>([]);
  const [editIntentId, setEditIntentId] = useState('');
  const [framePreset, setFramePreset] = useState<string>('default');
  const [customWidth, setCustomWidth] = useState('768');
  const [customHeight, setCustomHeight] = useState('1344');
  const [clipSeconds, setClipSeconds] = useState('');
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    const rows = await api<ProductionSeries[]>(`/projects/${projectId}/production-series`);
    setSeries(rows);
  }, [projectId]);

  useEffect(() => { reload().catch(() => setSeries([])); }, [reload, projectId, shots.length]);

  useEffect(() => {
    api<Array<{ id: string; kind: string; stale: boolean; created_at: string }>>(`/projects/${projectId}/film-artifacts`)
      .then((rows) => setEditIntents(
        rows.filter((row) => row.kind === 'EditIntentPlan' && !row.stale)
          .map(({ id, created_at }) => ({ id, created_at })),
      ))
      .catch(() => setEditIntents([]));
  }, [projectId, shots.length]);

  const ref2vaProfiles = workflowProfiles.filter((profile) => profile.mode.toLowerCase() === 'ref2va');
  // Only one validated Ref2VA profile exists: preselect it instead of making
  // the user pick from one option; multiple options keep the manual choice.
  useEffect(() => {
    if (ref2vaProfiles.length === 1 && !profileId) setProfileId(ref2vaProfiles[0].id);
  }, [ref2vaProfiles, profileId]);
  const activeSeries = series.find((row) => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status));
  // A shot that is already being produced must not be queued a second time.
  const busyShotIds = new Set(
    series
      .filter((row) => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status))
      .flatMap((row) => row.items.map((item) => item.shot_id)),
  );
  const selectedBusy = selected.filter((id) => busyShotIds.has(id));
  const activeProfile = ref2vaProfiles.find((profile) => profile.id === profileId);
  const profileMapsSize = !!activeProfile
    && 'width' in (activeProfile.parameter_map ?? {}) && 'height' in (activeProfile.parameter_map ?? {});

  const preset = FRAME_PRESETS.find((row) => row.id === framePreset) ?? FRAME_PRESETS[0];
  const requestedSize = framePreset === 'custom'
    ? { width: Number(customWidth), height: Number(customHeight) }
    : { width: preset.width, height: preset.height };
  const wantsSize = requestedSize.width > 0 || requestedSize.height > 0;
  const sizeValid = !wantsSize || (isMultipleOf32(requestedSize.width) && isMultipleOf32(requestedSize.height));
  const sizeBlocked = wantsSize && !profileMapsSize;

  /** 成片长度：选中的镜头（未选时全部）按计划时长或统一时长估算。 */
  const lengthPreview = useMemo(() => {
    const rows = shots.filter((shot) => selected.length === 0 || selected.includes(shot.id));
    const each = clipSeconds ? Number(clipSeconds) : 0;
    const total = rows.reduce((sum, shot) => sum + (each || Number(shot.spec.duration_target_s ?? 0)), 0);
    return { count: rows.length, total, unified: !!each };
  }, [shots, selected, clipSeconds]);

  const identityRows = useMemo(() => {
    const rows = shots.filter((shot) => selected.length === 0 || selected.includes(shot.id));
    return rows.slice(0, 4).map((shot) => {
      const refs = referenceLabels(shot);
      const subjects = refs.filter((row) => row.use_as === 'subject');
      const characters = [...new Set(shot.spec.characters ?? [])];
      const unbound = characters.filter((name) => !subjects.some((row) => row.name === name));
      return { shot, refs, characters, unbound };
    });
  }, [shots, selected]);
  const unboundCount = identityRows.reduce((sum, row) => sum + row.unbound.length, 0);

  function toggle(shotId: string) {
    setSelected((current) => current.includes(shotId)
      ? current.filter((id) => id !== shotId)
      : [...current, shotId]);
  }

  /** 选择全部可制作镜头（排除已在生产中的），并按项目镜头顺序提交。 */
  function selectAllProducible() {
    setSelected(shots.filter((shot) => !busyShotIds.has(shot.id)).map((shot) => shot.id));
  }

  async function start() {
    if (demoSelected || system?.production_mode?.mode !== 'READY' || working || busy) return;
    if (!profileId) { onMessage('当前没有可用的生成方案，请先在基础配置里完成登记。', 'error'); return; }
    if (selected.length === 0) { onMessage('请至少选择一个镜头。', 'error'); return; }
    if (selectedBusy.length > 0) {
      onMessage('选中的镜头已经在生产队列中，请勿重复提交。', 'error');
      return;
    }
    if (!sizeValid) {
      onMessage('自定义画幅需要 256–2048 之间、且为 32 的倍数的宽高（H3 规范）。', 'error');
      return;
    }
    if (sizeBlocked) {
      onMessage('当前生成方案没有接入宽高参数：请到基础配置 → 模型与生成方案，点「补齐宽高参数」后再选画幅。', 'error');
      return;
    }
    if (clipSeconds) {
      const tooTight = shots
        .filter((shot) => selected.includes(shot.id))
        .filter((shot) => Math.max(0, ...(shot.spec.dialogue ?? []).map((line) => line.end_s)) + 0.2 > Number(clipSeconds))
        .map((shot) => shot.name);
      if (tooTight.length > 0) {
        onMessage(`这些镜头的对白装不进 ${clipSeconds} 秒：${tooTight.join('、')}。请改回「按分镜计划」或加长片段。`, 'error');
        return;
      }
      if (!window.confirm(`把选中的 ${selected.length} 个镜头统一改为 ${clipSeconds} 秒？\n这会重新编译这些镜头，已生成的同镜成片会失效。`)) return;
    }
    setWorking(true);
    try {
      // 按项目镜头顺序提交，不按勾选顺序改变故事顺序。
      const shot_ids = shots
        .map((shot) => shot.id)
        .filter((id) => selected.includes(id));
      const body: Record<string, unknown> = {
        shot_ids,
        provider: 'comfyui',
        workflow_profile_id: profileId,
        self_review_scope: scope,
      };
      if (scope === 'basic') {
        body.basic_review_resolution = { width: Number(reviewWidth), height: Number(reviewHeight) };
      }
      if (wantsSize || clipSeconds) {
        body.output = {
          ...(wantsSize ? { width: requestedSize.width, height: requestedSize.height } : {}),
          clip_seconds: clipSeconds ? Number(clipSeconds) : null,
        };
      }
      if (editIntentId) {
        body.edit_intent_artifact_id = editIntentId;
      }
      await api(`/projects/${projectId}/production-series`, json('POST', body));
      setSelected([]);
      onMessage(`已开始连续制作；本机会依次生成 ${shot_ids.length} 个镜头并自动整理成片。小模型审片和对白识别只提供建议。`, 'success');
      await reload();
      onStarted();
    } catch (error) {
      onMessage(String(error), 'error');
    } finally {
      setWorking(false);
    }
  }

  async function act(seriesId: string, action: 'cancel' | 'pause' | 'resume') {
    setWorking(true);
    try {
      await api(`/production-series/${seriesId}/${action}`, json('POST', {}));
      await reload();
      onMessage(`已${{ cancel: '取消', pause: '暂停', resume: '恢复' }[action]}该生产序列。`, 'success');
    } catch (error) {
      onMessage(String(error), 'error');
    } finally {
      setWorking(false);
    }
  }

  // 单一权威来源：后端 production_mode。READY 才允许开始新的连续制作；
  // 渲染器空闲停机不影响 READY（受管渲染器按需启动）。
  const mode = system?.production_mode?.mode ?? null;
  const canStart = !demoSelected && mode === 'READY' && !activeSeries && selected.length > 0
    && selectedBusy.length === 0 && !!profileId && sizeValid && !sizeBlocked;
  const blockers = [
    ...(shots.length === 0 ? ['还没有应用分镜，制作列表为 0 镜。请先在上方完成 AI 分镜，并点击「应用为正式分镜（人工确认）」将方案加入制作列表。'] : []),
    ...(shots.length > 0 && selected.length === 0 ? ['还没有勾选镜头。请点击「选择全部可制作镜头」，或逐个勾选。'] : []),
    ...(!profileId ? ['还没有选定生成方案。若下拉框里没有方案，请到基础配置登记并验证 H3 工作流。'] : []),
    ...(sizeBlocked ? ['当前方案没有宽高映射，不能按你选的画幅渲染。到基础配置点「补齐宽高参数」，或把画幅改回「按方案默认」。'] : []),
    ...(!sizeValid ? ['自定义画幅必须是 256–2048 之间、32 的倍数（H3 规范）。'] : []),
    ...(unboundCount > 0 ? [`有 ${unboundCount} 个角色没有绑定人物参考图；提交会被拦下，避免生成一张不相干的脸。`] : []),
    ...(demoSelected ? ['当前选择演示，请先切回真实制作。'] : []),
    ...(mode !== 'READY' ? [system?.production_mode?.detail ?? '尚未连接本机服务，无法确认制作环境。'] : []),
    ...(activeSeries ? ['已有未结束的生产序列，请先查看下方进度或处理该序列。'] : []),
    ...(selectedBusy.length > 0 ? ['选中的部分镜头已经在生产中，请取消这些镜头的勾选。'] : []),
    ...(busy || working ? ['正在处理当前操作，请等待完成。'] : []),
  ];

  return <div className="card section-card series-card" id="production-series">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">04 / CONTINUOUS PRODUCTION</div>
        <h2>连续制作影片</h2>
        <p>先定成片规格，再勾选镜头点「开始制作影片」。本机会连续生成并自动整理成片；文件损坏或规格不符会暂停处理，小模型画面判断和对白识别只作建议，不会卡住后续镜头。</p>
      </div>
      <div className="section-icon plum"><Layers size={17} /></div>
    </div>

    {mode !== 'READY' && mode !== 'RUNNING' && <div className="blocked-callout">
      <AlertTriangle size={15} />
      <span>{system?.production_mode?.detail ?? '暂无法确认制作模式。'}</span>
      {system?.production_mode?.action === 'enable_real'
        && <button type="button" className="text-button" onClick={() => onModeAction('enable_real')}>启用真实制作</button>}
      {system?.production_mode?.action === 'check_fix'
        && <button type="button" className="text-button" onClick={() => onModeAction('check_fix')}>检查并修复</button>}
    </div>}

    {demoSelected && <div className="blocked-callout">
      <span>当前选择演示；连续制作只支持真实 H3，演示态不能启动。</span>
      <button type="button" className="text-button" onClick={onSelectReal}>切回真实制作</button>
    </div>}
    <div className="series-builder">
      <div className="series-profile">
        <label htmlFor="series-profile">生成方案
          <select id="series-profile" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">自动选择已验证的方案</option>
            {ref2vaProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.name}</option>
            ))}
          </select>
        </label>
        <label htmlFor="series-scope">审片建议（可选）
          <select id="series-scope" value={scope} onChange={(event) => setScope(event.target.value as 'full' | 'basic')}>
            <option value="full">完整建议：画面、对白与风格（只记录，不阻断）</option>
            <option value="basic">基础建议：人物与对白（只记录，不阻断）</option>
          </select>
        </label>
        {scope === 'basic' && <p className="series-hint">画面规格仍会做技术校验；人物与对白建议未覆盖时会留待查看，不影响制作。</p>}
        <label htmlFor="series-edit-intent">剪辑方案（可选，分镜阶段冻结）
          <select id="series-edit-intent" value={editIntentId} onChange={(event) => setEditIntentId(event.target.value)}>
            <option value="">默认剪辑（全片完整镜头、直接切换）</option>
            {editIntents.map((row) => (
              <option key={row.id} value={row.id}>剪辑方案 {row.id.slice(0, 8)}…</option>
            ))}
          </select>
        </label>
      </div>

      <div className="output-spec">
        <div className="output-spec-head">
          <b>成片规格</b>
          <span>H3 规范：24fps · 宽高为 32 的倍数 · 单镜 4–15 秒</span>
        </div>
        <div className="output-spec-grid">
          <label htmlFor="frame-preset">分辨率与纵横比
            <select id="frame-preset" value={framePreset} onChange={(event) => setFramePreset(event.target.value)}>
              {FRAME_PRESETS.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}
            </select>
          </label>
          {framePreset === 'custom' && <>
            <label htmlFor="frame-width">宽（像素）
              <input id="frame-width" type="number" min={256} max={2048} step={32} value={customWidth}
                onChange={(event) => setCustomWidth(event.target.value)} />
            </label>
            <label htmlFor="frame-height">高（像素）
              <input id="frame-height" type="number" min={256} max={2048} step={32} value={customHeight}
                onChange={(event) => setCustomHeight(event.target.value)} />
            </label>
          </>}
          <label htmlFor="clip-seconds">单镜长度
            <select id="clip-seconds" value={clipSeconds} onChange={(event) => setClipSeconds(event.target.value)}>
              {CLIP_CHOICES.map((value) => <option key={value || 'plan'} value={value}>
                {value ? `${value} 秒（统一所有选中镜头）` : '按分镜计划（推荐）'}
              </option>)}
            </select>
          </label>
        </div>
        <p className="output-spec-foot">
          {wantsSize ? `${requestedSize.width}×${requestedSize.height}` : '分辨率沿用生成方案'}
          {' · '}
          预计成片 {lengthPreview.total.toFixed(1)} 秒 / {lengthPreview.count} 镜
          {lengthPreview.unified ? '（已统一单镜长度）' : ''}
          {!profileId ? '' : sizeBlocked ? ' · 当前方案未接入宽高参数' : ' · 宽高可在此调整'}
        </p>
        {sizeBlocked && <div className="blocked-callout">
          <AlertTriangle size={15} />
          <span>当前生成方案没有接住界面的宽高设置。补齐后才会按你选的分辨率渲染。</span>
          {onGoSetup && <button type="button" className="text-button" onClick={onGoSetup}>去基础配置补齐</button>}
        </div>}
      </div>

      <details className="identity-check" open={unboundCount > 0}>
        <summary><ShieldCheck size={13} /> 角色参考对照（提交前核对，避免脸对不上）</summary>
        {identityRows.length === 0 && <p className="empty-inline">还没有可核对的镜头。</p>}
        {identityRows.map(({ shot, refs, characters, unbound }) => (
          <div className="identity-row" key={shot.id}>
            <b>{shot.name}</b>
            <span className="identity-cast">{characters.length ? characters.join('、') : '无人物'}</span>
            <span className="identity-labels">
              {refs.filter((row) => row.use_as === 'subject').map((row) => (
                <em key={row.asset_id} className="identity-chip">{row.name} → {row.label}</em>
              ))}
              {refs.filter((row) => row.use_as === 'subject').length === 0 && <em className="identity-chip muted">没有人物参考</em>}
            </span>
            {unbound.length > 0 && <span className="identity-missing">未绑定：{unbound.join('、')}</span>}
          </div>
        ))}
        {shots.length > 4 && <p className="series-hint">只列出前 4 个镜头；每个镜头提交前都会按同一规则核对。</p>}
      </details>

      <div className="series-shots">
        {shots.map((shot) => (
          <label key={shot.id} className={`series-shot ${busyShotIds.has(shot.id) ? 'is-busy' : ''}`}>
            <input
              type="checkbox"
              checked={selected.includes(shot.id)}
              disabled={busyShotIds.has(shot.id)}
              onChange={() => toggle(shot.id)}
            />
            <span className="series-shot-name">{shot.name}</span>
            <small>{label(continuityLabels, String(shot.spec.continuity?.mode ?? 'cut'))}</small>
            <small className="series-shot-length">{Number(shot.spec.duration_target_s ?? 0).toFixed(1)}s</small>
          </label>
        ))}
        {shots.length === 0 && <span className="empty-inline">先创建分镜，才能排入生产序列。</span>}
      </div>
      <div className="series-actions-row">
        <button className="secondary-button" disabled={shots.length === 0} onClick={selectAllProducible}>选择全部可制作镜头</button>
        {busyShotIds.size > 0 && <small className="series-excluded-note">已在生产中被排除 {busyShotIds.size} 镜，不重复提交。</small>}
      </div>
      <button className="primary-button" disabled={busy || working || !canStart} onClick={start}>
        {working ? <Loader2 size={14} /> : <Play size={14} />} 开始制作影片（{selected.length} 镜）
      </button>
      {blockers.length > 0 && <div className="required-action" role="status" aria-label="开始制作前还需完成">
        <AlertTriangle size={17} className="required-icon" />
        <div className="required-text">
          <b>现在为什么不能开始制作</b>
          <ul>{blockers.map(reason => <li key={reason}>{reason}</li>)}</ul>
          {shots.length === 0 && <small>生成分镜方案只是草稿；人工复核并应用后，这里才会出现可勾选的镜头。</small>}
        </div>
        {shots.length === 0 && <button type="button" className="must-button"
          onClick={() => document.getElementById('storyboard')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          去完成并应用分镜
        </button>}
        {!profileId && <button type="button" className="text-button" onClick={() => onModeAction('check_fix')}>检查生成方案</button>}
      </div>}
    </div>

    {series.length > 0 && <div className="series-list">
      {series.map((row) => (
        <article key={row.id} className={`series-block ${row.status.toLowerCase()}`}>
          <div className="series-head">
            <div>
              <b>{statusText[row.status] ?? row.status}</b>
              <span>{row.completed_count}/{row.shot_count} 镜完成 · {row.id.slice(0, 8)}…</span>
              {row.output_spec?.width && row.output_spec?.height
                && <small>规格：{row.output_spec.width}×{row.output_spec.height}
                  {row.output_spec.clip_seconds ? ` · 单镜 ${row.output_spec.clip_seconds}s` : ''}</small>}
              {row.worker_id && <small>执行：本机制作服务 {row.worker_id.slice(0, 12)}</small>}
              {row.failure_code && <small className="failure">· {label(failureLabels, row.failure_code)}</small>}
              {row.finalization_state !== 'NONE' && <small>收尾：{label(finalizationLabels, row.finalization_state)}</small>}
              {row.delivery_result && <small className="delivered">· 自动交付 v{row.delivery_result.version}（{row.delivery_result.review_state === 'all_passed' ? '来源审查通过' : '待审样片'}）</small>}
            </div>
            <div className="series-actions">
              {row.status === 'QUEUED' && <button className="secondary-button" disabled={working} onClick={() => act(row.id, 'pause')}>暂停</button>}
              {['PAUSED', 'PAUSE_REQUESTED'].includes(row.status) && <button className="secondary-button" disabled={working} onClick={() => act(row.id, 'resume')}>恢复</button>}
              {['QUEUED', 'PAUSED', 'PAUSE_REQUESTED', 'NEEDS_REVIEW'].includes(row.status)
                && <button className="subtle-button" disabled={working} onClick={() => act(row.id, 'cancel')}><Square size={12} /> 取消</button>}
            </div>
          </div>
          <ol className="series-items">
            {row.items.map((item) => (
              <li key={item.id}>
                <Clapperboard size={12} />
                <span>{shots.find((shot) => shot.id === item.shot_id)?.name ?? item.shot_id.slice(0, 8)}</span>
                <em className={`series-item-status ${item.status.toLowerCase()}`}>{itemText[item.status] ?? item.status}</em>
                {item.job_status && <small>{label(statusLabels, item.job_status)}</small>}
                {item.job_artifact_id && <small>已出片</small>}
                {item.continuity_mode !== 'cut' && <small>{label(continuityLabels, item.continuity_mode)}</small>}
                {item.job_failure_code && <small className="failure">{label(failureLabels, item.job_failure_code)}</small>}
              </li>
            ))}
          </ol>
        </article>
      ))}
    </div>}
  </div>;
}
