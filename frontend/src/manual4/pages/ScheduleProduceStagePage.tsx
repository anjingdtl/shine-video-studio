import { useCallback, useEffect, useState } from 'react';
import {
  controlM4Run,
  createM4Run,
  fetchM4ProductionState,
  freezeM4Prompt,
  previewM4Snapshot,
  reconcileM4Job,
  retakeM4Shot,
  saveM4PromptDraft,
  updateM4ShotOptions,
  type M4Overview,
  type M4ProductionState,
  type M4RunConfigInput,
  type M4StageSummary,
} from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onNextStage: () => void;
  onMessage?: (msg: string) => void;
};

export function ScheduleProduceStagePage({ overview, stage, onRefresh, onNextStage, onMessage }: Props) {
  const projectId = overview.project.id;
  const [state, setState] = useState<M4ProductionState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [draftTexts, setDraftTexts] = useState<Record<string, string>>({});

  // Batch schedule configuration
  const [scopeKind, setScopeKind] = useState<'project' | 'episode'>('project');
  const [selectedEpId, setSelectedEpId] = useState('');
  const [variantId, setVariantId] = useState('h3-ref2va-int8-convrot');
  const [acceleratorId, setAcceleratorId] = useState('pdd8');
  const [resolutionScale, setResolutionScale] = useState(0.5);
  const [seed, setSeed] = useState(77);
  const [secondPassEnabled, setSecondPassEnabled] = useState(false);
  const [secondPassScale, setSecondPassScale] = useState(1.0);
  const [snapshotPreview, setSnapshotPreview] = useState<{
    snapshot_sha256: string;
    items: Array<Record<string, unknown>>;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchM4ProductionState(projectId);
      setState(data);
      setDraftTexts((prev) => {
        const next = { ...prev };
        for (const ep of data.episodes) {
          for (const sh of ep.shots) {
            if (next[sh.id] === undefined && sh.active_prompt?.prompt_text) {
              next[sh.id] = sh.active_prompt.prompt_text;
            }
          }
        }
        return next;
      });
      if (!selectedEpId && data.episodes.length > 0) {
        setSelectedEpId(data.episodes[0].id);
      }
    } catch (err) {
      onMessage?.(`读取排片状态失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [projectId, onMessage, selectedEpId]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActiveRun = Boolean(
    state?.runs.some((r) => r.status === 'PENDING' || r.status === 'CLAIMED' || r.status === 'RUNNING'),
  );

  useEffect(() => {
    if (!hasActiveRun) return;
    const timer = window.setInterval(() => {
      void load();
      void onRefresh();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, load, onRefresh]);

  const buildConfig = useCallback(
    (overrideScopeKind?: string, overrideTargets?: string[]): M4RunConfigInput => ({
      scope_kind: overrideScopeKind ?? scopeKind,
      scope_target_ids: overrideTargets ?? (scopeKind === 'episode' && selectedEpId ? [selectedEpId] : []),
      variant_id: variantId,
      accelerator_id: acceleratorId,
      resolution_scale: resolutionScale,
      second_pass: secondPassEnabled
        ? { enabled: true, denoise: 0.25, latent_scale: secondPassScale, nfe: '8' }
        : null,
      seed,
      provider: 'comfyui',
    }),
    [scopeKind, selectedEpId, variantId, acceleratorId, resolutionScale, secondPassEnabled, secondPassScale, seed],
  );

  const applyState = async (next: M4ProductionState) => {
    setState(next);
    await onRefresh();
  };

  const handleSaveDraft = async (shotId: string) => {
    setBusyKey(`draft:${shotId}`);
    try {
      const res = await saveM4PromptDraft(projectId, shotId, draftTexts[shotId] ?? '');
      await applyState(res.state);
      onMessage?.(`已保存 H3 提示词草稿（版本 ${res.prompt.revision}）。`);
    } catch (err) {
      onMessage?.(`保存草稿失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleFreezePrompt = async (shotId: string) => {
    setBusyKey(`freeze:${shotId}`);
    try {
      const res = await freezeM4Prompt(projectId, shotId, draftTexts[shotId] ?? '');
      await applyState(res.state);
      onMessage?.(`已冻结 H3 原文提示词（版本 ${res.prompt.revision}）。`);
    } catch (err) {
      onMessage?.(`冻结失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleOptionChange = async (shotId: string, opts: { aspect_ratio?: string; continuity_mode?: string }) => {
    setBusyKey(`opt:${shotId}`);
    try {
      const res = await updateM4ShotOptions(projectId, shotId, opts);
      await applyState(res.state);
    } catch (err) {
      onMessage?.(`修改镜头配置失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handlePreviewSnapshot = async () => {
    setBusyKey('preview');
    try {
      const snap = await previewM4Snapshot(projectId, buildConfig());
      setSnapshotPreview(snap);
      onMessage?.(`快照校验通过（共 ${snap.items.length} 镜，SHA ${snap.snapshot_sha256.slice(0, 10)}）。`);
    } catch (err) {
      setSnapshotPreview(null);
      onMessage?.(`快照校验阻滞：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleConfirmAndStart = async () => {
    setBusyKey('start');
    try {
      const res = await createM4Run(projectId, buildConfig());
      await applyState(res.state);
      setSnapshotPreview(null);
      onMessage?.(`已建立四阶排片批次 ${res.run.id.slice(0, 8)}，等待或正在由四阶 Worker 连续出片。`);
    } catch (err) {
      onMessage?.(`开拍提交失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleControlRun = async (runId: string, action: 'pause' | 'resume' | 'cancel') => {
    setBusyKey(`ctrl:${runId}`);
    try {
      const res = await controlM4Run(projectId, runId, action);
      await applyState(res.state);
      onMessage?.(`批次状态已更新为 ${res.run.status}。`);
    } catch (err) {
      onMessage?.(`操作失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleRetakeShot = async (shotId: string) => {
    setBusyKey(`retake:${shotId}`);
    try {
      const res = await retakeM4Shot(projectId, shotId, buildConfig('shot', [shotId]));
      await applyState(res.state);
      onMessage?.('已建立单镜重拍任务，历史成片产物均完整保留。');
    } catch (err) {
      onMessage?.(`单镜重拍失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleReconcileJob = async (jobId: string, decision: 'requeue' | 'mark_failed') => {
    setBusyKey(`reconcile:${jobId}`);
    try {
      const res = await reconcileM4Job(projectId, jobId, decision);
      await applyState(res.state);
      onMessage?.(`任务已完成人工对账（${decision}）。`);
    } catch (err) {
      onMessage?.(`对账失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyKey(null);
    }
  };

  const facts = stage?.facts ?? {};

  return (
    <section className="m4-panel" aria-label="排片生产">
      <div className="m4-panel-head">
        <div>
          <h2>③ 排片生产</h2>
          <p className="m4-panel-desc">
            逐镜填入并冻结 H3 原文提示词（严禁静默回退编译器文本），配置画幅与镜间 CUT / 延续后由四阶独立 Worker 批量拍摄。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : overview.gpu.busy ? 'busy' : 'warn'}`}>
          {stage?.done ? '全部镜头已通过' : overview.gpu.busy ? 'GPU 执行/占用中' : '待冻结与开拍'}
        </span>
      </div>

      {stage && stage.blockers.length > 0 && (
        <div className="m4-blocker-box" role="status">
          {stage.blockers.map((b, idx) => (
            <div key={`${b.code}-${idx}`}>• {b.message}</div>
          ))}
        </div>
      )}

      <div className="m4-ep-card">
        <div className="m4-ep-head">
          <strong>批量排片与全局 H3 参数</strong>
          <span className="m4-footer-note">
            已冻结：{String(facts.prompts_frozen ?? 0)} / {String(facts.shots_total ?? 0)} · 四阶 Worker：
            {overview.worker_online ? '在线' : '离线（请启动 worker-m4）'} · GPU：{overview.gpu.busy ? '占用中' : '空闲'}
          </span>
        </div>

        <div className="m4-toolbar">
          <label>
            排片范围：
            <select
              className="m4-input"
              value={scopeKind}
              onChange={(e) => setScopeKind(e.target.value as 'project' | 'episode')}
            >
              <option value="project">全片全部镜头</option>
              <option value="episode">按集排片</option>
            </select>
          </label>
          {scopeKind === 'episode' && (
            <label>
              选择分集：
              <select
                className="m4-input"
                value={selectedEpId}
                onChange={(e) => setSelectedEpId(e.target.value)}
              >
                {(state?.episodes ?? []).map((ep) => (
                  <option key={ep.id} value={ep.id}>
                    {ep.episode_code} · {ep.title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            模型变体：
            <select className="m4-input" value={variantId} onChange={(e) => setVariantId(e.target.value)}>
              <option value="h3-ref2va-int8-convrot">H3 Ref2VA INT8 ConvRot（本机基线）</option>
            </select>
          </label>
          <label>
            加速配方：
            <select className="m4-input" value={acceleratorId} onChange={(e) => setAcceleratorId(e.target.value)}>
              <option value="pdd8">PDD 8 步（推荐）</option>
              <option value="native20">原生 20 步</option>
            </select>
          </label>
          <label>
            分辨率档位：
            <select
              className="m4-input"
              value={resolutionScale}
              onChange={(e) => setResolutionScale(Number(e.target.value))}
            >
              <option value={0.3}>0.3× 快速验通</option>
              <option value={0.5}>0.5× 中等预览</option>
              <option value={0.7}>0.7× 高清</option>
              <option value={1.0}>1.0× 原生（1344×768 / 768×1344）</option>
            </select>
          </label>
          <label>
            随机种子：
            <input
              type="number"
              className="m4-input"
              style={{ width: 90 }}
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value) || 0)}
            />
          </label>
          <label className="m4-toolbar">
            <input
              type="checkbox"
              checked={secondPassEnabled}
              onChange={(e) => setSecondPassEnabled(e.target.checked)}
            />
            二采精修/潜空间放大
          </label>
          {secondPassEnabled && (
            <label>
              放大倍率：
              <input
                type="number"
                step="0.25"
                min={1}
                max={2}
                className="m4-input"
                style={{ width: 80 }}
                value={secondPassScale}
                onChange={(e) => setSecondPassScale(Number(e.target.value) || 1)}
              />
            </label>
          )}
        </div>

        <div className="m4-toolbar">
          <button
            type="button"
            className="btn-secondary"
            disabled={busyKey !== null}
            onClick={() => void handlePreviewSnapshot()}
          >
            预览提交快照
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busyKey !== null}
            onClick={() => void handleConfirmAndStart()}
          >
            确认排片并开拍
          </button>
        </div>

        {snapshotPreview && (
          <div className="m4-cand-list">
            <strong>不可变执行快照预览（SHA {snapshotPreview.snapshot_sha256.slice(0, 12)}）</strong>
            {snapshotPreview.items.map((item) => (
              <div key={String(item.shot_id)} className="m4-footer-note">
                {String(item.shot_code)} · {String(item.target_seconds)}s · {String(item.width)}×{String(item.height)} ·{' '}
                {String(item.continuity_mode)} · {String(item.prompt_excerpt)}
              </div>
            ))}
          </div>
        )}
      </div>

      {loading && !state ? (
        <div className="m4-empty-box">正在加载四阶排片与分镜工作台...</div>
      ) : (
        (state?.episodes ?? []).map((ep) => (
          <details key={ep.id} open className="m4-ep-card">
            <summary className="m4-ep-head">
              <strong>
                {ep.episode_code}：{ep.title || '未命名分集'}（{ep.shots.length} 镜 · {ep.planned_total_seconds}s）
              </strong>
            </summary>

            {ep.shots.map((sh) => {
              const lj = sh.latest_job;
              return (
                <div key={sh.id} className="m4-mat-card">
                  <div className="m4-ep-head">
                    <div>
                      <strong>
                        {sh.shot_code} · 时长 {sh.target_seconds}s（只读锁定） · 版本 v{sh.revision}
                      </strong>
                      <div className="m4-footer-note">{sh.summary}</div>
                    </div>
                    <div className="m4-toolbar">
                      <span className={`m4-badge ${sh.prompt_valid_for_run ? 'ok' : 'warn'}`}>
                        {sh.prompt_valid_for_run ? 'H3 已冻结' : `待冻结（${sh.prompt_invalid_reason ?? '未冻结'}）`}
                      </span>
                      {lj && (
                        <span className={`m4-badge ${lj.status === 'PASSED' ? 'ok' : lj.status === 'FAILED' ? 'warn' : 'busy'}`}>
                          最近任务：{lj.status}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="m4-toolbar">
                    <span className="m4-footer-note">
                      Ref2VA 标签：
                      {sh.reference_manifest.map((m) => `${m.h3_label}=${m.role_label}`).join(' · ') || '无'}
                    </span>
                    <label>
                      画幅：
                      <select
                        className="m4-input"
                        style={{ width: 110 }}
                        value={sh.aspect_ratio}
                        onChange={(e) => void handleOptionChange(sh.id, { aspect_ratio: e.target.value })}
                      >
                        <option value="16:9">16:9 横屏</option>
                        <option value="9:16">9:16 竖屏</option>
                      </select>
                    </label>
                    <label>
                      衔接：
                      <select
                        className="m4-input"
                        style={{ width: 130 }}
                        value={sh.continuity_mode}
                        onChange={(e) => void handleOptionChange(sh.id, { continuity_mode: e.target.value })}
                      >
                        <option value="cut">CUT 硬切</option>
                        <option value="last_frame">尾帧延续</option>
                      </select>
                    </label>
                  </div>

                  <textarea
                    className="m4-textarea"
                    placeholder="填写这一镜提交给 MiniMax-H3 的人工原文提示词（如 subject_definitions / detailed_description 等）"
                    value={draftTexts[sh.id] ?? sh.active_prompt?.prompt_text ?? ''}
                    onChange={(e) => setDraftTexts((prev) => ({ ...prev, [sh.id]: e.target.value }))}
                  />

                  {sh.active_prompt && sh.active_prompt.warnings.length > 0 && (
                    <div className="m4-footer-note">
                      格式参考建议（不阻塞提交）：
                      {sh.active_prompt.warnings.map((w) => w.message).join('；')}
                    </div>
                  )}

                  <div className="m4-toolbar">
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busyKey !== null}
                      onClick={() => void handleSaveDraft(sh.id)}
                    >
                      保存草稿
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={busyKey !== null}
                      onClick={() => void handleFreezePrompt(sh.id)}
                    >
                      冻结当前提示词
                    </button>
                    {sh.prompt_valid_for_run && (
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busyKey !== null}
                        onClick={() => void handleRetakeShot(sh.id)}
                      >
                        单镜开拍 / 重拍
                      </button>
                    )}
                    {lj?.status === 'NEEDS_REVIEW' && (
                      <>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => void handleReconcileJob(lj.id, 'requeue')}
                        >
                          人工对账重排
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => void handleReconcileJob(lj.id, 'mark_failed')}
                        >
                          确认失败
                        </button>
                      </>
                    )}
                  </div>

                  {lj?.failure_detail && (
                    <div className="m4-footer-note">
                      失败/阻滞详情（{lj.failure_code}）：{lj.failure_detail}
                    </div>
                  )}

                  {lj?.media_url && (
                    <div className="m4-cand-list">
                      <video src={lj.media_url} controls width={360} preload="metadata" />
                      <span className="m4-footer-note">
                        实测 {lj.measured_duration_s ?? '?'}s · {lj.measured_width ?? '?'}×{lj.measured_height ?? '?'} · SHA{' '}
                        {lj.output_sha256.slice(0, 12)}
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </details>
        ))
      )}

      {(state?.runs ?? []).length > 0 && (
        <div className="m4-ep-card">
          <strong>排片批次历史与运行控制</strong>
          {(state?.runs ?? []).map((r) => (
            <div key={r.id} className="m4-toolbar">
              <span>
                批次 {r.id.slice(0, 8)} · 范围 {r.scope_kind} · 状态 <strong>{r.status}</strong> · 镜头数 {r.jobs.length}
              </span>
              {(r.status === 'PENDING' || r.status === 'RUNNING') && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => void handleControlRun(r.id, 'pause')}
                >
                  暂停
                </button>
              )}
              {r.status === 'PAUSED' && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => void handleControlRun(r.id, 'resume')}
                >
                  恢复继续
                </button>
              )}
              {r.status !== 'COMPLETED' && r.status !== 'CANCELLED' && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => void handleControlRun(r.id, 'cancel')}
                >
                  取消
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="m4-footer-bar">
        <span className="m4-footer-note">系统级 GPU 互斥租约保护本机显卡，不会中断七阶或跨线覆盖。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done} onClick={onNextStage}>
          下一步：产出成片
        </button>
      </div>
    </section>
  );
}
