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
  reviewWidth: string;
  reviewHeight: string;
  onStarted: () => void;
  onMessage: (message: string, kind?: 'success' | 'error') => void;
};

const statusText: Record<string, string> = {
  QUEUED: '等待 Worker 接管',
  RUNNING: '生产中',
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
  onStarted, onMessage,
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
    if (!profileId) { onMessage('请先选择已注册的 H3 Ref2VA 工作流 Profile。', 'error'); return; }
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
      onMessage('生产序列已提交；常驻 Worker 会自动依次完成全部镜头。', 'success');
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

  const canStart = system?.render_mode === 'comfyui' && !activeSeries && selected.length > 0
    && selectedBusy.length === 0 && !!profileId;

  return <div className="card section-card series-card" id="production-series">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">04 / PRODUCTION SERIES</div>
        <h2>多镜头无人值守生产</h2>
        <p>按顺序排入镜头后由常驻 Worker 自动连续生产：同一台受管渲染器贯穿全程，独占 GPU 租约在镜头之间续接，序列结束后才释放。</p>
      </div>
      <div className="section-icon plum"><Layers size={17} /></div>
    </div>

    {system?.render_mode !== 'comfyui' && <div className="blocked-callout">
      <AlertTriangle size={15} /><span>当前是演示模式：这里只能演练排队和调度，不会生成真实视频。真实生产需要本机 H3 渲染器在线。</span>
    </div>}

    <div className="series-builder">
      <div className="series-profile">
        <label htmlFor="series-profile">H3 工作流 Profile
          <select id="series-profile" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">选择已注册的 Ref2VA Profile</option>
            {ref2vaProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.name} · {profile.mode}</option>
            ))}
          </select>
        </label>
        <label htmlFor="series-scope">质量门禁
          <select id="series-scope" value={scope} onChange={(event) => setScope(event.target.value as 'full' | 'basic')}>
            <option value="full">完整门禁：技术 + 语义 + 对白 + 风格 + 最终</option>
            <option value="basic">基础自审：分辨率、人物、对白</option>
          </select>
        </label>
        {scope === 'basic' && <p className="series-hint">基础自审需填写预期输出宽高，并要求已资格化的本地人物审片能力。</p>}
        <label htmlFor="series-edit-intent">剪辑意图（可选，冻结于分镜阶段）
          <select id="series-edit-intent" value={editIntentId} onChange={(event) => setEditIntentId(event.target.value)}>
            <option value="">默认剪辑规则（全片完整镜头、CUT 硬切）</option>
            {editIntents.map((row) => (
              <option key={row.id} value={row.id}>剪辑意图 {row.id.slice(0, 8)}…</option>
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
        {working ? <Loader2 size={14} /> : <Play size={14} />} 开始真实生产（{selected.length} 镜）
      </button>
    </div>

    {series.length > 0 && <div className="series-list">
      {series.map((row) => (
        <article key={row.id} className={`series-block ${row.status.toLowerCase()}`}>
          <div className="series-head">
            <div>
              <b>{statusText[row.status] ?? row.status}</b>
              <span>{row.completed_count}/{row.shot_count} 镜完成 · {row.id.slice(0, 8)}…</span>
              {row.worker_id && <small>执行 Worker：{row.worker_id}</small>}
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
