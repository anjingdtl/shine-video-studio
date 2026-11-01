import { SetupPanel } from './components/SetupPanel';
import { PreproductionPanel } from './components/PreproductionPanel';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowDownToLine, BookOpenText, Check, ChevronDown, CircleHelp, Clapperboard, Clock3,
  Film, FolderKanban, ListChecks, LockKeyhole, PackageCheck, PenLine, RefreshCw, ShieldCheck,
  SlidersHorizontal, Sparkles, Users, Video, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
  api, json, type AgentRoute, type Asset, type AuditPage, type Job, type Project,
  type PromptPreview, type ProviderProfile, type QCReport, type QualityGate, type RepairSuggestion, type Shot,
  type Style, type StyleVersion, type SystemHealth, type SystemStatus, type WorkflowProfile,
} from './api';
import { emptyStyle } from './constants';
import AssetPanel from './components/AssetPanel';
import StylePanel from './components/StylePanel';
import ShotPanel from './components/ShotPanel';
import ProductionPanel from './components/ProductionPanel';
import ProductionSeriesPanel from './components/ProductionSeriesPanel';
import SettingsPanel from './components/SettingsPanel';
import AuditPanel from './components/AuditPanel';
import FilmCrewPanel from './components/FilmCrewPanel';
import StoryboardPanel from './components/StoryboardPanel';
import DeliveryPanel from './components/DeliveryPanel';
import TerminologyPanel, { SubtitlePanel } from './components/TerminologyPanel';
import PreferencesPanel from './components/PreferencesPanel';
import StorageSettings from './components/StorageSettings';

type ViewId = 'setup' | 'production' | 'delivery' | 'pro';
type NavItem = { id: string; text: string; icon: LucideIcon };
type ViewGroup = { view: ViewId; title: string; hint: string; core?: boolean; items: NavItem[] };

/** 四个独立板块：导航结构与页面视图一一对应，互不混放。 */
const VIEW_GROUPS: ViewGroup[] = [
  {
    view: 'setup', title: '设置板块', hint: '装机 · 模型 · 偏好', items: [
      { id: 'setup', text: '制作环境', icon: ShieldCheck },
      { id: 'settings', text: '模型与系统', icon: SlidersHorizontal },
      { id: 'preferences', text: '显示与偏好', icon: Sparkles },
    ],
  },
  {
    view: 'production', title: '生产板块', hint: '从素材到镜头成片', core: true, items: [
      { id: 'overview', text: '项目概览', icon: FolderKanban },
      { id: 'assets', text: '素材与风格', icon: Film },
      { id: 'preproduction', text: '制片准备', icon: ListChecks },
      { id: 'storyboard', text: 'AI 自动分镜', icon: PenLine },
      { id: 'terminology', text: '人名与字幕', icon: BookOpenText },
      { id: 'production', text: '分镜与任务', icon: Activity },
      { id: 'production-series', text: '多镜头连续生产', icon: Video },
    ],
  },
  {
    view: 'delivery', title: '交付板块', hint: '成片 · 导出 · 记录', items: [
      { id: 'delivery', text: '成片与导出', icon: ArrowDownToLine },
      { id: 'audit', text: '审计追踪', icon: Clock3 },
    ],
  },
  {
    view: 'pro', title: '专业板块', hint: '制片团队 · H3 规范', items: [
      { id: 'film-crew', text: '影视制片团队', icon: Users },
      { id: 'h3-rulebook', text: 'H3 官方规范', icon: PackageCheck },
    ],
  },
];
const VIEW_IDS: ViewId[] = VIEW_GROUPS.map((group) => group.view);
const ANCHOR_VIEW: Record<string, ViewId> = Object.fromEntries(
  VIEW_GROUPS.flatMap((group) => group.items.map((item) => [item.id, group.view])),
);
const NAV_IDS = VIEW_GROUPS.flatMap((group) => group.items.map((item) => item.id));
const VIEW_TITLES: Record<ViewId, string> = Object.fromEntries(
  VIEW_GROUPS.map((group) => [group.view, group.title]),
) as Record<ViewId, string>;

