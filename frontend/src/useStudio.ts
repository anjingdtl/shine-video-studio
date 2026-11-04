import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api, json, type AgentRoute, type Asset, type AuditPage, type Job, type Project,
  type PromptPreview, type ProviderProfile, type QCReport, type QualityGate, type RepairSuggestion,
  type Shot, type Style, type StyleVersion, type SystemHealth, type SystemStatus, type WorkflowProfile,
} from './api';
import { emptyStyle } from './constants';

/** 数据与操作层：项目/素材/风格/镜头/任务/系统状态的全部加载、轮询和提交。
 *  App.tsx 只负责布局与导航，不再自带业务逻辑。
 *  「演示 / 真实制作」的唯一事实来源是后端 /system/status 的 production_mode：
 *  用户的生成意图单独保存，绝不据此推断环境就绪。 */
export function useStudio() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [projectsLoadError, setProjectsLoadError] = useState('');
  const activeProjectId = useRef(projectId);
  activeProjectId.current = projectId;
  const projectsRequest = useRef(0);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [styles, setStyles] = useState<StyleVersion[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [style, setStyle] = useState<Style>(emptyStyle);
  const [projectTitle, setProjectTitle] = useState('');
  const [mockScenario, setMockScenario] = useState('success');
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [systemChecked, setSystemChecked] = useState(false);
  const [apiOnline, setApiOnline] = useState(false);
  const healthProbe = useRef(0);
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [workflowProfiles, setWorkflowProfiles] = useState<WorkflowProfile[]>([]);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  /** Bumped by the global refresh button; panels that keep their own data
   *  (delivery) listen to it so the topbar refresh covers their data too. */
  const [dataEpoch, setDataEpoch] = useState(0);
  const [queueProfile, setQueueProfile] = useState('');
  const [reviewScope, setReviewScope] = useState<'basic' | 'full'>('full');
  const [reviewWidth, setReviewWidth] = useState('768');
  const [reviewHeight, setReviewHeight] = useState('1344');

  const project = projects.find((item) => item.id === projectId);
  const currentStyle = styles.find((item) => item.id === project?.frozen_style_version_id);
  const approvedAssets = useMemo(() => assets.filter((asset) => asset.approved && asset.locked), [assets]);
  const production = system?.production_mode ?? null;
  /** 后端环境决定能否提交真实任务；用户演示选择独立、持久保存。 */
  const realSubmissionAllowed = production !== null
    && ['READY', 'RUNNING'].includes(production.mode);
  const [demoOverride, updateDemoOverride] = useState(() => localStorage.getItem('shine.productionIntent') === 'mock');
  function setDemoOverride(demo: boolean) {
    localStorage.setItem('shine.productionIntent', demo ? 'mock' : 'comfyui');
    updateDemoOverride(demo);
  }
  const hasSeenReal = useRef(false);
  if (realSubmissionAllowed) hasSeenReal.current = true;
  useEffect(() => {
    if (realSubmissionAllowed && !demoOverride) localStorage.setItem('shine.productionIntent', 'comfyui');
  }, [realSubmissionAllowed, demoOverride]);
  // Capability loss must never turn a real submission into a successful demo.
  const queueProvider: 'mock' | 'comfyui' = demoOverride || (!hasSeenReal.current && !realSubmissionAllowed && production?.mode === 'DEMO'
    && localStorage.getItem('shine.productionIntent') !== 'comfyui') ? 'mock' : 'comfyui';

  function report(text: string, kind: 'success' | 'error' = 'success') {
    setNotice({ text, kind });
    window.setTimeout(() => setNotice(null), 5200);
  }

  function dismissNotice() { setNotice(null); }

  async function withBusy(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { report((error as Error).message, 'error'); }
    finally { setBusy(false); }
  }

  async function withBusyReturn<T>(action: () => Promise<T>): Promise<T> {
    setBusy(true);
    try { return await action(); }
    catch (error) { report((error as Error).message, 'error'); throw error; }
    finally { setBusy(false); }
  }

  const checkConnection = useCallback(async () => {
    const probe = ++healthProbe.current;
    let online = false;
    try {
      const projects = await api<Project[]>('/projects', { signal: AbortSignal.timeout(2500), cache: 'no-store' });
      online = Array.isArray(projects);
    } catch { /* A failed health probe means the API cannot be reached. */ }
    if (probe === healthProbe.current) {
      setApiOnline(online);
      setSystemChecked(true);
    }
    return online;
  }, []);

  const loadSystem = useCallback(async () => {
    try { setSystem(await api<SystemStatus>('/system/status', { signal: AbortSignal.timeout(8000), cache: 'no-store' })); }
    catch { setSystem(null); }
  }, []);

  const loadAudit = useCallback(async (id: string, offset = 0, signal?: AbortSignal) => {
    const page = await api<AuditPage>(`/projects/${id}/events?limit=30&offset=${offset}`, { signal });
    if (activeProjectId.current !== id) return;
    setAudit((current) => offset === 0 || !current
      ? page
      : { ...page, items: [...current.items, ...page.items] });
  }, []);

  const loadSettings = useCallback(async (id: string, signal?: AbortSignal) => {
    const [profileRows, routeRows, workflowRows] = await Promise.all([
      api<ProviderProfile[]>('/provider-profiles', { signal }),
      api<AgentRoute[]>('/agent-routes', { signal }),
      api<WorkflowProfile[]>(`/workflow-profiles?project_id=${id}`, { signal }),
    ]);
    if (activeProjectId.current !== id) return;
    setProfiles(profileRows); setRoutes(routeRows); setWorkflowProfiles(workflowRows);
  }, []);

  const loadProject = useCallback(async (id: string) => {
    const signal = AbortSignal.timeout(8000);
    const selected = await api<Project>(`/projects/${id}`, { signal });
    if (activeProjectId.current !== id) return;
    setProjects((rows) => rows.map((row) => row.id === id ? selected : row));
    const [assetRows, styleRows, shotRows, jobRows] = await Promise.all([
      api<Asset[]>(`/projects/${id}/assets`, { signal }),
      api<StyleVersion[]>(`/projects/${id}/styles`, { signal }),
      api<Shot[]>(`/projects/${id}/shots`, { signal }),
      api<Job[]>(`/projects/${id}/jobs`, { signal }),
    ]);
    if (activeProjectId.current !== id) return;
    setAssets(assetRows); setStyles(styleRows); setShots(shotRows); setJobs(jobRows);
    setStyle(selected.style_draft ?? styleRows.at(-1)?.snapshot ?? emptyStyle);
    await loadSettings(id, signal);
    await loadAudit(id, 0, signal);
  }, [loadAudit, loadSettings]);

  const loadProjects = useCallback(async (attempts = 4) => {
    const requestId = ++projectsRequest.current;
    let lastError: unknown;
    // The Windows launcher starts the web server and API independently. Retry
    // short startup races so a new user does not mistake an unavailable API
    // for an empty workspace. A later manual refresh supersedes this request.
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const rows = await api<Project[]>('/projects', { signal: AbortSignal.timeout(3000), cache: 'no-store' });
        if (requestId !== projectsRequest.current) return null;
        setProjects(rows);
        setProjectsLoaded(true);
        setProjectsLoadError('');
        setNotice((current) => current?.kind === 'error' ? null : current);
        const remembered = window.localStorage.getItem('shine-project');
        const currentId = activeProjectId.current;
        const preferred = currentId || remembered;
        const nextId = rows.find((row) => row.id === preferred)?.id ?? (rows[0]?.id ?? '');
        if (nextId !== currentId) setProjectId(nextId);
        return nextId;
      } catch (error) {
        if (requestId !== projectsRequest.current) return null;
        lastError = error;
        if (attempt < attempts - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 500 * (2 ** attempt)));
        }
      }
    }
    if (requestId === projectsRequest.current) {
      const message = '暂时连接不上本机工作区。请检查本机服务是否已启动，然后点“重新连接”重试。';
      setProjectsLoadError(message);
      report(message, 'error');
    }
    throw lastError instanceof Error ? lastError : new Error('无法读取本机作品列表');
  }, []);

  const refreshWorkspace = useCallback(async (attempts = 4) => {
    try {
      const selectedId = await loadProjects(attempts);
      // If the list restored a remembered project, its normal selection effect
      // loads the detail. Avoid a duplicate request in the common case.
      if (selectedId && selectedId === activeProjectId.current) await loadProject(selectedId);
    } finally {
      setDataEpoch((epoch) => epoch + 1);
    }
  }, [loadProject, loadProjects]);

  const refreshAll = useCallback(async () => {
    await loadSystem();
    await refreshWorkspace();
  }, [loadSystem, refreshWorkspace]);

  useEffect(() => {
    void loadProjects().catch(() => undefined);
    return () => { projectsRequest.current += 1; };
  }, [loadProjects]);

  useEffect(() => {
    if (projectId) window.localStorage.setItem('shine-project', projectId);
  }, [projectId]);

  useEffect(() => {
    setAssets([]); setStyles([]); setShots([]); setJobs([]); setAudit(null);
    setWorkflowProfiles([]); setQueueProfile(''); setPreview(null); setStyle(emptyStyle);
    if (projectId) loadProject(projectId).catch((error: Error) => report(error.message, 'error'));
  }, [projectId, loadProject]);

  useEffect(() => {
    void loadSystem();
    const timer = window.setInterval(loadSystem, 10000);
    return () => window.clearInterval(timer);
  }, [loadSystem]);

  useEffect(() => {
    void checkConnection();
    const timer = window.setInterval(checkConnection, 3000);
    return () => window.clearInterval(timer);
  }, [checkConnection]);

  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      Promise.all([
        api<Job[]>(`/projects/${projectId}/jobs`),
        api<Shot[]>(`/projects/${projectId}/shots`),
      ]).then(([jobRows, shotRows]) => {
        if (activeProjectId.current !== projectId) return;
        setJobs(jobRows); setShots(shotRows);
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [projectId]);

  async function createProject() {
    if (!projectTitle.trim()) return;
    await withBusy(async () => {
      const created = await api<Project>('/projects', json('POST', { title: projectTitle.trim(), description: '' }));
      setProjectTitle(''); setProjects((rows) => [created, ...rows]); setProjectId(created.id);
      report('作品已建好。下一步：添加素材并确认，然后冻结风格。');
    });
  }

  async function saveStyle() {
    if (!projectId) return;
    await withBusy(async () => {
      await api(`/projects/${projectId}/style/draft`, json('PUT', style));
      await loadProject(projectId); report('风格草稿已保存，确认无误后可冻结。');
    });
  }

  async function freezeStyle() {
    if (!projectId) return;
    await withBusy(async () => {
      // Freeze exactly what the user sees: save the on-screen form first, then
      // request the approval grant against that saved snapshot, then freeze.
      // Carry the actual approved content too: another page may change the
      // saved draft before the grant is issued. The server checks both under
      // the same write lock as freezing.
      const snapshot: Style = { ...style };
      await api(`/projects/${projectId}/style/draft`, json('PUT', snapshot));
      const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
        json('POST', { action: 'style_freeze', target_id: projectId }));
      const frozen = await api<StyleVersion>(`/projects/${projectId}/style/freeze`, {
        ...json('POST', { approval_note: '', grant_id: grant.grant_id, snapshot: grant.snapshot, expected_style: snapshot }),
      });
      if (Object.entries(snapshot).some(([key, value]) => frozen.snapshot[key as keyof Style] !== value)) {
        throw new Error('冻结结果与本次确认的风格内容不一致，请核验版本记录；没有自动重试。');
      }
      if (activeProjectId.current !== projectId) return;
      await loadProject(projectId);
      report(`已冻结第 ${frozen.version} 版（哈希 ${frozen.sha256.slice(0, 12)}…），与屏幕上确认的七项一致；${frozen.impact?.affected_shot_ids.length ?? 0} 个旧镜头仍引用老版本。`);
    });
  }

  async function uploadAsset(file: File, meta: { kind: string; role: string; entityId: string; tags: string }) {
    if (!projectId) return;
    await withBusy(async () => {
      const form = new FormData();
      form.append('asset_kind', meta.kind);
      form.append('file', file);
      if (meta.role) form.append('role', meta.role);
      if (meta.entityId) form.append('entity_id', meta.entityId);
      if (meta.tags) form.append('tags', meta.tags);
      const asset = await api<Asset>(`/projects/${projectId}/assets`, { method: 'POST', body: form });
      await loadProject(projectId);
      report(`素材已上传（指纹 ${asset.sha256.slice(0, 8)}…），点击「确认使用」即可用于分镜。`);
    });
  }

  async function confirmAssets(assetIds: string[]) {
    if (!window.confirm(`确认使用这 ${assetIds.length} 项素材？请确认内容无误且你有权使用。确认后将固定素材版本。`)) return;
    await withBusy(async () => {
      let completed = 0;
      try {
        for (const assetId of assetIds) {
          const asset = assets.find(item => item.id === assetId);
          if (!asset || asset.locked) continue;
          for (const action of asset.approved ? ['lock'] : ['approve', 'lock']) {
            const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
              json('POST', { action: `asset_${action}`, target_id: assetId }));
            await api(`/assets/${assetId}/${action}`, json('POST', {
              grant_id: grant.grant_id, snapshot: grant.snapshot, offline: false,
              evidence: '用户在本机点击确认使用，并确认素材内容及使用权。',
            }));
          }
          completed += 1;
        }
        report(`已确认 ${completed} 项素材，可以用于分镜。`);
      } finally { await loadProject(projectId); }
    });
  }

  async function patchAssetMetadata(assetId: string, patch: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/assets/${assetId}/metadata`, json('PATCH', patch));
      await loadProject(projectId); report('素材信息已更新。');
    });
  }

  async function createShot(payload: Record<string, unknown>) {
    await withBusyReturn(async () => {
      await api(`/projects/${projectId}/shots`, json('POST', payload));
      // The shot now exists server-side: a failed refresh must not make the
      // user resubmit and create a duplicate. Surface the refresh failure
      // without reporting the create as failed.
      await loadProject(projectId).catch((refreshError: Error) => report(refreshError.message, 'error'));
      report('分镜已保存，记得给它绑定素材。');
    });
  }

  async function saveBindings(shotId: string, bindings: Record<string, unknown>[]) {
    await withBusyReturn(async () => {
      await api(`/shots/${shotId}/bindings`, json('PUT', { bindings }));
      await loadProject(projectId).catch((refreshError: Error) => report(refreshError.message, 'error'));
      setPreview(null); report('镜头绑定的素材已更新。');
    });
  }

  async function previewShot(shotId: string) {
    await withBusy(async () => {
      const result = await api<PromptPreview>(`/shots/${shotId}/compile`, { method: 'POST' });
      setPreview(result); report(`已生成 H3 提示词预览（方式：${result.mode}），仅供检查，不会提交。`);
    });
  }

  async function queueShot(shot: Shot) {
    // 单一真实模式来源：后端 production_mode 未允许时，真实选项根本不可提交，
    // 而不是提交后再失败。演示任务永远走 Mock。
    if (queueProvider === 'comfyui' && !realSubmissionAllowed) {
      report('真实制作尚未就绪，请先在顶部完成「启用真实制作」。', 'error');
      return;
    }
    const profile = queueProvider === 'comfyui' ? workflowProfiles.find(p => p.id === queueProfile) : undefined;
    const basic = queueProvider === 'comfyui' && reviewScope === 'basic';
    const width = Number(reviewWidth), height = Number(reviewHeight);
    if (queueProvider === 'comfyui') {
      if (!profile) { report('请先选择一个生成方案。', 'error'); return; }
      if (basic && (!Number.isInteger(width) || !Number.isInteger(height) || width < 2 || height < 2 || width > 8192 || height > 8192)) {
        report('基础自审需要填写预期画面宽高（2 到 8192 的整数，如 1280×720）。', 'error'); return;
      }
      if (!window.confirm(`确认用真实模式生成？\n镜头：${shot.name}\n方案：${profile.name}\n会占用本机显卡，耗时较长。`)) return;
    }
    await withBusy(async () => {
      await api(`/projects/${projectId}/jobs`, json('POST', {
        shot_id: shot.id, mock_scenario: mockScenario, provider: queueProvider,
        workflow_profile_id: profile?.id ?? null,
        generation_mode: profile?.mode ?? null,
        self_review_scope: basic ? 'basic' : 'full',
        basic_review_resolution: basic ? { width, height } : null,
      }));
      await loadProject(projectId); report('任务已加入本机队列，后台制作服务会自动领取，稍后可在下方查看进度。');
    });
  }

  async function togglePause(job: Job) {
    await withBusy(async () => {
      await api(`/jobs/${job.id}/${job.paused ? 'resume' : 'pause'}`, { method: 'POST' });
      await loadProject(projectId);
    });
  }

  async function loadJobQC(jobId: string): Promise<QCReport[]> {
    return api<QCReport[]>(`/jobs/${jobId}/qc`);
  }

  async function loadJobGates(jobId: string): Promise<QualityGate[]> {
    return api<QualityGate[]>(`/jobs/${jobId}/quality-gates`);
  }

  async function suggestRepair(jobId: string): Promise<RepairSuggestion> {
    return api<RepairSuggestion>(`/jobs/${jobId}/repair/suggest`, { method: 'POST' });
  }

  async function applyRepair(jobId: string, plan: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/jobs/${jobId}/repair?mock_scenario=success`, json('POST', plan));
      await loadProject(projectId); report('已按修复建议重新排队。');
    });
  }

  async function recheckReview(job: Job) {
    await withBusy(async () => {
      await api(`/jobs/${job.id}/review/recheck`, json('POST', { state_version: job.state_version, request_id: crypto.randomUUID() }));
      await loadProject(projectId); report('已重新排队机器审片，原视频和历史记录都会保留。');
    });
  }

  async function updateProjectSettings(patch: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/projects/${projectId}/settings`, json('PATCH', patch));
      await loadProject(projectId);
    });
  }

  async function createProfile(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api<ProviderProfile>('/provider-profiles', json('POST', payload));
      await loadSettings(projectId); report('模型配置已保存；密钥只存在本机，不会显示出来。');
    });
  }

  async function testProfile(profileId: string) {
    await withBusy(async () => {
      const result = await api<{ ok: boolean; message: string; error_code?: string | null; capabilities: Record<string, unknown> }>(`/provider-profiles/${profileId}/test`, { method: 'POST' });
      report(result.ok ? `连接成功，可用模型：${String(result.capabilities?.models ?? '')}` : `连接失败：${result.error_code ?? result.message}`, result.ok ? 'success' : 'error');
      await loadSettings(projectId);
    });
  }

  async function setProfileKey(profileId: string, key: string) {
    await withBusy(async () => {
      await api(`/provider-profiles/${profileId}/key`, json('PUT', { api_key: key }));
      await loadSettings(projectId); report('密钥已写入本机安全存储。');
    });
  }

  async function deleteProfile(profileId: string) {
    await withBusy(async () => {
      await api(`/provider-profiles/${profileId}`, { method: 'DELETE' });
      await loadSettings(projectId); report('模型配置已删除。');
    });
  }

  async function routeProfile(role: string, profileId: string) {
    await withBusy(async () => {
      await api<AgentRoute>('/agent-routes', json('PUT', { role, provider_profile_id: profileId }));
      await loadSettings(projectId); report('已保存该环节使用的模型。');
    });
  }

  async function testComfy(baseUrl: string): Promise<SystemHealth> {
    return withBusyReturn(async () => {
      const result = await api<SystemHealth>('/comfy/test', json('POST', { base_url: baseUrl, timeout_s: 10 }));
      if (result.ok) await loadSystem();
      return result;
    });
  }

  async function registerWorkflow(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api<WorkflowProfile>('/workflow-profiles', json('POST', payload));
      await loadSettings(projectId); report('生成方案已注册并通过结构校验。');
    });
  }

  async function deleteWorkflow(profileId: string) {
    await withBusy(async () => {
      await api(`/workflow-profiles/${profileId}`, { method: 'DELETE' });
      await loadSettings(projectId);
    });
  }

  async function abandonJob(jobId: string) {
    if (!window.confirm('放弃这次生成？已经生成的产物会保留，但系统不会自动重投。')) return;
    await withBusy(async () => {
      await api(`/operator/jobs/${jobId}/abandon`, json('POST', {
        evidence: 'local user abandoned an unreconciled render',
      }));
      await loadProject(projectId);
      report('已结束这次任务；产物保留，未重新提交。');
    });
  }

  async function validateWorkflow(profileId: string) {
    await withBusy(async () => {
      const result = await api<WorkflowProfile>(`/workflow-profiles/${profileId}/validate-managed`, json('POST', {}));
      await loadSettings(projectId);
      const validation = result.validated_against as { missing_nodes?: unknown[]; missing_inputs?: unknown[] } | null;
      report(validation && !validation.missing_nodes?.length && !validation.missing_inputs?.length
        ? '节点接口检查完成；仍需真实生成验证。' : '节点检查发现缺项，请展开验证详情。');
    });
  }

  /** Map the registered H3 node's own width/height inputs into the profile.
   *  Only ever adds a mapping the graph itself declares; a graph without a
   *  size input is refused by the server instead of being guessed. */
  async function bindStandardParameters(profileId: string) {
    await withBusy(async () => {
      const result = await api<WorkflowProfile>(`/workflow-profiles/${profileId}/standard-parameters`, json('POST', {}));
      await loadSettings(projectId);
      const mapped = 'width' in result.parameter_map && 'height' in result.parameter_map;
      report(mapped
        ? '已把 H3 节点的宽高输入接入画幅设置；下次开始制作即可按你选的分辨率渲染。'
        : '这个方案没有可接入的宽高输入。', mapped ? 'success' : 'error');
    });
  }

  return {
    // data
    projects, projectsLoaded, projectsLoadError, projectId, setProjectId, project, assets, styles, shots, jobs, style, setStyle,
    currentStyle, approvedAssets, projectTitle, setProjectTitle, mockScenario, setMockScenario,
    system, systemChecked, checkConnection, refreshWorkspace, production, profiles, routes, workflowProfiles, audit, preview, setPreview,
    notice, report, dismissNotice, busy, dataEpoch, apiOnline,
    // generation settings
    queueProvider, queueProfile, setQueueProfile, reviewScope, setReviewScope,
    reviewWidth, setReviewWidth, reviewHeight, setReviewHeight,
    realSubmissionAllowed, demoOverride, setDemoOverride,
    // loaders
    loadAudit, refreshAll,
    // actions
    createProject, saveStyle, freezeStyle, uploadAsset, confirmAssets, patchAssetMetadata,
    createShot, saveBindings, previewShot, queueShot, togglePause, loadJobQC, loadJobGates,
    suggestRepair, applyRepair, recheckReview, updateProjectSettings, createProfile, testProfile,
    setProfileKey, deleteProfile, routeProfile, testComfy, registerWorkflow, validateWorkflow, deleteWorkflow,
    bindStandardParameters,
    abandonJob,
  };
}
