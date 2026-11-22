import { useCallback, useEffect, useRef, useState } from 'react';
import { AssetCenterPicker } from '../../shared/AssetCenterPicker';
import {
  adoptM4LibraryVersion,
  batchM4Requirements,
  declineM4Material,
  fetchM4Materials,
  fetchM4MaterialTask,
  promoteM4Asset,
  putM4ShotBindings,
  searchM4Library,
  selectOrGenerateM4Material,
  uploadM4Asset,
  type M4AssetVersion,
  type M4LibraryItem,
  type M4MaterialBatchAction,
  type M4MaterialRequirement,
  type M4MaterialsState,
  type M4Overview,
  type M4StageSummary,
} from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onNextStage: () => void;
  onMessage?: (msg: string, kind?: 'success' | 'error') => void;
};

const FAMILY_LABELS: Record<string, string> = {
  character: '人物',
  scene: '场景',
  prop: '道具',
  voice: '音色',
  reference_video: '视频参考',
};

export function MaterialsStagePage({ overview, stage, onRefresh, onNextStage, onMessage }: Props) {
  const projectId = overview.project.id;
  const [state, setState] = useState<M4MaterialsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [promptsByReq, setPromptsByReq] = useState<Record<string, string>>({});
  const [reuseByReq, setReuseByReq] = useState<Record<string, string>>({});
  const [selectedReqIds, setSelectedReqIds] = useState<string[]>([]);
  const [batchAction, setBatchAction] = useState<M4MaterialBatchAction>('generate');

  // Standalone asset upload state (e.g. extra voice or shot-level Ref2VA upload)
  const [extraFamily, setExtraFamily] = useState('character');
  const [extraName, setExtraName] = useState('');
  const [extraFile, setExtraFile] = useState<File | null>(null);

  // Public Asset Center cross-pipeline search & adopt state
  const [libQuery, setLibQuery] = useState('');
  const [libItems, setLibItems] = useState<M4LibraryItem[]>([]);
  const [libTargetReq, setLibTargetReq] = useState('');
  const [libBusy, setLibBusy] = useState(false);
  /** 「从素材中心导入」弹窗当前服务的需求；点选后走既有 adoptM4LibraryVersion 执行者。 */
  const [centerPickerReq, setCenterPickerReq] = useState<M4MaterialRequirement | null>(null);
  const uploadInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, lib] = await Promise.all([
        fetchM4Materials(projectId),
        searchM4Library(projectId).catch(() => ({ items: [] as M4LibraryItem[], total: 0 })),
      ]);
      setState(data);
      setLibItems(lib.items ?? []);
    } catch (err) {
      onMessage?.(`读取四阶素材状态失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId, onMessage]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyNewState = useCallback(
    async (nextState: M4MaterialsState) => {
      setState(nextState);
      await onRefresh();
    },
    [onRefresh],
  );

  const pendingTaskIds = (state?.requirements ?? [])
    .map((r) => r.pending_task_id)
    .filter((id): id is string => Boolean(id));

  useEffect(() => {
    if (pendingTaskIds.length === 0) return;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          let settled = false;
          for (const taskId of pendingTaskIds) {
            const res = await fetchM4MaterialTask(projectId, taskId);
            if (res.status === 'CANDIDATE_READY' || res.status === 'FAILED') {
              settled = true;
            }
          }
          const latest = await fetchM4Materials(projectId);
          setState(latest);
          if (settled) {
            await onRefresh();
          }
        } catch {
          // Next poll tick retries quietly
        }
      })();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [projectId, pendingTaskIds.join(','), onRefresh]);

  const handleGenerate = async (reqId: string) => {
    setBusyKey(`gen:${reqId}`);
    try {
      const res = await selectOrGenerateM4Material(projectId, reqId, {
        mode: 'generate',
        prompt: promptsByReq[reqId] ?? '',
      });
      await applyNewState(res.state);
      if (res.status === 'QUEUED' || res.task_id) {
        onMessage?.('已加入公共素材中心 Qwen 生图队列，完成后自动刷新候选图。', 'success');
      } else {
        onMessage?.('已生成候选参考图，请核对后晋升为主素材。', 'success');
      }
    } catch (err) {
      onMessage?.(`生成失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleReuse = async (reqId: string) => {
    const assetId = reuseByReq[reqId];
    if (!assetId) return;
    setBusyKey(`reuse:${reqId}`);
    try {
      const res = await selectOrGenerateM4Material(projectId, reqId, {
        mode: 'reuse',
        asset_version_id: assetId,
      });
      await applyNewState(res.state);
      onMessage?.('已复用并绑定已有主素材。', 'success');
    } catch (err) {
      onMessage?.(`复用失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleDecline = async (reqId: string) => {
    setBusyKey(`decline:${reqId}`);
    try {
      const res = await declineM4Material(projectId, reqId, '用户明确跳过该素材需求');
      await applyNewState(res.state);
      onMessage?.('已标记明确放弃该素材需求；缺少参考的镜头仍会被 Ref2VA 门禁单独提示。', 'success');
    } catch (err) {
      onMessage?.(`操作失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleUploadForReq = async (
    req: { id: string; family: string; entity_name: string; variant_name: string },
    file: File | null,
  ) => {
    if (!file) return;
    setBusyKey(`upload:${req.id}`);
    try {
      const res = await uploadM4Asset(projectId, {
        family: req.family,
        entity_name: req.entity_name,
        variant_name: req.variant_name,
        requirement_id: req.id,
        file,
      });
      await applyNewState(res.state);
      onMessage?.(`已上传候选素材「${res.asset.filename}」，请点击晋升启用。`, 'success');
    } catch (err) {
      onMessage?.(`上传失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleStandaloneUpload = async () => {
    if (!extraFile || !extraName.trim()) {
      onMessage?.('请填写实体名称并选择素材文件。', 'error');
      return;
    }
    setBusyKey('upload:standalone');
    try {
      const res = await uploadM4Asset(projectId, {
        family: extraFamily,
        entity_name: extraName.trim(),
        file: extraFile,
      });
      setExtraName('');
      setExtraFile(null);
      await applyNewState(res.state);
      onMessage?.(`已上传独立候选素材「${res.asset.filename}」，请晋升后绑定至镜头。`, 'success');
    } catch (err) {
      onMessage?.(`上传失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handlePromote = async (assetId: string, requirementId?: string) => {
    setBusyKey(`promote:${assetId}`);
    try {
      const res = await promoteM4Asset(projectId, assetId, requirementId);
      await applyNewState(res.state);
      onMessage?.('已晋升为主素材并更新镜头绑定。', 'success');
    } catch (err) {
      onMessage?.(`晋升失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleAddShotBinding = async (shotId: string, existing: Array<{ asset_version_id: string; use_as: string; role_label: string }>, assetId: string) => {
    if (!assetId) return;
    setBusyKey(`bind:${shotId}`);
    try {
      const next = [
        ...existing.map((b) => ({ asset_version_id: b.asset_version_id, use_as: b.use_as, role_label: b.role_label })),
        { asset_version_id: assetId },
      ];
      const res = await putM4ShotBindings(projectId, shotId, next);
      await applyNewState(res.state);
      onMessage?.('已绑定参考素材到镜头。', 'success');
    } catch (err) {
      onMessage?.(`绑定失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleRemoveShotBinding = async (
    shotId: string,
    existing: Array<{ asset_version_id: string; use_as: string; role_label: string }>,
    removeAssetId: string,
  ) => {
    setBusyKey(`unbind:${shotId}:${removeAssetId}`);
    try {
      const next = existing
        .filter((b) => b.asset_version_id !== removeAssetId)
        .map((b) => ({ asset_version_id: b.asset_version_id, use_as: b.use_as, role_label: b.role_label }));
      const res = await putM4ShotBindings(projectId, shotId, next);
      await applyNewState(res.state);
      onMessage?.('已解除镜头素材绑定。', 'success');
    } catch (err) {
      onMessage?.(`解绑失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleSearchLibrary = async () => {
    setLibBusy(true);
    try {
      const res = await searchM4Library(projectId, { query: libQuery.trim() || undefined });
      setLibItems(res.items ?? []);
    } catch (err) {
      onMessage?.(`检索公共素材库失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setLibBusy(false);
    }
  };

  const handleAdoptLibrary = async (item: M4LibraryItem) => {
    setBusyKey(`adopt:${item.library_version_id}`);
    try {
      const res = await adoptM4LibraryVersion(projectId, {
        version_id: item.library_version_id,
        requirement_id: libTargetReq || undefined,
      });
      await applyNewState(res.state);
      onMessage?.(`已从公共素材中心采纳「${item.filename}」为四阶主素材。`, 'success');
    } catch (err) {
      onMessage?.(`采纳失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handlePickFromCenter = async (item: M4LibraryItem) => {
    const req = centerPickerReq;
    setCenterPickerReq(null);
    if (!req) return;
    setBusyKey(`adopt:${item.library_version_id}`);
    try {
      const res = await adoptM4LibraryVersion(projectId, {
        version_id: item.library_version_id,
        requirement_id: req.id,
        family: req.family,
      });
      await applyNewState(res.state);
      onMessage?.(`已从素材中心导入「${item.title}」并绑定到该素材需求。`, 'success');
    } catch (err) {
      onMessage?.(`导入失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const facts = stage?.facts ?? {};
  const requirements = state?.requirements ?? [];
  const validSelectedIds = selectedReqIds.filter((id) => requirements.some((r) => r.id === id));
  const allSelected = requirements.length > 0 && validSelectedIds.length === requirements.length;
  const promotedAssets: M4AssetVersion[] = (state?.assets ?? []).filter((a) => a.status === 'promoted');
  const shotCodeMap = new Map((state?.shots ?? []).map((s) => [s.id, s.shot_code]));
  const shotsTotal = Number(facts.shots_total ?? state?.shots.length ?? 0);
  const shotsReady = Number(facts.shots_ready ?? (state?.shots ?? []).filter((s) => s.ref2va_ready).length);

  const toggleSelectReq = (reqId: string, checked: boolean) => {
    setSelectedReqIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(reqId);
      else next.delete(reqId);
      return Array.from(next);
    });
  };

  const toggleSelectAll = (checked: boolean) => {
    setSelectedReqIds(checked ? requirements.map((r) => r.id) : []);
  };

  const handleApplyBatch = async () => {
    if (validSelectedIds.length === 0) return;
    setBusyKey(`batch:${batchAction}`);
    try {
      const res = await batchM4Requirements(projectId, {
        action: batchAction,
        requirement_ids: validSelectedIds,
        prompts_by_req: batchAction === 'generate' ? promptsByReq : undefined,
      });
      await applyNewState(res.state);
      const parts: string[] = [];
      if (res.applied_ids.length > 0) parts.push(`已处理 ${res.applied_ids.length} 项`);
      if (res.skipped.length > 0) parts.push(`跳过 ${res.skipped.length} 项（${res.skipped[0]?.reason ?? ''}）`);
      if (res.errors.length > 0) parts.push(`失败 ${res.errors.length} 项（${res.errors[0]?.message ?? ''}）`);
      if (res.errors.length > 0 && res.applied_ids.length === 0) {
        onMessage?.(`批量操作未完成：${parts.join('；')}`, 'error');
      } else {
        setSelectedReqIds([]);
        onMessage?.(`批量操作完成：${parts.join('，') || '已完成'}。`, 'success');
      }
    } catch (err) {
      onMessage?.(`批量操作失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className="m4-panel" aria-label="素材生成">
      <div className="m4-panel-head">
        <div>
          <h2>② 素材生成</h2>
          <p className="m4-panel-desc">
            按①已冻结分镜归并人物 / 场景 / 道具需求；自主制片模式仅要求每一镜具备必需 Ref2VA 参考素材，未选用的需求项不硬阻滞排片。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          {shotsTotal > 0
            ? `就绪镜头 ${shotsReady} / ${shotsTotal} · 需求已备 ${String(facts.requirements_ready ?? 0)} / ${String(facts.requirements_total ?? requirements.length)}`
            : `${String(facts.requirements_ready ?? 0)} / ${String(facts.requirements_total ?? requirements.length)} 项已就绪`}
        </span>
      </div>

      {stage && stage.blockers.length > 0 && (
        <div className="m4-blocker-box" role="status">
          {stage.blockers.map((b, idx) => (
            <div key={`${b.code}-${idx}`}>• {b.message}</div>
          ))}
        </div>
      )}

      {loading && !state ? (
        <div className="m4-empty-box">正在加载四阶素材清单...</div>
      ) : (
        <>
          <div className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>归并素材需求（{requirements.length} 项）</strong>
              <span className="m4-footer-note">
                已晋升主素材：{promotedAssets.length} · 就绪镜头：{shotsReady} / {shotsTotal}
              </span>
            </div>

            {requirements.length === 0 ? (
              <div className="m4-empty-box">
                当前分镜未抽出命名人物/场景/道具需求，可在下方手动上传主素材并绑定至各镜头。
              </div>
            ) : (
              <>
                <div className="m4-batch-bar" role="region" aria-label="归并素材批量操作">
                  <label className="m4-req-check">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      disabled={busyKey !== null}
                      aria-label="全选归并素材需求"
                      onChange={(e) => toggleSelectAll(e.target.checked)}
                    />
                    全选
                  </label>
                  <span className="m4-footer-note">已选 {validSelectedIds.length} 项</span>
                  <select
                    className="m4-input"
                    style={{ maxWidth: 220 }}
                    aria-label="批量操作"
                    value={batchAction}
                    disabled={busyKey !== null}
                    onChange={(e) => setBatchAction(e.target.value as M4MaterialBatchAction)}
                  >
                    <option value="generate">Qwen 批量生成（图片类）</option>
                    <option value="promote_latest">批量晋升最新候选为主素材</option>
                    <option value="decline">批量明确放弃</option>
                  </select>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busyKey !== null || validSelectedIds.length === 0}
                    onClick={() => void handleApplyBatch()}
                  >
                    {busyKey?.startsWith('batch:') ? '批量执行中...' : '应用到所选'}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busyKey !== null || validSelectedIds.length === 0}
                    onClick={() => setSelectedReqIds([])}
                  >
                    清空选择
                  </button>
                </div>

                <div className="m4-mat-grid">
                  {requirements.map((req) => {
                    const isChecked = validSelectedIds.includes(req.id);
                    const statusLabel = req.user_declined
                      ? '已放弃'
                      : req.status === 'bound' || req.status === 'promoted'
                        ? req.status
                        : req.status === 'candidate'
                          ? '待晋升'
                          : '待备料（不阻滞）';
                    return (
                    <div key={req.id} className={`m4-mat-card ${isChecked ? 'selected' : ''}`}>
                      <div className="m4-ep-head">
                        <div className="m4-req-title-row">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={busyKey !== null}
                            aria-label={`选择需求 ${req.entity_name}`}
                            onChange={(e) => toggleSelectReq(req.id, e.target.checked)}
                          />
                          <div>
                            <strong>
                              [{FAMILY_LABELS[req.family] ?? req.family}] {req.entity_name}
                            </strong>
                            {req.variant_name && <span className="m4-footer-note"> · {req.variant_name}</span>}
                          </div>
                        </div>
                        <span
                          className={`m4-badge ${
                            req.status === 'bound' || req.status === 'promoted'
                              ? 'ok'
                              : req.user_declined
                                ? ''
                                : 'warn'
                          }`}
                        >
                          {statusLabel}
                        </span>
                      </div>

                    <div className="m4-footer-note">
                      关联镜头：{req.shot_ids.map((id) => shotCodeMap.get(id) ?? id.slice(0, 6)).join('、') || '无'}
                    </div>

                    {req.bound_asset && (
                      <div className="m4-bound-preview">
                        {req.bound_asset.content_type.startsWith('image/') && (
                          <img src={req.bound_asset.media_url} alt={req.entity_name} className="m4-thumb" />
                        )}
                        <div className="m4-footer-note">
                          已绑定主素材：{req.bound_asset.filename}
                          {req.bound_asset.is_mock ? '（Mock）' : ''}
                          {req.bound_asset.rights_status ? ` · 权属:${req.bound_asset.rights_status}` : ''}
                        </div>
                      </div>
                    )}

                    {req.pending_task_id && (
                      <div className="m4-footer-note">
                        公共素材中心生图任务 {req.pending_task_id.slice(0, 8)}… · 状态：{req.pending_task?.status ?? 'QUEUED'}
                      </div>
                    )}

                    <div className="m4-toolbar">
                      <input
                        className="m4-input"
                        placeholder="可选：Qwen-Image-2.1 提示词"
                        value={promptsByReq[req.id] ?? ''}
                        onChange={(e) => setPromptsByReq((prev) => ({ ...prev, [req.id]: e.target.value }))}
                      />
                    </div>

                    <div className="m4-toolbar">
                      <label className="m4-source-field">素材来源
                        <select
                          className="m4-input"
                          value=""
                          disabled={busyKey !== null}
                          onChange={(e) => {
                            const v = e.target.value;
                            e.target.value = '';
                            if (v === 'import_center') setCenterPickerReq(req);
                            else if (v === 'generate') void handleGenerate(req.id);
                            else if (v === 'decline') void handleDecline(req.id);
                            else if (v === 'upload') uploadInputRefs.current[req.id]?.click();
                          }}
                        >
                          <option value="" disabled>选择来源…</option>
                          <option value="import_center">从素材中心导入…</option>
                          <option value="generate">Qwen 生成</option>
                          <option value="upload">本地上传</option>
                          <option value="decline">明确放弃</option>
                        </select>
                      </label>
                      <input
                        type="file"
                        hidden
                        accept="image/*,audio/*,video/*"
                        ref={(el) => { uploadInputRefs.current[req.id] = el; }}
                        onChange={(e) => { void handleUploadForReq(req, e.target.files?.[0] ?? null); e.target.value = ''; }}
                      />
                    </div>

                    <div className="m4-toolbar">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={
                          busyKey !== null ||
                          Boolean(req.pending_task_id) ||
                          req.family === 'voice' ||
                          req.family === 'reference_video'
                        }
                        onClick={() => void handleGenerate(req.id)}
                      >
                        {busyKey === `gen:${req.id}`
                          ? '提交中...'
                          : req.pending_task_id
                            ? '排队生成中...'
                            : 'Qwen 生成'}
                      </button>
                      <label className="btn-secondary m4-file-btn">
                        本地上传
                        <input
                          type="file"
                          accept="image/*,audio/*,video/*"
                          hidden
                          onChange={(e) => void handleUploadForReq(req, e.target.files?.[0] ?? null)}
                        />
                      </label>
                      {req.reusable_assets.length > 0 && (
                        <>
                          <select
                            className="m4-input"
                            style={{ maxWidth: 180 }}
                            value={reuseByReq[req.id] ?? ''}
                            onChange={(e) => setReuseByReq((prev) => ({ ...prev, [req.id]: e.target.value }))}
                          >
                            <option value="">选择可复用主素材…</option>
                            {req.reusable_assets.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.entity_name} ({a.filename})
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busyKey !== null || !reuseByReq[req.id]}
                            onClick={() => void handleReuse(req.id)}
                          >
                            复用绑定
                          </button>
                        </>
                      )}
                      {!req.user_declined && (
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleDecline(req.id)}
                        >
                          明确放弃
                        </button>
                      )}
                    </div>

                    {req.candidates.length > 0 && (
                      <div className="m4-cand-list">
                        {req.candidates.map((cand) => (
                          <div key={cand.id} className="m4-cand-row">
                            {cand.content_type.startsWith('image/') && (
                              <img src={cand.media_url} alt={cand.filename} className="m4-thumb-sm" />
                            )}
                            <span className="m4-footer-note">
                              {cand.filename} ({cand.status}
                              {cand.is_mock ? ', Mock' : ''})
                            </span>
                            {cand.status !== 'promoted' && (
                              <button
                                type="button"
                                className="btn-primary"
                                disabled={busyKey !== null}
                                onClick={() => void handlePromote(cand.id, req.id)}
                              >
                                晋升为主素材
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>

          <div className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>独立上传参考素材（补充参考图 / 参考视频 / 音色）</strong>
            </div>
            <div className="m4-toolbar">
              <select
                className="m4-input"
                style={{ maxWidth: 130 }}
                value={extraFamily}
                onChange={(e) => setExtraFamily(e.target.value)}
              >
                <option value="character">人物图</option>
                <option value="scene">场景图</option>
                <option value="prop">道具图</option>
                <option value="voice">音色</option>
                <option value="reference_video">视频参考</option>
              </select>
              <input
                className="m4-input"
                style={{ maxWidth: 220 }}
                placeholder="实体名称（如：林青 / 雨巷音色）"
                value={extraName}
                onChange={(e) => setExtraName(e.target.value)}
              />
              <input
                type="file"
                accept="image/*,audio/*,video/*"
                onChange={(e) => setExtraFile(e.target.files?.[0] ?? null)}
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={busyKey !== null || !extraFile || !extraName.trim()}
                onClick={() => void handleStandaloneUpload()}
              >
                上传候选
              </button>
            </div>

            {(state?.assets ?? []).filter((a) => !a.requirement_id).length > 0 && (
              <div className="m4-cand-list">
                {(state?.assets ?? [])
                  .filter((a) => !a.requirement_id)
                  .map((a) => (
                    <div key={a.id} className="m4-cand-row">
                      <span className="m4-footer-note">
                        [{FAMILY_LABELS[a.family] ?? a.family}] {a.entity_name} · {a.filename} ({a.status})
                      </span>
                      {a.status !== 'promoted' && (
                        <button
                          type="button"
                          className="btn-primary"
                          disabled={busyKey !== null}
                          onClick={() => void handlePromote(a.id)}
                        >
                          晋升为主素材
                        </button>
                      )}
                    </div>
                  ))}
              </div>
            )}
          </div>

          <div className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>公共素材中心检索与跨线复用</strong>
              <span className="m4-footer-note">支持检索全局素材库已登记/授权资产并采纳为四阶主素材</span>
            </div>
            <div className="m4-toolbar">
              <input
                className="m4-input"
                style={{ maxWidth: 220 }}
                placeholder="按实体名/文件名检索公共库…"
                value={libQuery}
                onChange={(e) => setLibQuery(e.target.value)}
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={libBusy}
                onClick={() => void handleSearchLibrary()}
              >
                {libBusy ? '检索中...' : '检索公共库'}
              </button>
              {(state?.requirements ?? []).length > 0 && (
                <select
                  className="m4-input"
                  style={{ maxWidth: 220 }}
                  value={libTargetReq}
                  onChange={(e) => setLibTargetReq(e.target.value)}
                >
                  <option value="">仅采纳至本作品主素材库…</option>
                  {(state?.requirements ?? []).map((req) => (
                    <option key={req.id} value={req.id}>
                      并满足需求：[{FAMILY_LABELS[req.family] ?? req.family}] {req.entity_name}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {libItems.length === 0 ? (
              <div className="m4-footer-note" style={{ marginTop: 8 }}>
                当前检索条件下暂无可复用公共库版本（生成或上传后会自动同步入库）。
              </div>
            ) : (
              <div className="m4-cand-list" style={{ marginTop: 8 }}>
                {libItems.slice(0, 8).map((item) => (
                  <div key={item.library_version_id} className="m4-cand-row">
                    {item.media_kind === 'image' && (
                      <img src={item.media_url} alt={item.filename} className="m4-thumb-sm" />
                    )}
                    <span className="m4-footer-note">
                      {item.title || item.filename} ({item.media_kind} · {item.rights_status}
                      {item.project_authorized ? ' · 已授权' : ''})
                    </span>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busyKey !== null}
                      onClick={() => void handleAdoptLibrary(item)}
                    >
                      {busyKey === `adopt:${item.library_version_id}` ? '采纳中...' : '采纳为主素材'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>逐镜 Ref2VA 参考绑定检查</strong>
            </div>
            <div className="m4-table-wrap">
              <table className="m4-table">
                <thead>
                  <tr>
                    <th>镜号</th>
                    <th>
                      已绑定参考（最多 {state?.slot_limits?.image ?? 6} 图 + {state?.slot_limits?.audio ?? 2} 音频 +{' '}
                      {state?.slot_limits?.video ?? 2} 视频）
                    </th>
                    <th>添加主素材</th>
                    <th>Ref2VA 状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(state?.shots ?? []).map((sh) => (
                    <tr key={sh.id}>
                      <td>{sh.shot_code}</td>
                      <td>
                        <div className="m4-toolbar">
                          {sh.bindings.length === 0 ? (
                            <span className="m4-badge warn">缺少参考素材</span>
                          ) : (
                            sh.bindings.map((b) => (
                              <span key={b.id} className="m4-badge ok">
                                {b.role_label || b.asset?.entity_name} ({b.use_as})
                                <button
                                  type="button"
                                  className="m4-chip-x"
                                  aria-label={`移除 ${b.role_label}`}
                                  onClick={() => void handleRemoveShotBinding(sh.id, sh.bindings, b.asset_version_id)}
                                >
                                  ×
                                </button>
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td>
                        <select
                          className="m4-input"
                          value=""
                          disabled={busyKey !== null || promotedAssets.length === 0}
                          onChange={(e) => {
                            if (e.target.value) void handleAddShotBinding(sh.id, sh.bindings, e.target.value);
                          }}
                        >
                          <option value="">＋ 绑定已晋升主素材…</option>
                          {promotedAssets
                            .filter((a) => !sh.bindings.some((b) => b.asset_version_id === a.id))
                            .map((a) => (
                              <option key={a.id} value={a.id}>
                                [{FAMILY_LABELS[a.family] ?? a.family}] {a.entity_name} ({a.filename})
                              </option>
                            ))}
                        </select>
                      </td>
                      <td>
                        <span className={`m4-badge ${sh.ref2va_ready ? 'ok' : 'warn'}`}>
                          {sh.ref2va_ready ? 'Ref2VA 就绪' : sh.ref2va_blocker?.message ?? '缺参考阻滞'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <div className="m4-footer-bar">
        <span className="m4-footer-note">所有素材版本、晋升授权与镜头绑定独立存于四阶库，严禁无参考出片。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done} onClick={onNextStage}>
          下一步：排片生产
        </button>
      </div>
      {centerPickerReq && <AssetCenterPicker
        title={`从素材中心导入 · ${centerPickerReq.entity_name}`}
        kicker="素材来源"
        emptyHint="素材中心还没有可分配的已晋升素材。先在素材中心生成、审核并晋升，再回来导入。"
        fetchItems={async (q) => (await searchM4Library(projectId, { query: q })).items}
        display={(item) => ({
          key: item.library_version_id,
          title: item.title,
          subtitle: `${item.content_sha256.slice(0, 8)} · ${item.category}`,
          mediaUrl: item.media_url,
        })}
        onPick={(item) => void handlePickFromCenter(item)}
        onClose={() => setCenterPickerReq(null)}
      />}
    </section>
  );
}