function resolveHash(hash: string): { view: ViewId; anchor?: string } {
  const raw = hash.replace(/^#/, '');
  if (raw.startsWith('v/')) {
    const [, viewPart, anchorPart] = raw.split('/');
    const view = (VIEW_IDS as string[]).includes(viewPart) ? viewPart as ViewId : 'production';
    return { view, anchor: anchorPart || undefined };
  }
  const anchorView = ANCHOR_VIEW[raw];
  if (anchorView) return { view: anchorView, anchor: raw };
  return { view: 'production' };
}

/** 启动时恢复显示偏好（主题 / 字号缩放）。 */
const savedTheme = window.localStorage.getItem('shine-theme');
if (savedTheme === 'dark' || savedTheme === 'light') document.documentElement.dataset.theme = savedTheme;
const savedZoom = window.localStorage.getItem('shine-font-zoom');
if (savedZoom) document.documentElement.style.zoom = savedZoom;

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const activeProjectId = useRef(projectId);
  activeProjectId.current = projectId;
  const [assets, setAssets] = useState<Asset[]>([]);
  const [styles, setStyles] = useState<StyleVersion[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [style, setStyle] = useState<Style>(emptyStyle);
  const [projectTitle, setProjectTitle] = useState('');
  const [mockScenario, setMockScenario] = useState('success');
  const [system, setSystem] = useState<SystemStatus | null>(null);
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
  const [queueProvider, setQueueProvider] = useState('mock');
  const [queueProfile, setQueueProfile] = useState('');
  const [reviewScope, setReviewScope] = useState<'basic' | 'full'>('basic');
  const [reviewWidth, setReviewWidth] = useState('');
  const [reviewHeight, setReviewHeight] = useState('');
  const [activeSection, setActiveSection] = useState('overview');
  const [helpOpen, setHelpOpen] = useState(false);
  /** Nav density contract: the current view's group is expanded by default; other groups expand only via their explicit toggle button. */
  const [manualExpanded, setManualExpanded] = useState<Set<ViewId>>(() => new Set());
  const [initialRoute] = useState(() => resolveHash(window.location.hash));
  const [view, setView] = useState<ViewId>(initialRoute.view);
  const viewScrollRef = useRef<Partial<Record<ViewId, number>>>({});
  const viewMountedRef = useRef(false);

  const project = projects.find((item) => item.id === projectId);
  const currentStyle = styles.find((item) => item.id === project?.frozen_style_version_id);
  const approvedAssets = useMemo(() => assets.filter((asset) => asset.approved && asset.locked), [assets]);

  function report(text: string, kind: 'success' | 'error' = 'success') {
    setNotice({ text, kind });
    window.setTimeout(() => setNotice(null), 5200);
  }

  async function withBusy(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { report((error as Error).message, 'error'); }
    finally { setBusy(false); }
  }

  const loadSystem = useCallback(async () => {
    try { setSystem(await api<SystemStatus>('/system/status')); }
    catch { setSystem(null); }
  }, []);

  const loadAudit = useCallback(async (id: string, offset = 0) => {
    const page = await api<AuditPage>(`/projects/${id}/events?limit=30&offset=${offset}`);
    if (activeProjectId.current !== id) return;
    setAudit((current) => offset === 0 || !current
      ? page
      : { ...page, items: [...current.items, ...page.items] });
  }, []);

  const loadSettings = useCallback(async (id: string) => {
    const [profileRows, routeRows, workflowRows] = await Promise.all([
      api<ProviderProfile[]>('/provider-profiles'),
      api<AgentRoute[]>('/agent-routes'),
      api<WorkflowProfile[]>(`/workflow-profiles?project_id=${id}`),
    ]);
    if (activeProjectId.current !== id) return;
    setProfiles(profileRows); setRoutes(routeRows); setWorkflowProfiles(workflowRows);
  }, []);

  const loadProject = useCallback(async (id: string) => {
    const selected = await api<Project>(`/projects/${id}`);
    if (activeProjectId.current !== id) return;
    setProjects((rows) => rows.map((row) => row.id === id ? selected : row));
    const [assetRows, styleRows, shotRows, jobRows] = await Promise.all([
      api<Asset[]>(`/projects/${id}/assets`),
      api<StyleVersion[]>(`/projects/${id}/styles`),
      api<Shot[]>(`/projects/${id}/shots`),
      api<Job[]>(`/projects/${id}/jobs`),
    ]);
    if (activeProjectId.current !== id) return;
    setAssets(assetRows); setStyles(styleRows); setShots(shotRows); setJobs(jobRows);
    setStyle(selected.style_draft ?? styleRows.at(-1)?.snapshot ?? emptyStyle);
    await loadSettings(id);
    await loadAudit(id, 0);
  }, [loadAudit, loadSettings]);

  const refreshAll = useCallback(async () => {
    await loadSystem();
    if (projectId) await loadProject(projectId);
    setDataEpoch((epoch) => epoch + 1);
  }, [loadSystem, loadProject, projectId]);

  useEffect(() => {
    api<Project[]>('/projects').then((rows) => {
      setProjects(rows);
      if (rows.length) setProjectId(rows[0].id);
    }).catch((error: Error) => report(error.message, 'error'));
  }, []);

  useEffect(() => {
    setAssets([]); setStyles([]); setShots([]); setJobs([]); setAudit(null);
    setWorkflowProfiles([]); setQueueProfile(''); setPreview(null); setStyle(emptyStyle);
    if (projectId) loadProject(projectId).catch((error: Error) => report(error.message, 'error'));
  }, [projectId, loadProject]);

  useEffect(() => { loadSystem(); }, [loadSystem]);

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
      loadSystem();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [projectId, loadSystem]);

  /** 滚动侦测：板块进入视口上部条带时菜单联动高亮（仅当前视图内的锚点会交叉）。
   *  点击菜单后按滚动距离锁定目标，平滑滚动途中不被旧板块抢回高亮；
   *  多板块同在观察带时取"最深入条带"（rect.top 最大）者。 */
  const spiedRef = useRef(false);
  const navLockRef = useRef<{ id: string; until: number } | null>(null);
  useEffect(() => {
    if (!project) return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      }
      let best: { id: string; top: number } | null = null;
      for (const id of NAV_IDS) {
        if (!visible.has(id)) continue;
        const element = document.getElementById(id);
        if (!element) continue;
        const top = element.getBoundingClientRect().top;
        if (!best || top > best.top) best = { id, top };
      }
      if (!best) return;
      spiedRef.current = true;
      const lock = navLockRef.current;
      if (lock && Date.now() < lock.until) { setActiveSection(lock.id); return; }
      setActiveSection(best.id);
    }, { rootMargin: '0px 0px -72% 0px', threshold: 0 });
    const raf = window.requestAnimationFrame(() => {
      NAV_IDS.forEach((id) => {
        const element = document.getElementById(id);
        if (element) observer.observe(element);
      });
    });
    return () => { window.cancelAnimationFrame(raf); observer.disconnect(); };
  }, [project, shots, jobs]);

  /** 滚动侦测触发后，地址栏锚点与当前视图/板块保持一致。 */
  useEffect(() => {
    if (!spiedRef.current) return;
    const next = `#v/${view}/${activeSection}`;
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  }, [activeSection, view]);

  useEffect(() => {
    if (!helpOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setHelpOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [helpOpen]);

  /** 切换视图：记忆上一个视图的滚动位置，回到目标视图时恢复。 */
  function switchView(next: ViewId, restoreScroll = true, delay = 0) {
    if (next === view) return;
    viewScrollRef.current[view] = window.scrollY;
    setView(next);
    setActiveSection(VIEW_GROUPS.find((group) => group.view === next)!.items[0].id);
    const y = restoreScroll ? viewScrollRef.current[next] ?? 0 : 0;
    window.setTimeout(() => { window.scrollTo({ top: y }); }, delay);
  }

  /** 浏览器前进/后退：按地址栏恢复视图与锚点。 */
  useEffect(() => {
    const onPop = () => {
      const target = resolveHash(window.location.hash);
      if (target.anchor) {
        const anchorView = ANCHOR_VIEW[target.anchor] ?? target.view;
        switchView(anchorView, false, 30);
        setActiveSection(target.anchor);
        navLockRef.current = { id: target.anchor, until: Date.now() + 1500 };
        window.setTimeout(() => {
          document.getElementById(target.anchor!)?.scrollIntoView({ behavior: 'auto', block: 'start' });
        }, 60);
      } else {
        switchView(target.view, true, 30);
        window.history.replaceState(null, '', `#v/${target.view}`);
      }
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  });

  /** 启动深链（如安装器落地 #setup 或 #v/production/assets）。 */
  useEffect(() => {
    if (viewMountedRef.current || !initialRoute.anchor) return;
    viewMountedRef.current = true;
    const anchor = initialRoute.anchor;
    const timer = window.setTimeout(() => {
      document.getElementById(anchor)?.scrollIntoView({ behavior: 'auto', block: 'start' });
      setActiveSection(anchor);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [initialRoute, project]);

  function goTo(id: string) {
    const targetView = ANCHOR_VIEW[id] ?? view;
    setActiveSection(id);
    const runScroll = (behavior: ScrollBehavior) => {
      if (id === 'overview') window.scrollTo({ top: 0, behavior });
      else document.getElementById(id)?.scrollIntoView({ behavior, block: 'start' });
    };
    if (targetView !== view) {
      // 跨视图：布局刚从 display:none 恢复，用瞬时定位最可靠；平滑滚动可能停在半路。
      navLockRef.current = { id, until: Date.now() + 1200 };
      switchView(targetView, false);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        runScroll('auto');
        navLockRef.current = { id, until: Date.now() + 700 };
      }));
      window.history.pushState(null, '', `#v/${targetView}/${id}`);
      return;
    }
    runScroll('smooth');
    const distance = id !== 'overview'
      ? Math.abs(document.getElementById(id)?.getBoundingClientRect().top ?? 0)
      : window.scrollY;
    navLockRef.current = { id, until: Date.now() + Math.min(2200, 600 + distance * 0.35) };
    window.history.pushState(null, '', `#v/${targetView}/${id}`);
  }

  async function createProject() {
    if (!projectTitle.trim()) return;
    await withBusy(async () => {
      const created = await api<Project>('/projects', json('POST', { title: projectTitle.trim(), description: '' }));
      setProjectTitle(''); setProjects((rows) => [created, ...rows]); setProjectId(created.id);
      report('项目已建好。下一步：上传素材并批准，然后冻结风格。');
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
    const profile = queueProvider === 'comfyui' ? workflowProfiles.find(p => p.id === queueProfile) : undefined;
    const basic = queueProvider === 'comfyui' && reviewScope === 'basic';
    const width = Number(reviewWidth), height = Number(reviewHeight);
    if (queueProvider === 'comfyui') {
      if (!profile) { report('请先在下方选择一个生成工作流模板。', 'error'); return; }
      if (basic && (!Number.isInteger(width) || !Number.isInteger(height) || width < 2 || height < 2 || width > 8192 || height > 8192)) {
        report('基础自审需要填写预期画面宽高（2 到 8192 的整数，如 1280×720）。', 'error'); return;
      }
      if (!window.confirm(`确认用真实模式生成？\n镜头：${shot.name}\n模板：${profile.name}\n会占用本机显卡，耗时较长。`)) return;
    }
    await withBusy(async () => {
      await api(`/projects/${projectId}/jobs`, json('POST', {
        shot_id: shot.id, mock_scenario: mockScenario, provider: queueProvider,
        workflow_profile_id: profile?.id ?? null,
        generation_mode: profile?.mode ?? null,
        self_review_scope: basic ? 'basic' : 'full',
        basic_review_resolution: basic ? { width, height } : null,
      }));
      await loadProject(projectId); report('任务已加入本机队列，后台执行器会自动领取，稍后可在下方查看进度。');
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

  async function withBusyReturn<T>(action: () => Promise<T>): Promise<T> {
    setBusy(true);
    try { return await action(); }
    catch (error) { report((error as Error).message, 'error'); throw error; }
    finally { setBusy(false); }
  }

  async function registerWorkflow(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api<WorkflowProfile>('/workflow-profiles', json('POST', payload));
      await loadSettings(projectId); report('工作流模板已注册并通过结构校验。');
    });
  }

  async function deleteWorkflow(profileId: string) {
    await withBusy(async () => {
      await api(`/workflow-profiles/${profileId}`, { method: 'DELETE' });
      await loadSettings(projectId);
    });
  }

  const apiOnline = system?.api_online ?? false;
  const modeLabel = system?.render_mode === 'comfyui' ? '真实生成' : '演示模式';

  const viewHero = (title: string, desc: string, extra?: string) => (
    <section className="view-hero">
      <div>
        <div className="eyebrow"><span className="eyebrow-line" /> {title} <span className="eyebrow-en">{extra}</span></div>
        <h1>{desc}</h1>
      </div>
    </section>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Clapperboard size={19} /></div><div><b>SHINE</b><span>VIDEO STUDIO</span></div></div>
        <nav className="nav-groups" aria-label="工作区导航">
          {VIEW_GROUPS.map((group) => {
            const isCurrent = view === group.view;
            const isExpanded = isCurrent || manualExpanded.has(group.view);
            return (
            <div className={`nav-group ${group.core ? 'core' : ''} ${isCurrent ? 'current' : ''} ${isExpanded ? 'expanded' : ''}`} key={group.view}>
              <div className="nav-view-head-row">
                <button
                  className={`nav-view-head ${isCurrent ? 'active' : ''}`}
                  title={`打开${group.title}`}
                  onClick={() => {
                    switchView(group.view);
                    window.history.pushState(null, '', `#v/${group.view}`);
                  }}
                >
                  <span className="nav-view-title">{group.title}</span>
                  <span className="nav-view-hint">{group.hint}</span>
                </button>
                <button
                  type="button"
                  className="nav-view-toggle"
                  aria-expanded={isExpanded}
                  aria-label={`${isExpanded ? '收起' : '展开'}${group.title}子项`}
                  title={`${isExpanded ? '收起' : '展开'}${group.title}子项`}
                  onClick={() => {
                    setManualExpanded((current) => {
                      const next = new Set(current);
                      if (next.has(group.view)) next.delete(group.view);
                      else next.add(group.view);
                      return next;
                    });
                  }}
                >
                  <ChevronDown size={13} className="nav-view-caret caret-in-toggle" />
                </button>
              </div>
              {isExpanded && group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    title={item.text}
                    className={`nav-item ${activeSection === item.id ? 'active' : ''}`}
                    onClick={() => goTo(item.id)}
                  >
                    <Icon size={17} />{item.text}
                    <span className="nav-dot" />
                  </button>
                );
              })}
            </div>
            );
          })}
        </nav>
        <div className="privacy-card"><ShieldCheck size={17} /><div><b>本地优先</b><span>素材与密钥不离开本机</span></div><Check size={15} /></div>
        <div className="profile"><div className="avatar">S</div><div><b>本地工作区</b><span>{apiOnline ? '已连接本机服务' : '本机服务未连接'}</span></div><ChevronDown size={16} /></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作区</span><span className="crumb-slash">/</span>
            <b>{VIEW_TITLES[view]}</b>
            {project && <><span className="crumb-slash">/</span><span>{project.title}</span></>}
          </div>
          <div className="top-actions">
            {projects.length > 1 && <select className="topbar-project" aria-label="快速切换项目" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
            </select>}
            <div className="status-cluster">
              <span className="service-status" title="本机网页服务是否在线"><span className={`status-led ${apiOnline ? 'mock' : 'offline'}`} />网页服务</span>
              <span className="service-status" title="负责执行生成任务的后台程序是否在线"><span className={`status-led ${system?.worker_online ? 'mock' : 'offline'}`} />后台执行器</span>
              <span className="service-status" title="演示模式引擎，用于不消耗显卡的流程演练"><span className={`status-led ${system?.mock_provider_available ? 'mock' : 'offline'}`} />演示引擎</span>
              <span className="service-status" title="本机 ComfyUI + H3 模型是否已连接"><span className={`status-led ${system?.h3_connected ? 'mock' : 'offline'}`} />H3 真机</span>
            </div>
            <span className={`mode-badge ${system?.render_mode === 'comfyui' ? 'real' : ''}`} title={system?.render_mode === 'comfyui' ? '任务会真实调用本机 ComfyUI + H3 生成视频' : '演示模式只模拟流程，不会生成真视频'}>{modeLabel}</span>
            <button className="icon-button" title="刷新全部数据" onClick={() => refreshAll().catch((error: Error) => report(error.message, 'error'))}><RefreshCw size={15} /></button>
            <button className="help-button" onClick={() => setHelpOpen(true)}><CircleHelp size={15} /> 帮助</button>
          </div>
        </header>

        <div className="page-wrap">
          {!project && <>
            {/* 空库也能进入设置板块：环境检查、上游可达性、部署清单、安装目录和偏好都不依赖项目。 */}
            {view === 'setup' && <>
              {viewHero('设置板块', '先把工具调教顺手，再开工。', 'SYSTEM SETUP')}
              <SetupPanel key="setup-no-project" projectId="" onRefresh={refreshAll} />
              <StorageSettings />
              <PreferencesPanel onNavigate={goTo} />
            </>}
            <section className="empty-project card">
              <div className="empty-art"><FolderKanban size={26} /></div>
              <h2>{view === 'setup' ? '接下来，从第一个项目开始' : '从一个项目开始'}</h2>
              <p>{view === 'setup'
                ? '上面的环境检查、安装目录和系统探测不需要项目。生产、交付等专业流程需要一个项目来承载：'
                : '所有内容都保存在你自己电脑上：项目、素材、风格、任务和操作记录。'}</p>
              <div className="create-project">
                <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="给项目起个名字" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
                <button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建项目</button>
              </div>
              {view !== 'setup' && <p className="empty-setup-hint">
                还没装机？<button className="text-button" onClick={() => { switchView('setup'); window.history.pushState(null, '', '#v/setup'); }}>先去「制作环境」检查本机</button>——不需要先建项目。
              </p>}
            </section>
          </>}

          {project && <>
            {/* 生产板块：从素材到镜头成片的主流程 */}
            <div className={`view-page ${view === 'production' ? '' : 'hidden'}`} data-view="production">
              <section className="welcome-row">
                <div>
                  <div className="eyebrow"><span className="eyebrow-line" /> 生产板块 <span className="eyebrow-en">PRODUCTION</span></div>
                  <h1>让好故事，<em>有章可循。</em></h1>
                  <p>先备好素材、定死风格，再交给本机 AI 一镜一镜地生成——每一步你说了算。</p>
                </div>
                <div className="date-chip"><Clock3 size={14} /> 本地项目 <span>·</span> {projects.length} 个</div>
              </section>

              <section className="project-selector card" id="overview">
                <div className="project-icon"><Clapperboard size={20} /></div>
                <div className="project-meta"><span>当前制片项目</span><b>{project.title}</b></div>
                <select aria-label="切换项目" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select>
                <div className="selector-divider" />
                <input className="new-project-inline" value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="新项目名称" />
                <button className="subtle-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建项目</button>
              </section>

              <section className="metrics-grid">
                <div className="metric-card"><div className="metric-top"><span>项目素材</span><div className="metric-icon mint"><Film size={15} /></div></div><div className="metric-value">{assets.length}<small>项</small></div><div className="metric-foot">{approvedAssets.length} 项已批准并锁定</div></div>
                <div className="metric-card"><div className="metric-top"><span>风格基准</span><div className="metric-icon sand"><LockKeyhole size={15} /></div></div><div className="metric-value">{currentStyle ? `第 ${currentStyle.version} 版` : '—'}</div><div className="metric-foot">{currentStyle ? '已冻结，全部镜头按此执行' : '填好七项风格约定后由你冻结'}</div></div>
                <div className="metric-card"><div className="metric-top"><span>生成任务</span><div className="metric-icon blue"><Activity size={15} /></div></div><div className="metric-value">{jobs.length}<small>个</small></div><div className="metric-foot">{jobs.filter((job) => job.status === 'PASSED').length} 个完成 · {jobs.filter((job) => job.acceptance_status === 'accepted').length} 个验收通过 · {jobs.filter((job) => job.acceptance_status === 'pending_review').length} 个等你验收</div></div>
                <div className="metric-card accent-metric"><div className="metric-top"><span>运行模式</span><div className="metric-icon cream"><Sparkles size={15} /></div></div><div className="metric-value small-value">{modeLabel}</div><div className="metric-foot">{system?.h3_connected ? '本机 H3 已就绪' : '本机 H3 未连接 · 暂无法真实生成'}</div></div>
              </section>

              <AssetPanel
                assets={assets}
                busy={busy}
                onUpload={uploadAsset}
                onConfirm={confirmAssets}
                onPatchMetadata={patchAssetMetadata}
              />

              <StylePanel
                key={`style-${projectId}`}
                projectId={projectId}
                style={style}
                setStyle={setStyle}
                serverDraft={project.style_draft}
                currentStyle={currentStyle}
                styles={styles}
                busy={busy}
                onSave={saveStyle}
                onFreeze={freezeStyle}
                onDraftApplied={() => { loadProject(projectId).catch(() => undefined); }}
                onNavigate={goTo}
                onMessage={report}
              />

              <PreproductionPanel key={`prep-${projectId}`} projectId={projectId} onRefresh={refreshAll} reviewScope={reviewScope} />

              <StoryboardPanel key={`storyboard-${projectId}`} projectId={projectId} onRefresh={refreshAll} />

              <TerminologyPanel
                key={`terminology-${projectId}`}
                projectId={projectId}
                busy={busy}
                onMessage={report}
              />

              <SubtitlePanel
                key={`subtitles-${projectId}`}
                projectId={projectId}
                busy={busy}
                onMessage={report}
              />

              <section className="production-layout" id="production">
                <div className="queue-provider-card card">
                  <div className="section-kicker">生成方式</div>
                  <p className="queue-provider-hint">这里决定点「加入队列」后发生什么。演示模式用于熟悉流程，不消耗显卡、不出真视频；真实模式调用本机 ComfyUI + H3。</p>
                  <label>生成方式<select value={queueProvider} onChange={e => setQueueProvider(e.target.value)}>
                    <option value="mock">演示模式（默认，不出真视频）</option><option value="comfyui">真实生成（走本机显卡，每次确认）</option>
                  </select></label>
                  {queueProvider === 'comfyui' && <label>生成模板<select value={queueProfile} onChange={e => setQueueProfile(e.target.value)}>
                    <option value="">请选择…</option>{workflowProfiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.mode}</option>)}
                  </select><small>模板在「模型与系统」里注册。</small></label>}
                  {queueProvider === 'comfyui' && <>
                    <label>自动检查范围<select value={reviewScope} onChange={e => setReviewScope(e.target.value as 'basic' | 'full')}>
                      <option value="basic">基础检查：分辨率、人物、对白</option>
                      <option value="full">完整五道质量关</option>
                    </select></label>
                    {reviewScope === 'basic' && <>
                      <label>预期画面宽度<input type="number" min="2" max="8192" step="1" value={reviewWidth} onChange={e => setReviewWidth(e.target.value)} /></label>
                      <label>预期画面高度<input type="number" min="2" max="8192" step="1" value={reviewHeight} onChange={e => setReviewHeight(e.target.value)} /></label>
                      <p>宽高只用来核对生成结果对不对，不会改动生成设置。人物检查需要先配好本机基础审片能力。</p>
                    </>}
                  </>}
                </div>
                <ShotPanel
                  assets={assets}
                  shots={shots}
                  currentStyle={currentStyle}
                  busy={busy}
                  preview={preview}
                  onPreview={previewShot}
                  onQueue={queueShot}
                  onCreate={createShot}
                  onSaveBindings={saveBindings}
                  setPreview={setPreview}
                />
                <ProductionPanel
                  jobs={jobs}
                  system={system}
                  busy={busy}
                  scenario={mockScenario}
                  setScenario={setMockScenario}
                  onTogglePause={togglePause}
                  onLoadQC={loadJobQC}
                  onLoadGates={loadJobGates}
                  onSuggest={suggestRepair}
                  onRepair={applyRepair}
                  onRecheck={recheckReview}
                />
              </section>

              <ProductionSeriesPanel
                key={`series-${projectId}`}
                projectId={projectId}
                shots={shots}
                workflowProfiles={workflowProfiles}
                system={system}
                busy={busy}
                reviewWidth={reviewWidth}
                reviewHeight={reviewHeight}
                onStarted={() => refreshAll().catch(() => undefined)}
                onMessage={report}
              />

              <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> V1.1 DEVELOPMENT</span><span><ShieldCheck size={12} /> 本地数据库 · 素材由你掌控</span></footer>
            </div>

            {/* 设置板块：装机、模型与系统、个人偏好 */}
            <div className={`view-page ${view === 'setup' ? '' : 'hidden'}`} data-view="setup">
              {viewHero('设置板块', '先把工具调教顺手，再开工。', 'SYSTEM SETUP')}
              <SetupPanel key={`setup-${projectId}`} projectId={projectId} onRefresh={refreshAll} />
              <SettingsPanel
                project={project}
                profiles={profiles}
                routes={routes}
                workflowProfiles={workflowProfiles}
                system={system}
                busy={busy}
                onProjectSettings={updateProjectSettings}
                onCreateProfile={createProfile}
                onTestProfile={testProfile}
                onSetKey={setProfileKey}
                onDeleteProfile={deleteProfile}
                onRoute={routeProfile}
                onTestComfy={testComfy}
                onRegisterWorkflow={registerWorkflow}
                onDeleteWorkflow={deleteWorkflow}
              />
              <PreferencesPanel onNavigate={goTo} />
            </div>

            {/* 交付板块：成片与记录 */}
            <div className={`view-page ${view === 'delivery' ? '' : 'hidden'}`} data-view="delivery">
              {viewHero('交付板块', '成片验收、导出与全程留痕。', 'DELIVERY')}
              <DeliveryPanel key={`delivery-${projectId}`} projectId={projectId} jobs={jobs} active={view === 'delivery'} refreshEpoch={dataEpoch} />
              <AuditPanel
                page={audit}
                busy={busy}
                onLoad={() => loadAudit(projectId, 0).catch((error: Error) => report(error.message, 'error'))}
                onMore={() => loadAudit(projectId, audit?.items.length ?? 0).catch((error: Error) => report(error.message, 'error'))}
              />
            </div>

            {/* 专业板块：影视制片团队与 H3 规范 */}
            <div className={`view-page ${view === 'pro' ? '' : 'hidden'}`} data-view="pro">
              {viewHero('专业板块', '行业工种协作与官方规范中心。', 'PROFESSIONAL')}
              <FilmCrewPanel key={projectId} projectId={projectId} profiles={profiles} shots={shots} onRefresh={refreshAll} />
            </div>
          </>}
        </div>
      </main>

      {helpOpen && <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="帮助" onClick={() => setHelpOpen(false)}>
        <div className="help-modal" onClick={(event) => event.stopPropagation()}>
          <button className="modal-close" aria-label="关闭帮助" onClick={() => setHelpOpen(false)}><X size={16} /></button>
          <h2>三分钟看懂这个工作台</h2>
          <p className="help-lead">这是一台装在你自己电脑上的「迷你制片厂」：你准备素材和风格，AI 按你的安排一镜一镜生成视频。所有素材、记录都留在本机，不经过云端。工作台分四个板块：<b>设置</b>管装机与模型，<b>生产</b>走素材→分镜→生成主流程，<b>交付</b>看成片和记录，<b>专业</b>放行业工具。</p>
          <div className="help-section">
            <h3>推荐的顺序</h3>
            <ol className="help-steps">
              <li><b>装机</b>（仅首次）— 在「设置板块 → 制作环境」自检环境、复用已有模型。</li>
              <li><b>传素材</b> — 「生产板块 → 素材与风格」上传并由你「批准 + 锁定」。</li>
              <li><b>定风格</b> — 填七项风格约定并「冻结」，之后所有镜头都按它执行。</li>
              <li><b>排分镜</b> — 手写或 AI 自动分镜，给每个镜头绑上素材。</li>
              <li><b>生成</b> — 先用「演示模式」走通流程，确认无误再切「真实生成」。</li>
              <li><b>验收</b> — 「交付板块」看成片和质检，该人工看的地方必须真人看完。</li>
            </ol>
          </div>
          <div className="help-section">
            <h3>看不懂的词</h3>
            <ul className="help-glossary">
              <li><b>演示模式 / Mock</b> — 模拟整个流程但不出真视频，用来练手和排查问题。</li>
              <li><b>真实生成 / ComfyUI / H3</b> — 调用你本机的显卡和 H3 模型，产出真视频。</li>
              <li><b>风格基准 / Style Bible</b> — 冻结后的风格约定版本，镜头、质检、修复都以它为准。</li>
              <li><b>需人工复核</b> — 系统拿不准（比如提交结果不明、质检低置信），必须由人决定下一步，不会自动重试。</li>
              <li><b>审计追踪</b> — 每一步谁做了什么的完整记录，可回查、不可篡改。</li>
            </ul>
          </div>
          <div className="help-section">
            <h3>遇到问题</h3>
            <ul className="help-glossary">
              <li>页面数据不对 → 点右上角「刷新」。</li>
              <li>任务不动了 → 「设置板块 → 模型与系统」看后台执行器是否在线。</li>
              <li>新电脑第一次用 → 先走「设置板块 → 制作环境」自检。</li>
            </ul>
          </div>
        </div>
      </div>}

      {notice && <div className={`toast ${notice.kind}`}><span>{notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}</span>{notice.text}<button onClick={() => setNotice(null)}><X size={14} /></button></div>}
      {busy && <div className="busy-indicator"><ArrowDownToLine size={14} />处理中</div>}
    </div>
  );
}

export default App;
