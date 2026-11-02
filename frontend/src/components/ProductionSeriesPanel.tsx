import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Clapperboard, Layers, Play, Square, Loader2 } from 'lucide-react';
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

export default function ProductionSeriesPanel({
  projectId, shots, workflowProfiles, system, busy, reviewWidth, reviewHeight,
  onStarted, onMessage, onModeAction, demoSelected = false, onSelectReal,
}: Props) {
  const [series, setSeries] = useState<ProductionSeries[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [profileId, setProfileId] = useState('');
  const [scope, setScope] = useState<'full' | 'basic'>('full');
  const [editIntents, setEditIntents] = useState<Array<{ id: string; created_at: string }>>([]);
  const [editIntentId, setEditIntentId] = useState('');
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
    if (!profileId) { onMessage('当前没有可用的生成方案，请先在制作环境中完成登记。', 'error'); return; }
    if (selected.length === 0) { onMessage('请至少选择一个镜头。', 'error'); return; }
    if (selectedBusy.length > 0) {
      onMessage('选中的镜头已经在生产队列中，请勿重复提交。', 'error');
      return;
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
      if (editIntentId) {
        body.edit_intent_artifact_id = editIntentId;
      }
      await api(`/projects/${projectId}/production-series`, json('POST', body));
      setSelected([]);
      onMessage('已开始连续制作；本机会依次生成镜头并自动整理成片。小模型审片和对白识别只提供建议。', 'success');
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
    && selectedBusy.length === 0 && !!profileId;

  return <div className="card section-card series-card" id="production-series">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">04 / CONTINUOUS PRODUCTION</div>
        <h2>连续制作影片</h2>
        <p>勾选镜头后点「开始制作影片」。本机会连续生成并自动整理成片；文件损坏或规格不符会暂停处理，小模型画面判断和对白识别只作建议，不会卡住后续镜头。</p>
      </div>
      <div className="section-icon plum"><Layers size={17} /></div>
    </div>

    {mode !== 'READY' && mode !== 'RUNNING' && <div className="blocked-callout">
      <AlertTriangle size={15} />
      <span>{system?.production_mode?.detail ?? '本机服务未连接，无法确认制作模式。'}</span>
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
            <option value="full">完整建议：画面、对白与风格</option>
            <option value="basic">基础建议：人物与对白</option>
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
    </div>

    {series.length > 0 && <div className="series-list">
      {series.map((row) => (
        <article key={row.id} className={`series-block ${row.status.toLowerCase()}`}>
          <div className="series-head">
            <div>
              <b>{statusText[row.status] ?? row.status}</b>
              <span>{row.completed_count}/{row.shot_count} 镜完成 · {row.id.slice(0, 8)}…</span>
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
