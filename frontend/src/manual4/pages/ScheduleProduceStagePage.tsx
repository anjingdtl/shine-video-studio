import { useCallback, useEffect, useRef, useState } from 'react';
import { FolderOpen } from 'lucide-react';
import {
  batchFreezeM4Prompts,
  controlM4Run,
  createM4Run,
  fetchM4ProductionState,
  freezeM4Prompt,
  openM4JobFolder,
  previewM4Snapshot,
  reconcileM4Job,
  retakeM4Shot,
  saveM4PromptDraft,
  updateM4ShotOptions,
  type M4Job,
  type M4Overview,
  type M4ProductionState,
  type M4RunConfigInput,
  type M4StageSummary,
} from '../api/client';
import { StyleLoraAssemblyPanel, type StyleLoraConfig } from '../../shared/StyleLoraAssemblyPanel';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onNextStage: () => void;
  onMessage?: (msg: string, kind?: 'success' | 'error') => void;
};

type M4ScheduleConfigSnapshot = {
  scopeKind: 'project' | 'episode';
  selectedEpId: string;
  variantId: string;
  acceleratorId: string;
  resolutionScale: number;
  seed: number;
  secondPassEnabled: boolean;
  secondPassScale: number;
  styleLoras: StyleLoraConfig[];
};

function sanitizeM4Loras(raw: unknown): StyleLoraConfig[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((row): row is StyleLoraConfig => (
      Boolean(row) && typeof row === 'object'
      && typeof (row as Record<string, unknown>).lora_id === 'string'
      && typeof (row as Record<string, unknown>).lora_name === 'string'
      && typeof (row as Record<string, unknown>).sha256 === 'string'
    ))
    .slice(0, 3)
    .map((row) => ({
      lora_id: row.lora_id,
      lora_name: row.lora_name,
      sha256: row.sha256,
      strength_model: typeof row.strength_model === 'number' && Number.isFinite(row.strength_model) ? row.strength_model : 0.65,
      trigger_word: typeof row.trigger_word === 'string' ? row.trigger_word : '',
    }));
}

function latestJobPromptId(job?: M4Job | null): string | null {
  const attempts = job?.attempts;
  if (!attempts || attempts.length === 0) return null;
  for (let idx = attempts.length - 1; idx >= 0; idx -= 1) {
    const pid = attempts[idx]?.prompt_id;
    if (pid) return pid;
  }
  return null;
}

function scheduleSnapshotKey(projectId: string): string {
  return `shine.m4.scheduleConfig.${projectId}`;
}

function readScheduleSnapshot(projectId: string): M4ScheduleConfigSnapshot | null {
  try {
    const raw = window.localStorage.getItem(scheduleSnapshotKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<M4ScheduleConfigSnapshot>;
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      scopeKind: parsed.scopeKind === 'episode' ? 'episode' : 'project',
      selectedEpId: typeof parsed.selectedEpId === 'string' ? parsed.selectedEpId : '',
      variantId: typeof parsed.variantId === 'string' && parsed.variantId ? parsed.variantId : 'h3-ref2va-int8-convrot',
      acceleratorId: typeof parsed.acceleratorId === 'string' && parsed.acceleratorId ? parsed.acceleratorId : 'native20',
      resolutionScale: typeof parsed.resolutionScale === 'number' && parsed.resolutionScale > 0 ? parsed.resolutionScale : 0.5,
      seed: typeof parsed.seed === 'number' && Number.isFinite(parsed.seed) ? parsed.seed : 77,
      secondPassEnabled: Boolean(parsed.secondPassEnabled),
      secondPassScale: typeof parsed.secondPassScale === 'number' && parsed.secondPassScale >= 1 ? parsed.secondPassScale : 1.0,
      styleLoras: sanitizeM4Loras(parsed.styleLoras),
    };
  } catch {
    return null;
  }
}

function writeScheduleSnapshot(projectId: string, snap: M4ScheduleConfigSnapshot): void {
  try {
    window.localStorage.setItem(scheduleSnapshotKey(projectId), JSON.stringify(snap));
  } catch {
    // Ignore storage quota or SSR/test restrictions
  }
}

function formatElapsedSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}秒`;
  const mins = Math.floor(s / 60);
  const rem = s % 60;
  return `${mins}分${rem}秒（${s}秒）`;
}

export function ScheduleProduceStagePage({ overview, stage, onRefresh, onNextStage, onMessage }: Props) {
  const projectId = overview.project.id;
  const initialSnapshotRef = useRef<M4ScheduleConfigSnapshot | null>(readScheduleSnapshot(projectId));
  const hasPersistedOrEditedRef = useRef<boolean>(initialSnapshotRef.current !== null);
  const seededFromRunRef = useRef<boolean>(false);

  const [state, setState] = useState<M4ProductionState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [draftTexts, setDraftTexts] = useState<Record<string, string>>({});
  const [retakeConfirmShotId, setRetakeConfirmShotId] = useState<string | null>(null);
  const promptTextareaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const [batchNotice, setBatchNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  // Batch schedule configuration (hydrated from project snapshot or latest execution snapshot)
  const [scopeKind, setScopeKind] = useState<'project' | 'episode'>(initialSnapshotRef.current?.scopeKind ?? 'project');
  const [selectedEpId, setSelectedEpId] = useState(initialSnapshotRef.current?.selectedEpId ?? '');
  const [variantId, setVariantId] = useState(initialSnapshotRef.current?.variantId ?? 'h3-ref2va-int8-convrot');
  const [acceleratorId, setAcceleratorId] = useState(initialSnapshotRef.current?.acceleratorId ?? 'native20');
  const [resolutionScale, setResolutionScale] = useState(initialSnapshotRef.current?.resolutionScale ?? 0.5);
  const [seed, setSeed] = useState(initialSnapshotRef.current?.seed ?? 77);
  const [secondPassEnabled, setSecondPassEnabled] = useState(initialSnapshotRef.current?.secondPassEnabled ?? false);
  const [secondPassScale, setSecondPassScale] = useState(initialSnapshotRef.current?.secondPassScale ?? 1.0);
  const [styleLoras, setStyleLoras] = useState<StyleLoraConfig[]>(initialSnapshotRef.current?.styleLoras ?? []);
  const [snapshotPreview, setSnapshotPreview] = useState<{
    snapshot_sha256: string;
    items: Array<Record<string, unknown>>;
  } | null>(null);

  const selectedVariantObj = state?.options.variants.find((v) => v.variant_id === variantId);
  const selectedVariantLoraSupported =
    typeof selectedVariantObj?.lora_supported === 'boolean'
      ? selectedVariantObj.lora_supported
      : variantId === 'h3-ref2va-int8-convrot';

  useEffect(() => {
    if (!hasPersistedOrEditedRef.current) return;
    writeScheduleSnapshot(projectId, {
      scopeKind,
      selectedEpId,
      variantId,
      acceleratorId,
      resolutionScale,
      seed,
      secondPassEnabled,
      secondPassScale,
      styleLoras,
    });
  }, [projectId, scopeKind, selectedEpId, variantId, acceleratorId, resolutionScale, seed, secondPassEnabled, secondPassScale, styleLoras]);

  const markEdited = () => {
    hasPersistedOrEditedRef.current = true;
  };

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    try {
      const data = await fetchM4ProductionState(projectId);
      setState(data);

      // Seed once from latest backend execution snapshot if no local draft snapshot exists yet
      if (!hasPersistedOrEditedRef.current && !seededFromRunRef.current && data.runs.length > 0) {
        seededFromRunRef.current = true;
        const latestRun = data.runs.find((r) => r.scope_kind === 'project' || r.scope_kind === 'episode') ?? data.runs[0];
        const cfg = latestRun.config;
        if (latestRun.scope_kind === 'episode' || latestRun.scope_kind === 'project') {
          setScopeKind(latestRun.scope_kind);
          if (latestRun.scope_kind === 'episode' && latestRun.scope_target_ids?.[0]) {
            setSelectedEpId(latestRun.scope_target_ids[0]);
          }
        }
        if (cfg) {
          if (cfg.variant_id) setVariantId(cfg.variant_id);
          if (cfg.accelerator_id) setAcceleratorId(cfg.accelerator_id);
          if (typeof cfg.resolution_scale === 'number') setResolutionScale(cfg.resolution_scale);
          if (typeof cfg.seed === 'number') setSeed(cfg.seed);
          if (cfg.second_pass && cfg.second_pass.enabled) {
            setSecondPassEnabled(true);
            if (typeof cfg.second_pass.latent_scale === 'number') setSecondPassScale(cfg.second_pass.latent_scale);
          } else {
            setSecondPassEnabled(false);
          }
          setStyleLoras(sanitizeM4Loras(cfg.style_loras));
        }
      }

      const currentList = data.options.accelerators ?? [];
      setAcceleratorId((prev) => {
        const cur = currentList.find((a) => a.id === prev);
        if (cur && cur.verified === false) {
          const fallbackAcc = currentList.find((a) => a.verified !== false && a.requires_pdd !== null);
          return fallbackAcc ? fallbackAcc.id : prev;
        }
        return prev;
      });
      if (data.options.second_pass && !data.options.second_pass.supported) {
        setSecondPassEnabled(false);
      }
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
      if (data.episodes.length > 0) {
        setSelectedEpId((prev) => (prev && data.episodes.some((e) => e.id === prev) ? prev : data.episodes[0].id));
      }
    } catch (err) {
      if (!opts?.silent) {
        onMessage?.(`读取排片状态失败：${err instanceof Error ? err.message : String(err)}`);
      }
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, [projectId, onMessage]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActiveRun = Boolean(
    state?.runs.some((r) => r.status === 'PENDING' || r.status === 'CLAIMED' || r.status === 'RUNNING'),
  );
  const prevHasActiveRunRef = useRef<boolean>(false);

  useEffect(() => {
    if (prevHasActiveRunRef.current && !hasActiveRun) {
      void onRefresh();
    }
    prevHasActiveRunRef.current = hasActiveRun;
    if (!hasActiveRun) return;
    const timer = window.setInterval(() => {
      void load({ silent: true });
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
      style_loras: styleLoras,
    }),
    [scopeKind, selectedEpId, variantId, acceleratorId, resolutionScale, secondPassEnabled, secondPassScale, seed, styleLoras],
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
      onMessage?.(`保存草稿失败：${err instanceof Error ? err.message : String(err)}`, 'error');
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
      onMessage?.(`冻结失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const batchFreezeTargets = (state?.episodes ?? []).flatMap((ep) =>
    ep.shots.flatMap((sh) => {
      const text = (draftTexts[sh.id] ?? sh.active_prompt?.prompt_text ?? '').trim();
      if (!text) return [];
      const frozenText = (sh.frozen_prompt?.prompt_text ?? '').trim();
      if (sh.prompt_valid_for_run && text === frozenText) return [];
      return [{ shot_id: sh.id, prompt_text: text }];
    }),
  );

  const handleBatchFreeze = async () => {
    if (batchFreezeTargets.length === 0) return;
    setBusyKey('batch-freeze');
    setBatchNotice(null);
    try {
      const res = await batchFreezeM4Prompts(projectId, batchFreezeTargets);
      await applyState(res.state);
      const parts: string[] = [];
      if (res.frozen_shot_ids.length > 0) parts.push(`已冻结 ${res.frozen_shot_ids.length} 镜`);
      if (res.skipped.length > 0) parts.push(`跳过 ${res.skipped.length} 镜`);
      if (res.errors.length > 0) parts.push(`失败 ${res.errors.length} 镜（${res.errors[0]?.message ?? ''}）`);
      if (res.errors.length > 0 && res.frozen_shot_ids.length === 0) {
        const msg = `批量冻结未完成：${parts.join('；')}`;
        setBatchNotice({ kind: 'error', text: msg });
        onMessage?.(msg, 'error');
      } else {
        const msg = `批量冻结完成：${parts.join('，') || '已完成'}。`;
        setBatchNotice({ kind: res.errors.length > 0 ? 'error' : 'success', text: msg });
        onMessage?.(msg, 'success');
      }
    } catch (err) {
      const msg = `批量冻结失败：${err instanceof Error ? err.message : String(err)}`;
      setBatchNotice({ kind: 'error', text: msg });
      onMessage?.(msg, 'error');
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
      onMessage?.(`修改镜头配置失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleBatchContinuity = async (
    shotsToUpdate: Array<{ id: string; continuity_mode: string }>,
    mode: 'last_frame' | 'cut',
  ) => {
    const targets = shotsToUpdate.filter((s) => s.continuity_mode !== mode);
    if (targets.length === 0) return;
    setBusyKey(`batch-continuity:${mode}`);
    try {
      let latestState: M4ProductionState | null = null;
      for (const sh of targets) {
        const res = await updateM4ShotOptions(projectId, sh.id, { continuity_mode: mode });
        latestState = res.state;
      }
      if (latestState) {
        await applyState(latestState);
      }
      onMessage?.(
        `已将该集 ${targets.length} 镜统一设为「${mode === 'last_frame' ? '尾帧延续' : 'CUT 硬切'}」。`,
        'success',
      );
    } catch (err) {
      onMessage?.(`批量更新衔接配置失败：${err instanceof Error ? err.message : String(err)}`, 'error');
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
      onMessage?.(`快照校验阻滞：${err instanceof Error ? err.message : String(err)}`, 'error');
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
      onMessage?.(`开拍提交失败：${err instanceof Error ? err.message : String(err)}`, 'error');
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
      onMessage?.(`操作失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleRetakeShot = async (shotId: string, opts?: { freezeFirst?: boolean }) => {
    setBusyKey(`retake:${shotId}`);
    try {
      if (opts?.freezeFirst) {
        await freezeM4Prompt(projectId, shotId, draftTexts[shotId] ?? '');
      }
      const res = await retakeM4Shot(projectId, shotId, buildConfig('shot', [shotId]));
      setRetakeConfirmShotId(null);
      await applyState(res.state);
      onMessage?.('已建立单镜重拍任务，历史成片产物均完整保留。');
    } catch (err) {
      onMessage?.(`单镜重拍失败：${err instanceof Error ? err.message : String(err)}`, 'error');
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
      onMessage?.(`对账失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleOpenJobFolder = async (jobId: string) => {
    setBusyKey(`open-folder:${jobId}`);
    try {
      await openM4JobFolder(projectId, jobId);
    } catch (err) {
      onMessage?.(`打开文件夹失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const facts = stage?.facts ?? {};
  const selectedEp = scopeKind === 'episode'
    ? (state?.episodes ?? []).find((ep) => ep.id === selectedEpId) ?? null
    : null;
  const selectedEpShotIds = new Set((selectedEp?.shots ?? []).map((s) => s.id));
  const selectedEpFrozenCount = (selectedEp?.shots ?? []).filter((s) => s.prompt_valid_for_run).length;
  const selectedEpTotalCount = selectedEp?.shots.length ?? 0;
  const selectedEpReady = selectedEpTotalCount > 0 && selectedEpFrozenCount === selectedEpTotalCount;
  const visibleBlockers = (stage?.blockers ?? []).filter((b) => {
    if (scopeKind !== 'episode' || !selectedEp) return true;
    if (b.code === 'M4_SHOT_PROMPT_NOT_FROZEN') {
      if (b.episode_id) return b.episode_id === selectedEp.id;
      if (b.shot_id) return selectedEpShotIds.has(b.shot_id);
    }
    if (b.code === 'M4_NO_PRODUCTION_RUN' && !selectedEpReady) return false;
    return true;
  });
  const hasPassedEpisode = (state?.episodes ?? []).some(
    (ep) => ep.shots.length > 0 && ep.shots.every((s) => s.latest_job?.status === 'PASSED'),
  );
  const shotCodeById = new Map<string, string>();
  for (const ep of state?.episodes ?? []) {
    for (const sh of ep.shots) shotCodeById.set(sh.id, sh.shot_code);
  }

  return (
    <section className="m4-panel" aria-label="排片生产">
      <div className="m4-panel-head">
        <div>
          <h2>③ 排片生产</h2>
          <p className="m4-panel-desc">
            逐镜填入并冻结 H3 原文提示词（严禁静默回退编译器文本），配置画幅与镜间 CUT / 延续后由四阶独立 Worker 批量拍摄。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done || selectedEpReady ? 'ok' : overview.gpu.busy ? 'busy' : 'warn'}`}>
          {stage?.done
            ? '全部镜头已通过'
            : overview.gpu.busy
              ? 'GPU 执行/占用中'
              : selectedEpReady
                ? `${selectedEp?.episode_code} 已就绪可开拍`
                : '待冻结与开拍'}
        </span>
      </div>

      {visibleBlockers.length > 0 && (
        <div className="m4-blocker-box" role="status">
          {visibleBlockers.map((b, idx) => (
            <div key={`${b.code}-${idx}`}>• {b.message}</div>
          ))}
        </div>
      )}

      <div className="m4-ep-card">
        <div className="m4-ep-head">
          <strong>批量排片与全局 H3 参数</strong>
          <span className="m4-footer-note">
            {selectedEp
              ? `当前所选 ${selectedEp.episode_code} 已冻结：${selectedEpFrozenCount} / ${selectedEpTotalCount}（全片 ${String(facts.prompts_frozen ?? 0)} / ${String(facts.shots_total ?? 0)}）`
              : `已冻结：${String(facts.prompts_frozen ?? 0)} / ${String(facts.shots_total ?? 0)}`}
            {' · '}四阶 Worker：{overview.worker_online ? '在线' : '离线（请启动 worker-m4）'} · GPU：
            {overview.gpu.busy ? '占用中' : '空闲'}
          </span>
        </div>

        <div className="m4-toolbar">
          <label>
            排片范围：
            <select
              className="m4-input"
              value={scopeKind}
              onChange={(e) => { markEdited(); setScopeKind(e.target.value as 'project' | 'episode'); }}
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
                onChange={(e) => { markEdited(); setSelectedEpId(e.target.value); }}
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
            <select className="m4-input" value={variantId} onChange={(e) => { markEdited(); setVariantId(e.target.value); }}>
              {(state?.options.variants?.length
                ? state.options.variants
                : [{ variant_id: 'h3-ref2va-int8-convrot', label: 'H3 Ref2VA INT8 ConvRot（本机基线）', verified: true, accelerators: ['pdd8', 'native20'] }]
              ).map((v) => (
                <option key={v.variant_id} value={v.variant_id}>
                  {v.label}{v.verified ? '' : '（未验收）'}
                </option>
              ))}
            </select>
          </label>
          <label>
            Ref2VA 采样：
            <select className="m4-input" value={acceleratorId} onChange={(e) => { markEdited(); setAcceleratorId(e.target.value); }}>
              {(state?.options.accelerators?.length
                ? state.options.accelerators
                : [
                    { id: 'pdd8', steps: 8, requires_pdd: true, label: 'PDD 8 步' },
                    { id: 'native20', steps: 20, requires_pdd: false, label: '原生 20 步' },
                  ]
              )
                .filter((a) => a.requires_pdd !== null)
                .map((a) => (
                  <option key={a.id} value={a.id} disabled={a.verified === false}>
                    {a.label} ({a.steps} 步){a.verified === false ? '（可选未就绪）' : ''}
                  </option>
                ))}
            </select>
          </label>
          <label>
            分辨率档位：
            <select
              className="m4-input"
              value={resolutionScale}
              onChange={(e) => { markEdited(); setResolutionScale(Number(e.target.value)); }}
            >
              {(state?.options.frames?.length
                ? Array.from(
                    new Map(
                      state.options.frames
                        .filter((f) => f.aspect_ratio === '16:9')
                        .map((f) => [f.scale, f]),
                    ).values(),
                  )
                : [
                    { scale: 0.3, width: 416, height: 240 },
                    { scale: 0.5, width: 672, height: 384 },
                    { scale: 0.7, width: 960, height: 544 },
                    { scale: 1.0, width: 1344, height: 768 },
                  ]
              ).map((f) => (
                <option key={f.scale} value={f.scale}>
                  {f.scale}× ({f.width}×{f.height})
                </option>
              ))}
            </select>
          </label>
          <label>
            随机种子：
            <input
              type="number"
              className="m4-input"
              style={{ width: 90 }}
              value={seed}
              onChange={(e) => { markEdited(); setSeed(Number(e.target.value) || 0); }}
            />
          </label>
          <label className="m4-toolbar">
            <input
              type="checkbox"
              checked={secondPassEnabled}
              disabled={state?.options.second_pass ? !state.options.second_pass.supported : false}
              onChange={(e) => { markEdited(); setSecondPassEnabled(e.target.checked); }}
            />
            二采精修/潜空间放大
            {state?.options.second_pass && !state.options.second_pass.supported ? '（可选未就绪）' : ''}
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
                onChange={(e) => { markEdited(); setSecondPassScale(Number(e.target.value) || 1); }}
              />
            </label>
          )}
        </div>

        <StyleLoraAssemblyPanel
          value={styleLoras}
          onChange={(next) => {
            markEdited();
            setStyleLoras(next);
          }}
          loraSupported={selectedVariantLoraSupported}
          disabled={busyKey !== null}
          onMessage={(msg) => onMessage?.(msg, 'success')}
        />

        <div className="m4-toolbar">
          <button
            type="button"
            className="btn-secondary"
            disabled={busyKey !== null || batchFreezeTargets.length === 0}
            onClick={() => void handleBatchFreeze()}
          >
            {busyKey === 'batch-freeze'
              ? '批量冻结中...'
              : `批量冻结已输入提示词（${batchFreezeTargets.length} 镜）`}
          </button>
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

        {batchNotice && (
          <div className={batchNotice.kind === 'error' ? 'm4-blocker-box' : 'm4-footer-note'} role="status">
            {batchNotice.text}
          </div>
        )}

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
              <span className="m4-toolbar">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busyKey !== null || ep.shots.every((s) => s.continuity_mode === 'last_frame')}
                  onClick={(e) => {
                    e.preventDefault();
                    void handleBatchContinuity(ep.shots, 'last_frame');
                  }}
                >
                  整集尾帧延续
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busyKey !== null || ep.shots.every((s) => s.continuity_mode === 'cut')}
                  onClick={(e) => {
                    e.preventDefault();
                    void handleBatchContinuity(ep.shots, 'cut');
                  }}
                >
                  整集 CUT 硬切
                </button>
              </span>
            </summary>

            {ep.shots.map((sh) => {
              const lj = sh.latest_job;
              const currentText = draftTexts[sh.id] ?? sh.active_prompt?.prompt_text ?? '';
              const frozenText = sh.frozen_prompt?.prompt_text ?? '';
              const hasUnfrozenEdits = currentText.trim() !== frozenText.trim();
              const isRetake = lj !== null;
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
                        disabled={busyKey !== null}
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
                        disabled={busyKey !== null}
                        value={sh.continuity_mode}
                        onChange={(e) => void handleOptionChange(sh.id, { continuity_mode: e.target.value })}
                      >
                        <option value="cut">CUT 硬切</option>
                        <option value="last_frame">尾帧延续</option>
                      </select>
                    </label>
                  </div>

                  {sh.reference_manifest.length > 0 && (
                    <div className="m4-bound-preview" aria-label={`${sh.shot_code} 参考素材核对`}>
                      {sh.reference_manifest.map((m) => (
                        <span key={`${sh.id}-${m.asset_version_id}`} className="m4-badge">
                          {m.use_as === 'image' && m.media_url && (
                            <img src={m.media_url} alt={m.role_label} className="m4-thumb-sm" />
                          )}
                          <span>
                            {m.h3_label} = {m.role_label}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}

                  <textarea
                    ref={(el) => {
                      promptTextareaRefs.current[sh.id] = el;
                    }}
                    className="m4-textarea"
                    placeholder="填写这一镜提交给 MiniMax-H3 的人工原文提示词（如 subject_definitions / detailed_description 等）"
                    value={currentText}
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
                        onClick={() => {
                          if (isRetake || hasUnfrozenEdits) {
                            setRetakeConfirmShotId(sh.id);
                          } else {
                            void handleRetakeShot(sh.id);
                          }
                        }}
                      >
                        单镜开拍 / 重拍
                      </button>
                    )}
                    {lj?.status === 'NEEDS_REVIEW' && (
                      <>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleReconcileJob(lj.id, 'requeue')}
                        >
                          人工对账重排
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleReconcileJob(lj.id, 'mark_failed')}
                        >
                          确认失败
                        </button>
                      </>
                    )}
                  </div>

                  {retakeConfirmShotId === sh.id && (
                    <div className="m4-blocker-box" role="alertdialog" aria-label={`重拍 ${sh.shot_code} 提示词确认`}>
                      <div>
                        {hasUnfrozenEdits
                          ? `重拍 ${sh.shot_code} 前提示：提示词文本框有尚未冻结的修改，是否先冻结当前提示词后再重拍，或继续调整？`
                          : `重拍 ${sh.shot_code} 前提示：将沿用已冻结的 H3 提示词（版本 v${sh.frozen_prompt?.revision ?? '?'}），重拍前是否需要调整提示词？`}
                      </div>
                      <div className="m4-toolbar">
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => {
                            setRetakeConfirmShotId(null);
                            promptTextareaRefs.current[sh.id]?.focus();
                          }}
                        >
                          去调整提示词
                        </button>
                        {hasUnfrozenEdits && (
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={busyKey !== null || !currentText.trim()}
                            onClick={() => void handleRetakeShot(sh.id, { freezeFirst: true })}
                          >
                            冻结修改并重拍
                          </button>
                        )}
                        <button
                          type="button"
                          className={hasUnfrozenEdits ? 'btn-secondary' : 'btn-primary'}
                          disabled={busyKey !== null}
                          onClick={() => void handleRetakeShot(sh.id)}
                        >
                          {hasUnfrozenEdits ? '按原冻结版本重拍' : '无需调整，继续重拍'}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => setRetakeConfirmShotId(null)}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  )}

                  {lj?.failure_detail && (
                    <div className="m4-footer-note">
                      失败/阻滞详情（{lj.failure_code}）：{lj.failure_detail}
                      {latestJobPromptId(lj) ? ` · prompt_id=${latestJobPromptId(lj)}` : ''}
                    </div>
                  )}

                  {lj?.media_url && (
                    <div className="m4-cand-list">
                      <video src={lj.media_url} controls width={360} preload="metadata" />
                      <div className="m4-toolbar">
                        <span className="m4-footer-note">
                          实测 {lj.measured_duration_s ?? '?'}s · {lj.measured_width ?? '?'}×{lj.measured_height ?? '?'} · SHA{' '}
                          {lj.output_sha256.slice(0, 12)}
                          {typeof lj.elapsed_seconds === 'number'
                            ? ` · 拍摄耗时 ${formatElapsedSeconds(lj.elapsed_seconds)}`
                            : ''}
                        </span>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleOpenJobFolder(lj.id)}
                        >
                          <FolderOpen size={14} aria-hidden="true" /> 打开所在文件夹
                        </button>
                      </div>
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
          {(state?.runs ?? []).map((r) => {
            const reviewJobs = r.jobs.filter((j) => j.status === 'NEEDS_REVIEW');
            return (
              <div key={r.id} className="m4-mat-card">
                <div className="m4-toolbar">
                  <span>
                    批次 {r.id.slice(0, 8)} · 范围 {r.scope_kind} · 状态 <strong>{r.status}</strong> · 镜头数 {r.jobs.length}
                    {r.config?.style_loras?.length
                      ? ` · LoRA(${r.config.style_loras.map((l) => `${l.lora_name}@${l.strength_model}`).join('+')})`
                      : ''}
                  </span>
                  {(r.status === 'PENDING' || r.status === 'RUNNING') && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busyKey !== null}
                      onClick={() => void handleControlRun(r.id, 'pause')}
                    >
                      暂停
                    </button>
                  )}
                  {r.status === 'PAUSED' && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busyKey !== null}
                      onClick={() => void handleControlRun(r.id, 'resume')}
                    >
                      恢复继续
                    </button>
                  )}
                  {r.status !== 'COMPLETED' && r.status !== 'CANCELLED' && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busyKey !== null}
                      onClick={() => void handleControlRun(r.id, 'cancel')}
                    >
                      取消
                    </button>
                  )}
                </div>
                {reviewJobs.map((rj) => {
                  const pid = latestJobPromptId(rj);
                  return (
                    <div key={rj.id} className="m4-blocker-box" role="status">
                      <div>
                        • <strong>{shotCodeById.get(rj.shot_id) ?? rj.shot_id.slice(0, 8)}</strong> 待人工对账（
                        {rj.failure_code ?? 'NEEDS_REVIEW'}）：{rj.failure_detail || '提交结果未知，请确认后处置。'}
                        {pid ? ` · prompt_id=${pid}` : ''}
                      </div>
                      <div className="m4-toolbar">
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleReconcileJob(rj.id, 'requeue')}
                        >
                          人工对账重排
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={busyKey !== null}
                          onClick={() => void handleReconcileJob(rj.id, 'mark_failed')}
                        >
                          确认失败
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}

      <div className="m4-footer-bar">
        <span className="m4-footer-note">系统级 GPU 互斥租约保护本机显卡，不会中断七阶或跨线覆盖。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done && !hasPassedEpisode} onClick={onNextStage}>
          下一步：产出成片
        </button>
      </div>
    </section>
  );
}
