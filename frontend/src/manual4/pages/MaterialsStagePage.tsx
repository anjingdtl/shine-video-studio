import { useCallback, useEffect, useState } from 'react';
import {
  declineM4Material,
  fetchM4Materials,
  promoteM4Asset,
  putM4ShotBindings,
  selectOrGenerateM4Material,
  uploadM4Asset,
  type M4AssetVersion,
  type M4MaterialsState,
  type M4Overview,
  type M4StageSummary,
} from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onNextStage: () => void;
  onMessage?: (msg: string) => void;
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

  // Standalone asset upload state (e.g. extra voice or shot-level Ref2VA upload)
  const [extraFamily, setExtraFamily] = useState('character');
  const [extraName, setExtraName] = useState('');
  const [extraFile, setExtraFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchM4Materials(projectId);
      setState(data);
    } catch (err) {
      onMessage?.(`读取四阶素材状态失败：${err instanceof Error ? err.message : String(err)}`);
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

  const handleGenerate = async (reqId: string) => {
    setBusyKey(`gen:${reqId}`);
    try {
      const res = await selectOrGenerateM4Material(projectId, reqId, {
        mode: 'generate',
        prompt: promptsByReq[reqId] ?? '',
      });
      await applyNewState(res.state);
      onMessage?.('已生成候选参考图，请核对后晋升为主素材。');
    } catch (err) {
      onMessage?.(`生成失败：${err instanceof Error ? err.message : String(err)}`);
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
      onMessage?.('已复用并绑定已有主素材。');
    } catch (err) {
      onMessage?.(`复用失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleDecline = async (reqId: string) => {
    setBusyKey(`decline:${reqId}`);
    try {
      const res = await declineM4Material(projectId, reqId, '用户明确跳过该素材需求');
      await applyNewState(res.state);
      onMessage?.('已标记明确放弃该素材需求；缺少参考的镜头仍会被 Ref2VA 门禁单独提示。');
    } catch (err) {
      onMessage?.(`操作失败：${err instanceof Error ? err.message : String(err)}`);
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
      onMessage?.(`已上传候选素材「${res.asset.filename}」，请点击晋升启用。`);
    } catch (err) {
      onMessage?.(`上传失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleStandaloneUpload = async () => {
    if (!extraFile || !extraName.trim()) {
      onMessage?.('请填写实体名称并选择素材文件。');
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
      onMessage?.(`已上传独立候选素材「${res.asset.filename}」，请晋升后绑定至镜头。`);
    } catch (err) {
      onMessage?.(`上传失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handlePromote = async (assetId: string, requirementId?: string) => {
    setBusyKey(`promote:${assetId}`);
    try {
      const res = await promoteM4Asset(projectId, assetId, requirementId);
      await applyNewState(res.state);
      onMessage?.('已晋升为主素材并更新镜头绑定。');
    } catch (err) {
      onMessage?.(`晋升失败：${err instanceof Error ? err.message : String(err)}`);
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
      onMessage?.('已绑定参考素材到镜头。');
    } catch (err) {
      onMessage?.(`绑定失败：${err instanceof Error ? err.message : String(err)}`);
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
      onMessage?.('已解除镜头素材绑定。');
    } catch (err) {
      onMessage?.(`解绑失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const facts = stage?.facts ?? {};
  const promotedAssets: M4AssetVersion[] = (state?.assets ?? []).filter((a) => a.status === 'promoted');
  const shotCodeMap = new Map((state?.shots ?? []).map((s) => [s.id, s.shot_code]));

  return (
    <section className="m4-panel" aria-label="素材生成">
      <div className="m4-panel-head">
        <div>
          <h2>② 素材生成</h2>
          <p className="m4-panel-desc">
            按①已冻结分镜归并人物 / 场景 / 道具需求；支持本地上传、复用已有主素材或 Qwen-Image-2.1 生成后人工晋升绑定。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          {String(facts.requirements_ready ?? 0)} / {String(facts.requirements_total ?? 0)} 项已就绪
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
              <strong>归并素材需求（{state?.requirements.length ?? 0} 项）</strong>
              <span className="m4-footer-note">
                已晋升主素材：{promotedAssets.length} · 已绑定镜头：{String(facts.shots_bound ?? 0)} /{' '}
                {String(facts.shots_total ?? 0)}
              </span>
            </div>

            {state?.requirements.length === 0 ? (
              <div className="m4-empty-box">
                当前分镜未抽出命名人物/场景/道具需求，可在下方手动上传主素材并绑定至各镜头。
              </div>
            ) : (
              <div className="m4-mat-grid">
                {state?.requirements.map((req) => (
                  <div key={req.id} className="m4-mat-card">
                    <div className="m4-ep-head">
                      <div>
                        <strong>
                          [{FAMILY_LABELS[req.family] ?? req.family}] {req.entity_name}
                        </strong>
                        {req.variant_name && <span className="m4-footer-note"> · {req.variant_name}</span>}
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
                        {req.user_declined ? '已放弃' : req.status}
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
                        </div>
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
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busyKey !== null || req.family === 'voice' || req.family === 'reference_video'}
                        onClick={() => void handleGenerate(req.id)}
                      >
                        {busyKey === `gen:${req.id}` ? '生成中...' : 'Qwen 生成'}
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
                ))}
              </div>
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
              <strong>逐镜 Ref2VA 参考绑定检查</strong>
            </div>
            <div className="m4-table-wrap">
              <table className="m4-table">
                <thead>
                  <tr>
                    <th>镜号</th>
                    <th>已绑定参考（最多 6 图 + 2 音频 + 2 视频）</th>
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
                          {sh.ref2va_ready ? 'Ref2VA 就绪' : '缺参考阻滞'}
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
    </section>
  );
}
