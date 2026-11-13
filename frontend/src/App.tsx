import { SetupPanel } from './components/SetupPanel';
import DirectorSetupPanel from './components/DirectorSetupPanel';
import { PreproductionPanel } from './components/PreproductionPanel';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Activity, AlertTriangle, ArrowDownToLine, BookOpenText, Check, ChevronDown, CircleHelp, Clapperboard, Clock3,
  Film, FolderKanban, Images, ListChecks, LockKeyhole, PackageCheck, PenLine, Plus, RefreshCw, Settings2,
  ShieldCheck, SlidersHorizontal, Sparkles, Users, Video, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useStudio } from './useStudio';
import { DemoRibbon, ModeStatusEntry } from './components/ModeStatusEntry';
import { EngineStatusEntry } from './components/EngineStatusEntry';
import { RealProductionWizard } from './components/RealProductionWizard';
import AssetPanel from './components/AssetPanel';
import { MaterialPreparationPanel } from './components/MaterialPreparationPanel';
import { FourStagePanel } from './components/FourStagePanel';
import { DirectorStoryboardStage } from './components/StoryboardDraftSection';
import { ScriptEntryPanel } from './components/ScriptEntryPanel';
import { SchemaNotice } from './components/SchemaNotice';
import StylePanel from './components/StylePanel';
import ShotPanel from './components/ShotPanel';
import ProductionPanel from './components/ProductionPanel';
import ProductionSeriesPanel from './components/ProductionSeriesPanel';
import SettingsPanel from './components/SettingsPanel';
import AuditPanel from './components/AuditPanel';
import FilmCrewPanel from './components/FilmCrewPanel';
import StoryboardPanel from './components/StoryboardPanel';
import DeliveryPanel from './components/DeliveryPanel';
import VideoLibraryPanel from './components/VideoLibraryPanel';
import TerminologyPanel, { SubtitlePanel } from './components/TerminologyPanel';
import PreferencesPanel from './components/PreferencesPanel';
import { DirectorProductionRunPanel } from './components/DirectorProductionRunPanel';
import { DirectorShootPanel } from './components/DirectorShootPanel';
import { EpisodeStoryboardPanel, ShotMaterialsPanel, StoryboardBookPanel } from './components/StreamlinePanels';
import { JourneyBridge, JourneyRail } from './components/JourneyRail';
import { StageRailProvider } from './components/StageRail';
import { Meter } from './components/Meter';
import { WorkStackMonitor } from './components/WorkStackMonitor';
import { GuideTip } from './components/GuideTip';
import type { RailBlocker } from './components/ActionRail';
import { describeBlocker, type ErrorActionTarget } from './errorActions';
import { ProjectPortfolio } from './components/ProjectPortfolio';
import { getDirectorOverview, getJourney, type DirectorOverview, type Journey, type JourneyStage } from './api';
import StorageSettings from './components/StorageSettings';
import { AssetsWorkspace, type AssetCenterPage } from './asset-center/AssetsWorkspace';
import type { Asset, Project } from './api';

type ViewId = 'setup' | 'production' | 'delivery' | 'pro';
type NavItem = { id: string; text: string; icon: LucideIcon };
type ViewGroup = { view: ViewId; title: string; hint: string; core?: boolean; items: NavItem[] };

/** 专业模式：原四板块结构原样保留（普通用户默认不进入这一层）。 */
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

/** 导演模式：项目管理 + 七阶段制作页 + 不占流程序号的基础配置页。 */
type DirectorPage = 'projects' | 'creative' | 'assets' | 'produce' | 'materials' | 'package' | 'run' | 'deliver' | 'library' | 'setup';
const DIRECTOR_PAGE_LABELS: Record<DirectorPage, string> = {
  projects: '项目管理', creative: '创意与剧本', assets: '剧本统筹', produce: '镜头拆解', materials: '拍摄素材准备', package: '导演包',
  run: '全片生产', deliver: '成片中心', library: '成片库', setup: '基础配置',
};
/** 主线阶段。完成态一律来自服务端 /journey，不在前端另算一套。
 *  顺序遵守一条原则：素材是为剧本和分镜服务的，所以「拍摄素材准备」排在
 *  「镜头拆解」之后 —— 分镜先定稿，素材库再按分镜的要求准备。 */
const DIRECTOR_STAGES: Array<{ id: string; text: string; short?: string; icon: LucideIcon; page: DirectorPage; hint: string }> = [
  { id: 'script', text: '创意与剧本', icon: PenLine, page: 'creative', hint: '写想法或导入剧本，确认大纲' },
  { id: 'plan', text: '剧本统筹', icon: Film, page: 'assets', hint: '分集 · 冻结风格' },
  { id: 'storyboard', text: '镜头拆解', icon: ListChecks, page: 'produce', hint: '逐场分镜、对白与声音安排' },
  { id: 'assets', text: '拍摄素材准备', short: '素材准备', icon: Images, page: 'materials', hint: '按分镜定稿的清单准备素材并确认为主素材' },
  { id: 'package', text: '导演包', icon: LockKeyhole, page: 'package', hint: '冻结这次要拍的全部内容与执行政策' },
  { id: 'production', text: '视频生产', icon: Video, page: 'run', hint: '一次启动，后台跑完全部分集' },
  { id: 'post', text: '后期成片', icon: ArrowDownToLine, page: 'deliver', hint: '逐集剪辑、字幕、声音与交付' },
];

type ProduceStageFocus = 'storyboard' | 'assets' | 'produce' | 'monitor';
const PRODUCE_FOCUS_LABELS: Record<ProduceStageFocus, string> = {
  storyboard: '剧本分镜', assets: '素材准备', produce: '排产制作', monitor: '进度与结果',
};

function resolveHash(hash: string): {
  ui: 'director' | 'expert' | 'assets';
  view: ViewId;
  page: DirectorPage;
  anchor?: string;
  assetPage?: AssetCenterPage;
  packageId?: string;
  produceFocus?: ProduceStageFocus;
} {
  const raw = hash.replace(/^#/, '');
  if (raw.startsWith('a/')) {
    const page = raw.slice(2) as AssetCenterPage;
    const pages: AssetCenterPage[] = ['home', 'images', 'voices', 'library', 'assignments'];
    return { ui: 'assets', view: 'production', page: 'projects', assetPage: pages.includes(page) ? page : 'home' };
  }
  if (raw.startsWith('d/')) {
    const [path, query = ''] = raw.slice(2).split('?');
    const params = new URLSearchParams(query);
    const packageId = params.get('package') ?? undefined;
    const stageParam = params.get('stage') as ProduceStageFocus | null;
    const produceFocus = stageParam && Object.prototype.hasOwnProperty.call(PRODUCE_FOCUS_LABELS, stageParam)
      ? stageParam : undefined;
    let page = path as DirectorPage;
    // 旧四步深链（排产/进度）归并进七阶段的「视频生产」页，不再留在制作页。
    if (page === 'produce' && (produceFocus === 'produce' || produceFocus === 'monitor')) {
      page = 'run';
    }
    // 旧深链 #d/produce?stage=assets：素材准备已是独立一页，不再挂在镜头页里。
    if (page === 'produce' && produceFocus === 'assets') {
      page = 'materials';
    }
    if (Object.prototype.hasOwnProperty.call(DIRECTOR_PAGE_LABELS, page)) {
      return { ui: 'director', view: 'production', page, packageId, produceFocus: page === 'produce' ? produceFocus : undefined };
    }
    return { ui: 'director', view: 'production', page: 'projects' };
  }
  if (raw.startsWith('v/')) {
    const [, viewPart, anchorPart] = raw.split('/');
    const view = (VIEW_IDS as string[]).includes(viewPart) ? viewPart as ViewId : 'production';
    return { ui: 'expert', view, page: 'produce', anchor: anchorPart || undefined };
  }
  const anchorView = ANCHOR_VIEW[raw];
  if (anchorView) return { ui: 'expert', view: anchorView, page: 'produce', anchor: raw };
  return { ui: 'director', view: 'production', page: 'projects' };
}

function journeyCount(stage: JourneyStage | undefined, key: string): number | null {
  const value = stage?.facts[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 启动时恢复显示偏好（主题 / 字号缩放 / 界面层级）。 */
const savedTheme = window.localStorage.getItem('shine-theme');
if (savedTheme === 'dark' || savedTheme === 'light') document.documentElement.dataset.theme = savedTheme;
const savedZoom = window.localStorage.getItem('shine-font-zoom');
if (savedZoom) document.documentElement.style.zoom = savedZoom;

function App() {
  const studio = useStudio();
  const {
    projects, projectsLoaded, projectsLoadError, projectId, setProjectId, project, assets, styles, shots, jobs, style, setStyle,
    currentStyle, approvedAssets, projectTitle, setProjectTitle, mockScenario, setMockScenario,
    system, systemChecked, apiOnline, checkConnection, refreshWorkspace, production, profiles, routes, workflowProfiles, audit, preview, setPreview,
    notices, report, dismissNotice, busy, dataEpoch, queueProvider, queueProfile, setQueueProfile, reviewScope,
    setReviewScope, reviewWidth, setReviewWidth, reviewHeight, setReviewHeight,
    realSubmissionAllowed, demoOverride, setDemoOverride, loadAudit, refreshAll,
    createProject, saveStyle, freezeStyle, uploadAsset, confirmAssets, patchAssetMetadata,
    createShot, saveBindings, adoptShotStyle, applyStyleToAllShots, refreshStyleFreeze, unlockAsset,
    setShotContinuity, previewShot, queueShot, togglePause, loadJobQC, loadJobGates,
    suggestRepair, applyRepair, recheckReview, updateProjectSettings, createProfile, testProfile,
    setProfileKey, deleteProfile, routeProfile, testComfy, registerWorkflow, validateWorkflow, deleteWorkflow,
    bindStandardParameters, abandonJob,
  } = studio;

  // 旧深链 #d/produce?stage=assets 指向的素材页已经独立：地址与页面保持一致。
  useEffect(() => {
    const raw = window.location.hash;
    if (raw.startsWith('#d/produce?stage=assets')) {
      window.history.replaceState(null, '', '#d/materials');
    }
  }, []);

  const [ui, setUi] = useState<'director' | 'expert' | 'assets'>(() => {
    const hash = window.location.hash;
    const route = resolveHash(hash);
    if (route.ui === 'assets' || hash.startsWith('#d/') || hash.startsWith('#v/') || ANCHOR_VIEW[hash.slice(1)]) return route.ui;
    const saved = window.localStorage.getItem('shine-interface');
    return saved === 'expert' ? 'expert' : 'director';
  });
  const [assetPage, setAssetPage] = useState<AssetCenterPage>(() => resolveHash(window.location.hash).assetPage ?? 'home');
  const [assetReturnHash, setAssetReturnHash] = useState(() => window.location.hash.startsWith('#a/') ? '#d/projects' : (window.location.hash || '#d/projects'));
  const [directorPage, setDirectorPage] = useState<DirectorPage>(() => resolveHash(window.location.hash).page);
  /** The exact package a deep link named, so the workbench opens that one. */
  const [directorPackageId, setDirectorPackageId] = useState<string | undefined>(
    () => resolveHash(window.location.hash).packageId);
  const [setupFocusTab, setSetupFocusTab] = useState<'env' | 'models' | 'extra'>('env');
  /** 建好作品后直接进入第一步：项目管理不占阶段序号，但新作品要立刻能开工。 */
  const createProjectAndEnter = async () => {
    const created = await createProject();
    if (created) goToDirectorPage('creative');
  };
  const [wizard, setWizard] = useState<null | 'enable' | 'diagnose'>(null);
  const [activeSection, setActiveSection] = useState('overview');
  const [helpOpen, setHelpOpen] = useState(false);
  const [reconnectPhase, setReconnectPhase] = useState<'checking' | 'loading' | null>(null);
  const [reconnectError, setReconnectError] = useState('');
  const reconnecting = reconnectPhase !== null;
  const reconnectLabel = reconnectPhase === 'checking' ? '正在检查后端…' : '后端已连接，正在读取作品…';

  async function reconnectWorkbench() {
    if (reconnecting) return;
    setReconnectError('');
    setReconnectPhase('checking');
    try {
      if (!await checkConnection()) {
        setReconnectError('后端未响应。请双击项目根目录的「一键启动 Shine 视频工作台」，启动后再点重新连接。');
        return;
      }
      setReconnectPhase('loading');
      await refreshWorkspace(1);
    } catch {
      setReconnectError('后端连接检查已通过，但作品数据读取失败。请重新连接。');
    } finally {
      setReconnectPhase(null);
    }
  }
  /** Nav density contract: the current view's group is expanded by default; other groups expand only via their explicit toggle button. */
  const [manualExpanded, setManualExpanded] = useState<Set<ViewId>>(() => new Set());
  const [initialRoute] = useState(() => resolveHash(window.location.hash));
  const [view, setView] = useState<ViewId>(initialRoute.view);
  const viewScrollRef = useRef<Partial<Record<ViewId, number>>>({});
  const viewMountedRef = useRef(false);

  useEffect(() => { if (ui !== 'assets') window.localStorage.setItem('shine-interface', ui); }, [ui]);

  /**
   * 生成方案没手动选过时自动挑一个：单镜头开拍与一键开拍用同一套默认，
   * 用户不必先去「请选择…」里做一次技术选择。优先带画面宽高的 Ref2VA 方案。
   */
  useEffect(() => {
    if (queueProfile && workflowProfiles.some((profile) => profile.id === queueProfile)) return;
    const ref2va = workflowProfiles.filter((profile) => profile.mode.toLowerCase() === 'ref2va');
    if (ref2va.length === 0) return;
    const sized = ref2va.filter((profile) => 'width' in (profile.parameter_map ?? {})
      && 'height' in (profile.parameter_map ?? {}));
    const chosen = (sized.length ? sized : ref2va)[0];
    if (chosen) setQueueProfile(chosen.id);
  }, [queueProfile, workflowProfiles, setQueueProfile]);

  /** 七阶段完成态：全部来自服务端 /journey，包含本次范围、版本和真实服务端状态。 */
  const [journey, setJourney] = useState<Journey | null>(null);
  /** 读取状态与结果分开：读不到进度 ≠ 全部完成，失败必须如实显示并可重试。 */
  const [journeyState, setJourneyState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [journeyError, setJourneyError] = useState('');
  const [journeyRetry, setJourneyRetry] = useState(0);
  const [documentsVersion, setDocumentsVersion] = useState(0);
  useEffect(() => {
    if (!projectId) { setJourney(null); setJourneyState('idle'); return; }
    let alive = true;
    // Switching works never shows the previous work's stages while loading.
    setJourney((current) => (current && current.project_id === projectId ? current : null));
    setJourneyState('loading');
    getJourney(projectId)
      .then((row) => { if (alive) { setJourney(row); setJourneyState('ready'); setJourneyError(''); } })
      .catch((error) => {
        if (!alive) return;
        setJourney(null);
        setJourneyState('error');
        setJourneyError(error instanceof Error ? error.message : String(error));
      });
    return () => { alive = false; };
  }, [projectId, dataEpoch, journeyRetry]);
  /** V2 统一只读投影：项目与按集状态都来自这一份聚合，前端不自行猜完成度。 */
  const [overview, setOverview] = useState<DirectorOverview | null>(null);
  useEffect(() => {
    if (!projectId) { setOverview(null); return; }
    let alive = true;
    setOverview((current) => (current && current.project_id === projectId ? current : null));
    getDirectorOverview(projectId, { packageId: directorPackageId })
      .then((row) => { if (alive) setOverview(row); })
      .catch(() => { if (alive) setOverview(null); });
    return () => { alive = false; };
  }, [projectId, dataEpoch, directorPackageId]);
  const stageDone: Record<string, boolean> = useMemo(() => Object.fromEntries(
    (journey?.stages ?? []).map((stage) => [stage.id, stage.done]),
  ), [journey]);
  const stageBlockers: Record<string, number> = useMemo(() => Object.fromEntries(
    (journey?.stages ?? []).map((stage) => [stage.id, stage.blockers.length]),
  ), [journey]);
  /** 操作栏待办来自服务端 /journey 的 blocker，前端不另造完成态或阻塞。
   *  错误码经 errorActions 映射为「一句原因 + 去处理」，未知码保留原文。 */
  const railBlockers = (stageId: string): RailBlocker[] =>
    (journey?.stages.find((stage) => stage.id === stageId)?.blockers ?? [])
      .map((blocker) => {
        const described = describeBlocker(blocker.code, blocker.message);
        return {
          code: blocker.code,
          reason: described.reason,
          action: described.action ? { label: described.action.label, go: resolveBlockerTarget(described.action.target) } : undefined,
        };
      });
  function resolveBlockerTarget(target: ErrorActionTarget): () => void {
    switch (target) {
      case 'setup-models': return () => goToDirectorSetup('models');
      case 'setup-env': return () => goToDirectorSetup('env');
      case 'assets-stage': return () => goToDirectorPage('materials');
      case 'package-stage': return () => goToDirectorPage('package');
      case 'storyboard-stage': return () => goToDirectorPage('creative');
      case 'run-stage': return () => goToDirectorPage('run');
      case 'library': return () => goToDirectorPage('library');
      case 'consent': return () => goToDirectorPage('creative');
      case 'retry-scene': return () => goToDirectorPage('materials');
      case 'split-plan': return () => goToDirectorPage('materials');
    }
  }
  /** 生产阶段完成 = 本次范围内每个镜头都有当前有效成片，不是“有一个视频”。 */
  const deliverableReady = stageDone.production ?? false;
  /** 素材阶段的“还缺几项”：来自服务端 facts，缺图不堵流程但要让用户看见。 */
  const materialStage = journey?.stages.find((stage) => stage.id === 'assets');
  const materialMissing = journeyCount(materialStage, 'material_missing_count') ?? 0;
  const storyboardStage = journey?.stages.find((stage) => stage.id === 'storyboard');
  const productionStage = journey?.stages.find((stage) => stage.id === 'production');
  const projectShotCount = journeyCount(storyboardStage, 'shot_count') ?? shots.length;
  const plannedShotCount = journeyCount(storyboardStage, 'planned_shot_count') ?? projectShotCount;
  const scopeShotCount = journeyCount(productionStage, 'scope_shot_count');
  const passedScopeCount = journeyCount(productionStage, 'passed_shot_count');
  const hasProductionScope = scopeShotCount !== null && scopeShotCount > 0;
  const productionScopeLabel = hasProductionScope && passedScopeCount !== null
    ? `${passedScopeCount}/${scopeShotCount} 个计划镜头已通过`
    : null;
  /** 第一个未完成的阶段；null 表示七阶段全部完成。 */
  const nextStage = journey?.next_stage
    ? DIRECTOR_STAGES.find((stage) => stage.id === journey.next_stage) ?? null
    : null;
  /** 当前页面对应、最该被高亮的阶段：下一步在本页时高亮它，否则高亮本页第一个未完成项。 */
  const activeDirectorStage = (() => {
    const pageStages = DIRECTOR_STAGES.filter((stage) => stage.page === directorPage);
    if (pageStages.length === 0) return null;
    if (nextStage?.page === directorPage && pageStages.some((stage) => stage.id === nextStage.id)) return nextStage;
    return pageStages.find((stage) => !stageDone[stage.id]) ?? pageStages[0];
  })();
  const heroGuidance = journeyState !== 'ready' || !journey
    ? (journeyState === 'error'
      ? { page: 'projects' as DirectorPage, cta: '重新读取进度', hint: `暂时读不到这部作品的进度（${journeyError || '服务未响应'}）。这不代表已经完成，请重试。` }
      : { page: 'projects' as DirectorPage, cta: '正在读取进度…', hint: '正在从本机服务读取这部作品七个阶段的真实状态。' })
    : nextStage
      ? { page: nextStage.page, cta: `继续：${nextStage.text}`, hint: nextStage.hint }
      : { page: 'deliver' as DirectorPage, cta: '查看成片', hint: '全部选定范围已交付，去成片中心播放、核对并导出；艺术验收仍需你确认。' };
  function handleModeAction(action: 'enable_real' | 'start_making' | 'view_progress' | 'check_fix') {
    if (action === 'enable_real') setWizard('enable');
    else if (action === 'check_fix') setWizard('diagnose');
    else if (ui !== 'director') { setUi('director'); setDirectorPage('produce'); window.history.pushState(null, '', '#d/produce'); }
    else { setDirectorPage('produce'); window.history.pushState(null, '', '#d/produce'); }
  }

  function goToExpert(viewId: ViewId, anchor?: string) {
    setUi('expert');
    setView(viewId);
    window.history.pushState(null, '', anchor ? `#v/${viewId}/${anchor}` : `#v/${viewId}`);
    if (anchor) {
      setActiveSection(anchor);
      window.setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: 'auto', block: 'start' }), 60);
    }
  }

  function goToDirectorPage(page: DirectorPage, stageId?: string) {
    setUi('director');
    setDirectorPage(page);
    setDirectorPackageId(undefined);
    if (page === 'produce' && stageId) {
      // 镜头页只有一个工作区；旧锚点保留为滚动定位，不再切换分区。
      const dockId = stageId === 'storyboard' ? 'episode-storyboard' : null;
      if (dockId) window.setTimeout(() => document.getElementById(dockId)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 90);
    }
    window.history.pushState(null, '', `#d/${page}`);
    window.scrollTo({ top: 0 });
  }

  function goToAssets(page: AssetCenterPage = 'home') {
    if (ui !== 'assets') setAssetReturnHash(window.location.hash || (ui === 'expert' ? `#v/${view}` : `#d/${directorPage}`));
    setAssetPage(page);
    setUi('assets');
    window.history.pushState(null, '', `#a/${page}`);
    window.scrollTo({ top: 0 });
  }

  function leaveAssets(workspace: 'director' | 'expert') {
    const route = resolveHash(assetReturnHash);
    if (workspace === 'director') {
      if (route.ui === 'director') {
        setUi('director');
        setDirectorPage(route.page);
        setDirectorPackageId(route.packageId);
        window.history.pushState(null, '', assetReturnHash);
        window.scrollTo({ top: 0 });
      } else {
        goToDirectorPage('projects');
      }
      return;
    }
    if (route.ui === 'expert') {
      setUi('expert');
      setView(route.view);
      if (route.anchor) setActiveSection(route.anchor);
      window.history.pushState(null, '', assetReturnHash);
      window.setTimeout(() => {
        if (route.anchor) document.getElementById(route.anchor)?.scrollIntoView({ behavior: 'auto', block: 'start' });
        else window.scrollTo({ top: viewScrollRef.current[route.view] ?? 0 });
      }, 30);
    } else {
      goToExpert('production');
    }
  }

  /** 进入导演层基础配置，可直达指定分区；普通入口回到①安装与下载。
   *  导演模式内所有“去配置模型/生成方案”的引导都走这里，不再甩去专业模式。 */
  function goToDirectorSetup(tab: 'env' | 'models' | 'extra' = 'env') {
    setSetupFocusTab(tab);
    setUi('director');
    setDirectorPage('setup');
    window.history.pushState(null, '', '#d/setup');
    window.scrollTo({ top: 0 });
  }

  /** 滚动侦测：板块进入视口上部条带时菜单联动高亮（仅当前视图内的锚点会交叉）。
   *  点击菜单后按滚动距离锁定目标，平滑滚动途中不被旧板块抢回高亮；
   *  多板块同在观察带时取"最深入条带"（rect.top 最大）者。 */
  const spiedRef = useRef(false);
  const navLockRef = useRef<{ id: string; until: number } | null>(null);
  useEffect(() => {
    if (ui !== 'expert' || !project) return;
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
  }, [ui, project, shots, jobs]);

  /** 滚动侦测触发后，地址栏锚点与当前视图/板块保持一致。 */
  useEffect(() => {
    if (ui !== 'expert' || !spiedRef.current) return;
    const next = `#v/${view}/${activeSection}`;
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  }, [activeSection, view, ui]);

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
      if (target.ui === 'assets') {
        setUi('assets');
        setAssetPage(target.assetPage ?? 'home');
        return;
      }
      if (target.ui === 'director') {
        setUi('director');
        setDirectorPage(target.page);
        setDirectorPackageId(target.packageId);
        return;
      }
      setUi('expert');
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
    window.addEventListener('hashchange', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('hashchange', onPop);
    };
  });

  /** 启动深链（如安装器落地 #setup 或 #v/production/assets）。 */
  useEffect(() => {
    if (viewMountedRef.current || !initialRoute.anchor || initialRoute.ui !== 'expert') return;
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

  const viewHero = (title: string, desc: string, extra?: string) => (
    <section className="view-hero">
      <div>
        <div className="eyebrow"><span className="eyebrow-line" /> {title} <span className="eyebrow-en">{extra}</span></div>
        <h1>{desc}</h1>
      </div>
    </section>
  );

  /** 导演模式每一页统一的页头：我在哪一步、这一页要做什么、当前状态一眼可见。 */
  const directorPageIntro = (kicker: string, title: ReactNode, desc: string, aside?: ReactNode) => (
    <header className="director-page-head">
      <div className="director-page-head-copy">
        <div className="eyebrow"><span className="eyebrow-line" /> {kicker}</div>
        <h1>{title}</h1>
        <p>{desc}</p>
      </div>
      {aside && <div className="director-page-head-aside">{aside}</div>}
    </header>
  );

  const modeLabel = production?.label ?? '状态未知';

  if (ui === 'assets') {
    return <AssetsWorkspace
      page={assetPage}
      onNavigate={(page) => { setAssetPage(page); window.scrollTo({ top: 0 }); }}
      onWorkspace={leaveAssets}
      onOpenImageSetup={() => {
        goToDirectorSetup();
        window.setTimeout(() => document.getElementById('image-capability')?.scrollIntoView({ block: 'start' }), 60);
      }}
      projects={projects as Project[]}
      projectId={projectId}
      projectAssets={assets as Asset[]}
    />;
  }

  const workspaceReconnect = <div className="workspace-reconnect" aria-busy={reconnecting}>
    <button type="button" className="btn-secondary" disabled={reconnecting} onClick={reconnectWorkbench}>
      {reconnecting ? reconnectLabel : '重新连接'}
    </button>
    {reconnecting && <div role="status"><Meter label={reconnectLabel} value={0} tone="busy" /></div>}
    {reconnectError && <p role="alert">{reconnectError}</p>}
  </div>;

  return (
    <div className={`app-shell ${ui === 'director' ? 'ui-director' : 'ui-expert'}`}>
      {ui === 'director' && <aside className="director-sidebar">
        <div className="director-sidebar-brand">
          <div className="brand-mark"><Clapperboard size={18} /></div>
          <div><b>SHINE</b><span>导演工作台</span></div>
        </div>
        <nav className="director-sidebar-nav" aria-label="导演工作台导航">
          <button type="button" className={directorPage === 'projects' ? 'active' : ''}
            onClick={() => goToDirectorPage('projects')}>
            <FolderKanban size={15} /><span>项目管理</span>
          </button>
          <button type="button" onClick={() => goToAssets('home')}>
            <Images size={15} /><span>素材中心</span>
          </button>
          <button type="button" className={directorPage === 'library' ? 'active' : ''}
            onClick={() => goToDirectorPage('library')}>
            <Film size={15} /><span>成片库</span>
          </button>
          <button type="button"
            className={`${directorPage === 'setup' ? 'active' : ''} ${production && !['READY', 'RUNNING'].includes(production.mode) ? 'needs-setup' : ''}`}
            onClick={() => goToDirectorSetup('env')}>
            <Settings2 size={15} /><span>基础配置</span>
            {production && !['READY', 'RUNNING'].includes(production.mode) && <i className="setup-entry-dot" aria-hidden="true" />}
            {production && !['READY', 'RUNNING'].includes(production.mode)
              && <em className="sidebar-tip">先配这里</em>}
          </button>
        </nav>
        <WorkStackMonitor />
        <div className="director-sidebar-foot">
          <i aria-hidden="true" className={`status-led ${apiOnline ? '' : systemChecked ? 'offline' : 'checking'}`} />
          <span>{apiOnline ? '本机服务在线' : systemChecked ? '本机服务失联' : '正在检查服务'}</span>
        </div>
      </aside>}

      {ui === 'expert' && <aside className="sidebar">
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
        <WorkStackMonitor />
        <div className="privacy-card"><ShieldCheck size={17} /><div><b>本地优先</b><span>素材与密钥不离开本机</span></div><Check size={15} /></div>
        <div className="profile"><div className="avatar">S</div><div><b>本地工作区</b><span>{studio.apiOnline ? '已连接本机服务' : '本机服务未连接'}</span></div><ChevronDown size={16} /></div>
      </aside>}

      <main className="main-content">
        <header className={`topbar ${ui === 'director' ? 'director-topbar' : ''}`}>
          <div className="topbar-context">
            <nav className="breadcrumb" aria-label="当前位置">
              <span>工作区</span><span className="crumb-slash">/</span>
              {ui === 'director'
                ? <b>{DIRECTOR_PAGE_LABELS[directorPage]}</b>
                : <><b>{VIEW_TITLES[view]}</b>{project && <><span className="crumb-slash">/</span><span>{project.title}</span></>}</>}
            </nav>
            {ui === 'director' && project && (projects.length > 1
              ? <label className="project-switch">
                <span className="visually-hidden">快速切换作品</span>
                <Clapperboard size={12} aria-hidden="true" />
                <select aria-label="快速切换作品" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                  {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
                </select>
                <ChevronDown size={12} aria-hidden="true" />
              </label>
              : <span className="project-chip"><Clapperboard size={12} aria-hidden="true" />{project.title}</span>)}
          </div>
          <div className="top-actions">
            <div className="topbar-status">
              <span className="service-status" aria-live="polite" title={apiOnline ? '工作台后端在线' : systemChecked ? '工作台后端失联' : '正在检查工作台后端'}>
                <i aria-hidden="true" className={`status-led ${apiOnline ? '' : systemChecked ? 'offline' : 'checking'}`} />
                <span className="service-status-text">{apiOnline ? '工作台后端在线' : systemChecked ? '工作台后端失联' : '正在检查工作台后端'}</span>
              </span>
              {systemChecked && (!apiOnline || reconnecting || !!projectsLoadError) && <button type="button" className="btn-secondary"
                disabled={reconnecting}
                title="后端未启动时，请先启动本机工作台"
                onClick={reconnectWorkbench}>{reconnecting ? reconnectLabel : '重连工作台'}</button>}
              <ModeStatusEntry system={system} onAction={handleModeAction} />
              <EngineStatusEntry apiOnline={apiOnline} />
            </div>
            <div className="topbar-controls">
              <button
                type="button"
                className={`ui-toggle ${ui === 'director' ? 'on-director' : 'on-expert'}`}
                title={ui === 'director' ? '切换到专业模式：模型、工作流、调度诊断等全部高级能力' : '回到导演模式：普通用户的简洁制作流程'}
                onClick={() => { if (ui === 'director') goToExpert(view, activeSection); else goToDirectorPage(directorPage); window.scrollTo({ top: 0 }); }}
              >
                <Clapperboard size={13} /><span>导演模式</span>
                <i className="ui-toggle-sep" />
                <SlidersHorizontal size={13} /><span>专业模式</span>
              </button>
              <button className="icon-button" title="刷新全部数据" aria-label="刷新全部数据" onClick={() => refreshAll().catch((error: Error) => report(error.message, 'error'))}><RefreshCw size={15} /></button>
              <button className="help-button" onClick={() => setHelpOpen(true)} title="帮助"><CircleHelp size={15} /> <span>帮助</span></button>
            </div>
            {reconnectError && projectsLoaded && <span role="alert" className="topbar-alert">{reconnectError}</span>}
          </div>
        </header>

        <div className="page-wrap">
          {/* ============ 导演模式：普通用户的默认层 ============ */}
          {ui === 'director' && <>
            {directorPage === 'setup' && <DirectorSetupPanel
              focusTab={setupFocusTab}
              project={project}
              projectId={projectId}
              projectTitle={projectTitle}
              setProjectTitle={setProjectTitle}
              createProject={createProject}
              system={system}
              profiles={profiles}
              routes={routes}
              workflowProfiles={workflowProfiles}
              busy={busy}
              refreshEpoch={dataEpoch}
              onRefresh={refreshAll}
              onProjectSettings={updateProjectSettings}
              onCreateProfile={createProfile}
              onTestProfile={testProfile}
              onSetKey={setProfileKey}
              onDeleteProfile={deleteProfile}
              onRoute={routeProfile}
              onTestComfy={testComfy}
              onRegisterWorkflow={registerWorkflow}
              onValidateWorkflow={validateWorkflow}
              onDeleteWorkflow={deleteWorkflow}
              onBindStandardParameters={bindStandardParameters}
              onOpenExpert={() => { setUi('expert'); setView('production'); window.history.pushState(null, '', '#v/production/preproduction'); }}
            />}
            <SchemaNotice />
            {directorPage !== 'setup' && !project && <section className="first-run card">
              <div className="first-run-art"><Clapperboard size={30} /></div>
              {projectsLoaded ? <>
                <h2>创建我的第一部AI短剧</h2>
                <p>建好作品后按七个阶段推进，所有内容都保存在你自己的电脑上。现在只需要先起个名字：</p>
                <ol className="first-run-journey" aria-label="制作全旅程">
                  <li className="now"><b>① 创意与剧本</b><span>输入想法，或导入现成剧本</span></li>
                  <li><b>② 剧本统筹</b><span>分集，并冻结风格</span></li>
                  <li><b>③ 镜头拆解</b><span>逐场拆成可拍的镜头</span></li>
                  <li><b>④ 拍摄素材准备</b><span>按分镜清单备料并确认</span></li>
                  <li><b>⑤ 导演包</b><span>确认范围与执行政策</span></li>
                  <li><b>⑥ 视频生产</b><span>一次启动，持续生产</span></li>
                  <li><b>⑦ 后期成片</b><span>逐集剪辑与交付</span></li>
                </ol>
                <div className="create-project">
                  <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="给作品起个名字（如：巷口的灯）" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
                  <button className="btn-secondary" title="创建后直接进入第 1 阶段" disabled={busy || !projectTitle.trim()} onClick={() => void createProjectAndEnter()}>创建作品</button>
                  <GuideTip>起个名字，点这里开始第 1 步</GuideTip>
                </div>
                <div className="first-run-setup">
                  <b>第一次使用？先花两分钟把机器调好。</b>
                  <span>配置文本模型、检测/安装本机 ComfyUI 与 H3、登记生成方案——都在导演模式内完成。</span>
                  <button className="btn-secondary" onClick={() => goToDirectorSetup('env')}>
                    <Settings2 size={13} /> 打开基础配置
                  </button>
                </div>
                <p className="empty-setup-hint">
                  制作环境不足也不影响创建作品、导入剧本和整理素材；真正开拍前补上即可。
                </p>
              </> : <>
                <h2>{projectsLoadError ? '本机工作区暂时不可用' : '正在读取本机作品'}</h2>
                <p>{projectsLoadError || '正在连接本机服务。稍等片刻，已有作品会自动显示。'}</p>
                {workspaceReconnect}
              </>}
            </section>}

            {project && <>
              {/* 唯一的一条旅程：步骤、完成态、下一步提示和阻塞数都在这里。 */}
              {directorPage !== 'setup' && directorPage !== 'projects' && <JourneyRail
                stages={DIRECTOR_STAGES}
                currentPage={directorPage}
                activeStageId={activeDirectorStage?.id ?? null}
                done={stageDone}
                blockers={stageBlockers}
                nextStage={nextStage}
                state={journeyState}
                error={journeyError}
                onNavigate={(stage) => goToDirectorPage(stage.page, stage.id)}
                onRetry={() => setJourneyRetry((n) => n + 1)}
              />}
              <div className="demo-ribbon-slot" hidden={directorPage === 'setup'}><DemoRibbon production={production ?? {
                mode: 'BLOCKED', label: '状态未知', detail: '暂无法确认制作环境。', action: null, code: null,
                render_provider: 'mock', runtime_status: 'NOT_ACTIVATED', renderer_online: false,
                worker_online: false, running_jobs: 0, series_active: 0,
              }} onEnable={() => setWizard('enable')} /></div>

              {directorPage === 'projects' && <div className="director-page">
                <section className="welcome-row">
                  <div>
                    <div className="eyebrow"><span className="eyebrow-line" /> 项目管理</div>
                    <h1>每一部作品，<em>都清清楚楚。</em></h1>
                    <p>{journeyState !== 'ready' || !journey
                      ? heroGuidance.hint
                      : '所有作品的进度和产出物都在这里；选中哪部就继续。'}</p>
                  </div>
                  <button className="btn-primary" disabled={journeyState === 'loading' && !journey}
                    onClick={() => (journeyState === 'error' ? setJourneyRetry((n) => n + 1) : goToDirectorPage(heroGuidance.page))}>
                    {journeyState === 'error' ? '重新读取进度' : '打开当前作品'}
                  </button>
                  <GuideTip>接着上次停下的那一步继续</GuideTip>
                </section>
                <ProjectPortfolio
                  projects={projects as Project[]}
                  currentProjectId={projectId}
                  productionLabel={modeLabel}
                  productionDetail={production?.detail}
                  projectTitle={projectTitle}
                  setProjectTitle={setProjectTitle}
                  busy={busy}
                  refreshEpoch={dataEpoch}
                  onOpen={(id) => {
                    if (id !== projectId) setProjectId(id);
                    goToDirectorPage(nextStage?.page ?? 'creative');
                  }}
                  onCreate={() => void createProjectAndEnter()}
                />
              </div>}

              <div className="director-page" id="creative" hidden={directorPage !== 'creative'}>
                <StageRailProvider stageId="script" stageLabel="第①步 · 创意与剧本" blockers={railBlockers('script')}>
                {directorPageIntro(
                  '第 1 阶段',
                  <>先有一个<em>能开拍的故事。</em></>,
                  '写想法或导入剧本；确认大纲后进入统筹。',
                  <>
                    <span className="head-chip">{project.title}</span>
                    {stageDone.script && <span className="portfolio-badge ok"><Check size={10} />已完成</span>}
                  </>,
                )}
                <ScriptEntryPanel
                  key={`script-entry-${projectId}`}
                  projectId={projectId}
                  onMessage={report}
                  onDocumentReady={() => { setDocumentsVersion((n) => n + 1); }}
                />
                <p className="empty-inline">
                  文学四阶段（分卷、蓝图、分场剧本、五维故事记忆）在后台按你确认的大纲工作，不需要在这里再点一遍。
                  需要逐卷控制或查看中间产物时，到<b>专业模式 · 生产板块</b>的「四阶段自动编导」。
                </p>
                </StageRailProvider>
              </div>

              <div className="director-page director-plan" id="plan-stage" hidden={directorPage !== 'assets'}>
                <StageRailProvider stageId="plan" stageLabel="第②步 · 剧本统筹" blockers={railBlockers('plan')}>
                {directorPageIntro(
                  '第 2 阶段',
                  <>把剧本切成<em>能拍的集。</em></>,
                  '四个决定（风格、类型、集数、每集秒数）生成分镜书；一次确认同时定稿分集与风格。',
                  <>
                    <span className="head-chip">{journey?.episode_plan ? `${journey.episode_plan.episodes} 集` : '分集待确认'}</span>
                    <span className={`head-chip ${currentStyle ? 'ok' : ''}`}>{currentStyle ? `风格第 ${currentStyle.version} 版` : '风格待冻结'}</span>
                  </>,
                )}
                {directorPage === 'assets' && <StoryboardBookPanel
                  key={`book-${projectId}`}
                  projectId={projectId}
                  currentStyle={currentStyle}
                  onMessage={report}
                  onConfirmed={() => refreshAll().catch(() => undefined)}
                />}
                <p className="empty-inline">
                  需要逐项微调七个风格维度或按镜头兼容分集？这些专业入口保留在专业模式的「素材与风格 / 项目概览」里。
                </p>
                </StageRailProvider>
              </div>

              <div className="director-page" id="produce" hidden={directorPage !== 'produce'}>
                {directorPageIntro(
                  '第 3 阶段',
                  <>把故事拆成<em>能拍的镜头。</em></>,
                  '后台逐场调用五个分镜工种并自动落镜；每一集独立推进，一集没拆完不影响其他集。',
                  <>
                    <span className={`head-chip ${shots.length ? 'ok' : ''}`}>{shots.length ? `${plannedShotCount} 镜在册` : '还没有分镜'}</span>
                    <span className={`head-chip ${currentStyle ? 'ok' : ''}`}>{currentStyle ? `风格第 ${currentStyle.version} 版` : '风格待冻结'}</span>
                  </>,
                )}
                <StageRailProvider stageId="storyboard" stageLabel="第③步 · 镜头拆解" blockers={railBlockers('storyboard')}>
                  <EpisodeStoryboardPanel key={`ep-storyboard-${projectId}`} projectId={projectId}
                    onMessage={report} onJourneyRefresh={() => refreshAll().catch(() => undefined)} />
                  {routes.length === 0 && <div className="blocked-callout">
                    <Sparkles size={15} />
                    <span>AI 分镜能力未就绪：还没有配置模型服务。你仍然可以在专业模式手工新建分镜，或</span>
                    <button type="button" className="btn-text" onClick={() => goToDirectorSetup('models')}>去配置模型服务</button>
                  </div>}
                </StageRailProvider>
              </div>

              <div className="director-page" id="materials" hidden={directorPage !== 'materials'}>
                {directorPageIntro(
                  '第 4 阶段',
                  <>镜头要什么，<em>就准备什么。</em></>,
                  '清单只从已确认镜头派生；已有主素材直接复用，缺的必需项只暂停用到它的镜头。',
                  <>
                    <span className="head-chip">{assets.length} 项素材</span>
                    <span className={`head-chip ${approvedAssets.length ? 'ok' : ''}`}>{approvedAssets.length} 项已批准锁定</span>
                  </>,
                )}
                <StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备" blockers={railBlockers('assets')}>
                  {materialMissing > 0 && <div className="blocked-callout">
                    <AlertTriangle size={15} />
                    <span>分镜要求的必需素材还没有全部确认为主素材；缺失只影响用到它的镜头，其他镜头照常开拍。可选项可以直接跳过。</span>
                  </div>}
                  <ShotMaterialsPanel key={`shot-materials-${projectId}`} projectId={projectId}
                    onMessage={report} onOpenLibrary={() => goToAssets('images')} />

                  <section className="card section-card" id="project-assets">
                    <div className="section-heading">
                      <div>
                        <div className="section-kicker">项目素材</div>
                        <h2>项目素材</h2>
                        <p>已批准并锁定的主素材才能被镜头引用。上传后到素材中心确认候选、锁定版本；
                          这里只显示本项目的素材规模。</p>
                      </div>
                      <button type="button" className="btn-secondary" onClick={() => goToAssets('home')}>
                        <Images size={13} /> 打开素材中心
                      </button>
                    </div>
                    <p className="run-hint">
                      {assets.length} 项在库 · {approvedAssets.length} 项已批准并锁定
                      {assets.length > approvedAssets.length
                        ? ` · ${assets.length - approvedAssets.length} 项待确认（到素材中心处理）` : ''}
                    </p>
                  </section>

                  <p className="empty-inline">
                    需要按场次重新分析剧本、查看候选批次或做批量回绑时，到<b>专业模式 · 生产板块</b>的「素材准备」；
                    这里只保留从已确认镜头推导出的需求与决策。
                  </p>
                </StageRailProvider>
              </div>

              <div className="director-page" id="package" hidden={directorPage !== 'package'}>
                <StageRailProvider stageId="package" stageLabel="第⑤步 · 导演包" blockers={railBlockers('package')}>
                {directorPageIntro(
                  '第 5 阶段',
                  <>把这次要拍的<em>固定下来。</em></>,
                  '选择一个能真实开拍的方案，确认后冻结本次范围；没准备好的集不会被拖进来。',
                  <span className={`head-chip ${stageDone.package ? 'ok' : ''}`}>{journey?.package ? `第 ${journey.package.revision} 版` : '尚未生成'}</span>,
                )}
                {directorPage === 'package' && <DirectorShootPanel
                  projectId={projectId}
                  mode="package"
                  overview={overview}
                  demo={production?.mode === 'DEMO' || production?.render_provider === 'mock'}
                  onMessage={report}
                  onRefresh={() => refreshAll().catch(() => undefined)}
                  onNavigate={(page) => goToDirectorPage(page)}
                />}
                </StageRailProvider>
              </div>

              <div className="director-page" id="run" hidden={directorPage !== 'run'}>
                <StageRailProvider stageId="production" stageLabel="第⑥步 · 视频生产" blockers={railBlockers('production')}>
                {directorPageIntro(
                  '第 6 阶段',
                  <>一次启动，<em>后台跑完全片。</em></>,
                  '关了浏览器也会继续；进度按集显示，任何一集出问题都不影响其他集已经通过的镜头。',
                  <span className={`head-chip ${deliverableReady ? 'ok' : ''}`}>{productionScopeLabel ?? '等待导演包'}</span>,
                )}
                {directorPage === 'run' && <DirectorShootPanel
                  projectId={projectId}
                  mode="run"
                  overview={overview}
                  demo={production?.mode === 'DEMO' || production?.render_provider === 'mock'}
                  onMessage={report}
                  onRefresh={() => refreshAll().catch(() => undefined)}
                  onNavigate={(page) => goToDirectorPage(page)}
                />}
                <details className="stage-dock" id="production-monitor" open={false}>
                  <summary className="produce-focus-summary">
                    <span className="focus-summary-label">
                    {productionScopeLabel
                      ? `⑥ 任务明细（诊断）· ${productionScopeLabel}`
                      : jobs.length === 0 ? '⑥ 任务明细（诊断）· 暂无任务'
                      : '⑥ 任务明细（诊断）· 点击查看'}
                    </span>
                  </summary>
                  {productionScopeLabel && <div className="empty-inline">
                    本次确认范围：{scopeShotCount} 镜，{productionScopeLabel}。以下服务统计与任务列表包含工作台/项目历史记录及本次范围外镜头；本次成片状态以按集进度和成片页为准。
                  </div>}
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
                    onAbandon={abandonJob}
                  />
                </details>
                </StageRailProvider>
              </div>

              <div className="director-page" id="deliver" hidden={directorPage !== 'deliver'}>
                <StageRailProvider stageId="post" stageLabel="第⑦步 · 后期成片" blockers={railBlockers('post')}>
                {directorPageIntro(
                  '第 7 阶段',
                  <>逐集播放，<em>确认后交付。</em></>,
                  '技术完成不等于认可；先播放核对，再导出最终文件。',
                  <span className={`head-chip ${deliverableReady ? 'ok' : ''}`}>{deliverableReady ? '全部选定镜头已出片' : '等待镜头完成'}</span>,
                )}
                {directorPage === 'deliver' && <DirectorProductionRunPanel
                  key={`post-${projectId}`}
                  projectId={projectId}
                  mode="post"
                  // 成片按集读取：拍第 2 集时第 1 集仍要留在这一页。
                  episodes={overview?.episodes ?? null}
                  demo={production?.mode === 'DEMO' || production?.render_provider === 'mock'}
                  workflowProfiles={workflowProfiles}
                  onMessage={report}
                  onRefresh={() => refreshAll().catch(() => undefined)}
                  onOpenLibrary={() => goToDirectorPage('library')}
                />}
                <DeliveryPanel key={`delivery-${projectId}`} projectId={projectId} jobs={jobs} active={directorPage === 'deliver'} refreshEpoch={dataEpoch} />
                </StageRailProvider>
              </div>

              <div className="director-page" id="library" hidden={directorPage !== 'library'}>
                {directorPageIntro(
                  '本机成片库',
                  <>每一段视频，<em>都在你自己的盘上。</em></>,
                  '按作品汇总实际落盘的单镜、分集与成片；预览播放的是本机原文件，历史版本可在这里清理。',
                  <span className="head-chip">{project.title}</span>,
                )}
                {directorPage === 'library' && <VideoLibraryPanel
                  key={`library-${projectId}`}
                  projectId={projectId}
                  projectTitle={project.title}
                  active={directorPage === 'library'}
                  refreshEpoch={dataEpoch}
                  onMessage={report}
                />}
              </div>

              {directorPage !== 'projects' && directorPage !== 'setup' && activeDirectorStage && (
                <JourneyBridge
                  stages={DIRECTOR_STAGES}
                  activeStageId={activeDirectorStage.id}
                  onNavigate={(stage) => goToDirectorPage(stage.page, stage.id)}
                  onHome={() => goToDirectorPage('projects')}
                />
              )}

              <footer className="page-footer" hidden={directorPage === 'setup'}><span>SHINE VIDEO STUDIO <i>·</i> DIRECTOR WORKBENCH</span><span><ShieldCheck size={12} /> 本地数据库 · 素材由你掌控</span></footer>
            </>}
          </>}

          {/* ============ 专业模式：完整四板块（原界面） ============ */}
          {ui === 'expert' && <>
            {!project && <>
              {/* 空库也能进入设置板块：环境检查、上游可达性、部署清单、安装目录和偏好都不依赖项目。 */}
              {view === 'setup' && <>
                {viewHero('设置板块', '先把工具调教顺手，再开工。', 'SYSTEM SETUP')}
                <SetupPanel key="setup-no-project" projectId="" onRefresh={refreshAll} refreshEpoch={dataEpoch} />
                <StorageSettings />
                <PreferencesPanel onNavigate={goTo} />
              </>}
              <section className="empty-project card">
                <div className="empty-art"><FolderKanban size={26} /></div>
                <h2>{!projectsLoaded ? (projectsLoadError ? '本机工作区暂时不可用' : '正在读取本机作品') : view === 'setup' ? '接下来，从第一个项目开始' : '从一个项目开始'}</h2>
                <p>{!projectsLoaded ? (projectsLoadError || '正在连接本机服务。稍等片刻，已有作品会自动显示。') : view === 'setup'
                  ? '上面的环境检查、安装目录和系统探测不需要项目。生产、交付等专业流程需要一个项目来承载：'
                  : '所有内容都保存在你自己电脑上：项目、素材、风格、任务和操作记录。'}</p>
                {projectsLoaded && <div className="create-project">
                  <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="给项目起个名字" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
                  <button className="btn-secondary" disabled={busy || !projectTitle.trim()} onClick={() => void createProjectAndEnter()}>新建项目</button>
                </div>}
                {!projectsLoaded && workspaceReconnect}
                {projectsLoaded && view !== 'setup' && <p className="empty-setup-hint">
                  还没装机？<button className="btn-text" onClick={() => { switchView('setup'); window.history.pushState(null, '', '#v/setup'); }}>先去「制作环境」检查本机</button>——不需要先建项目。
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
                  <button className="btn-primary" disabled={busy || !projectTitle.trim()} onClick={createProject}><Plus size={15} aria-hidden="true" />新建项目</button>
                </section>

                <section className="metrics-grid">
                  <div className="metric-card"><div className="metric-top"><span>项目素材</span><div className="metric-icon mint"><Film size={15} /></div></div><div className="metric-value">{assets.length}<small>项</small></div><div className="metric-foot">{approvedAssets.length} 项已批准并锁定</div></div>
                  <div className="metric-card"><div className="metric-top"><span>风格基准</span><div className="metric-icon sand"><LockKeyhole size={15} /></div></div><div className="metric-value">{currentStyle ? `第 ${currentStyle.version} 版` : '—'}</div><div className="metric-foot">{currentStyle ? '已冻结，全部镜头按此执行' : '填好七项风格约定后由你冻结'}</div></div>
                  <div className="metric-card"><div className="metric-top"><span>生成任务</span><div className="metric-icon blue"><Activity size={15} /></div></div><div className="metric-value">{jobs.length}<small>个</small></div><div className="metric-foot">{jobs.filter((job) => job.status === 'PASSED').length} 个完成 · {jobs.filter((job) => job.acceptance_status === 'accepted').length} 个验收通过 · {jobs.filter((job) => job.acceptance_status === 'pending_review').length} 个等你验收</div></div>
                  <div className="metric-card accent-metric"><div className="metric-top"><span>制作方式</span><div className="metric-icon cream"><Sparkles size={15} /></div></div><div className="metric-value small-value">{modeLabel}</div><div className="metric-foot">{production?.renderer_online ? '本机渲染器在线' : production?.detail ?? ''}</div></div>
                </section>

                <AssetPanel
                  assets={assets}
                  busy={busy}
                  onUpload={uploadAsset}
                  onConfirm={confirmAssets}
                  onUnlock={unlockAsset}
                  onPatchMetadata={patchAssetMetadata}
                  onOpenLibrary={() => goToAssets('library')}
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
                  onApplyStyleToAllShots={() => void applyStyleToAllShots()}
                  onRefreshFreeze={() => void refreshStyleFreeze()}
                  onDraftApplied={() => { refreshAll().catch(() => undefined); }}
                  onNavigate={goTo}
                  onMessage={report}
                />

                <PreproductionPanel key={`prep-${projectId}`} projectId={projectId} onRefresh={refreshAll} reviewScope={reviewScope} />

                <StoryboardPanel key={`storyboard-${projectId}`} projectId={projectId} onRefresh={refreshAll}
                  backendDisconnected={systemChecked && !apiOnline}
                  visible={view === 'production'}
                  onNavigate={(page) => goToDirectorPage(page as DirectorPage)} />

                <FourStagePanel
                  key={`four-stage-${projectId}`}
                  projectId={projectId}
                  documentsVersion={documentsVersion}
                  workflowProfiles={workflowProfiles}
                  onMessage={report}
                />

                <MaterialPreparationPanel
                  key={`material-expert-${projectId}`}
                  projectId={projectId}
                  documentsVersion={documentsVersion}
                  shots={shots}
                  onMessage={report}
                  frozenStyle={currentStyle}
                  storyboardReady={!!stageDone.storyboard}
                  onPromoted={() => { refreshAll().catch(() => undefined); }}
                />

                <DirectorStoryboardStage projectId={projectId} onMessage={report}
                  onJourneyRefresh={() => refreshAll().catch(() => undefined)} />

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
                    <p className="queue-provider-hint">
                      {realSubmissionAllowed
                        ? '默认真实生成（本机显卡 + H3），可临时切回演示。'
                        : '演示模式用于熟悉流程，不消耗显卡、不出真视频。真实生成先在顶部启用真实制作。'}
                    </p>
                    {realSubmissionAllowed
                      ? <label>生成方式<select value={demoOverride ? 'mock' : 'comfyui'} onChange={(event) => setDemoOverride(event.target.value === 'mock')}>
                          <option value="comfyui">真实生成（默认）</option>
                          <option value="mock">演示（不出真视频）</option>
                        </select></label>
                      : <label>生成方式<select value={queueProvider} disabled={queueProvider === 'mock'} onChange={(event) => setDemoOverride(event.target.value === 'mock')}>
                          <option value="comfyui" disabled>真实制作受阻，请检查环境</option>
                          <option value="mock">演示体验（真实制作未启用）</option>
                        </select></label>}
                    {queueProvider === 'comfyui' && <label>生成模板<select value={queueProfile} onChange={(event) => setQueueProfile(event.target.value)}>
                      <option value="">请选择…</option>{workflowProfiles.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.mode}</option>)}
                    </select></label>}
                    {queueProvider === 'comfyui' && <>
                      <label>审片建议（可选）<select value={reviewScope} onChange={(event) => setReviewScope(event.target.value as 'basic' | 'full')}>
                        <option value="full">完整建议：画面、对白与风格（只记录，不阻断）</option>
                        <option value="basic">基础建议：人物与对白（只记录，不阻断）</option>
                      </select></label>
                      {reviewScope === 'basic' && <>
                        <label>预期画面宽度<input type="number" min="2" max="8192" step="1" value={reviewWidth} onChange={(event) => setReviewWidth(event.target.value)} /></label>
                        <label>预期画面高度<input type="number" min="2" max="8192" step="1" value={reviewHeight} onChange={(event) => setReviewHeight(event.target.value)} /></label>
                        <p>宽高只做视频技术校验，不会改动生成设置；小模型人物和对白建议未覆盖时也能继续生产。</p>
                      </>}
                    </>}
                  </div>
                  {/* 单镜制作与任务明细：导演模式只保留一个开拍入口，
                      逐镜提交与诊断留在专业模式。 */}
                  <details className="advanced-fold" open>
                    <summary>单镜头制作与任务明细（高级）</summary>
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
                      onAdoptStyle={adoptShotStyle}
                      setPreview={setPreview}
                    />
                  </details>
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
                    onAbandon={abandonJob}
                  />
                </section>

                <ProductionSeriesPanel
                  key={`series-${projectId}`}
                  projectId={projectId}
                  shots={shots}
                  assets={assets}
                  jobs={jobs}
                  workflowProfiles={workflowProfiles}
                  system={system}
                  demoSelected={demoOverride || queueProvider === 'mock'}
                  onSelectReal={() => setDemoOverride(false)}
                  busy={busy}
                  reviewWidth={reviewWidth}
                  reviewHeight={reviewHeight}
                  onStarted={() => refreshAll().catch(() => undefined)}
                  onMessage={report}
                  onSaveBindings={saveBindings}
                  onSetContinuity={setShotContinuity}
                  onModeAction={handleModeAction}
                  onGoSetup={() => goToDirectorPage('setup')}
                />

                <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> V1.1 DEVELOPMENT</span><span><ShieldCheck size={12} /> 本地数据库 · 素材由你掌控</span></footer>
              </div>

              {/* 设置板块：装机、模型与系统、个人偏好 */}
              <div className={`view-page ${view === 'setup' ? '' : 'hidden'}`} data-view="setup">
                {viewHero('设置板块', '先把工具调教顺手，再开工。', 'SYSTEM SETUP')}
                <SetupPanel key={`setup-${projectId}`} projectId={projectId} onRefresh={refreshAll} refreshEpoch={dataEpoch} />
                {/* AI 模型只出现在上面的「制作环境」里：这里再渲染一次就是同一张卡片重复两遍。 */}
                <SettingsPanel
                  includeLlm={false}
                  onRefresh={refreshAll}
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
                  onValidateWorkflow={validateWorkflow}
                  onDeleteWorkflow={deleteWorkflow}
                  onBindStandardParameters={bindStandardParameters}
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
          </>}
        </div>
      </main>

      {wizard && <RealProductionWizard key={`${projectId}-${wizard}`}
        mode={wizard}
        projectId={projectId}
        onClose={() => setWizard(null)}
        onFinished={() => { setWizard(null); goToDirectorPage('produce'); }}
        onOpenExpertSetup={() => { setWizard(null); goToExpert('setup', 'setup'); }}
        onRefresh={refreshAll}
      />}

      {helpOpen && <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="帮助" onClick={() => setHelpOpen(false)}>
        <div className="help-modal" onClick={(event) => event.stopPropagation()}>
          <button className="modal-close" aria-label="关闭帮助" onClick={() => setHelpOpen(false)}><X size={16} /></button>
          <h2>三分钟看懂这个工作台</h2>
          <p className="help-lead">这是一台装在你自己电脑上的「迷你制片厂」：你定剧本和分镜，AI 按你的安排一镜一镜生成视频。所有素材、记录都留在本机，不经过云端。</p>
          <div className="help-section">
            <h3>制作七阶段（和顶部进度条一致）</h3>
            <ol className="help-steps">
              <li><b>创意与剧本</b> — 写想法或导入剧本，确认大纲。</li>
              <li><b>剧本统筹</b> — 定分集方案，选一套风格「冻结」。</li>
              <li><b>镜头拆解</b> — 剧组链把剧本拆成一个个镜头，逐场核对后应用。</li>
              <li><b>素材准备</b> — 按分镜清单准备素材并确认为主素材；缺的图 H3 会自行生成，不卡流程。</li>
              <li><b>导演包</b> — 冻结这次要拍的全部内容与执行政策。</li>
              <li><b>视频生产</b> — 一次启动，后台按批次跑完全部分集。</li>
              <li><b>后期成片</b> — 逐集剪辑、字幕、声音与交付。</li>
            </ol>
            <p className="help-note">「项目管理」一眼看全部作品；不知道下一步做什么时，看顶部进度条上高亮的阶段。装环境、配模型走「基础配置」，开拍前补上即可。</p>
          </div>
          <div className="help-section">
            <h3>演示与真实制作</h3>
            <ul className="help-glossary">
              <li><b>演示体验</b> — 模拟整个流程但不出真视频，用来练手和排查问题。</li>
              <li><b>启用真实制作</b> — 顶部的常驻入口。按向导检查本机环境，已有 ComfyUI 和模型会自动识别复用，不会重复下载。</li>
              <li><b>渲染器空闲</b> — 真实制作就绪后，本机渲染服务平时停着是正常的，开始制作时会自动启动。</li>
              <li><b>需人工复核</b> — 系统拿不准（比如提交结果不明、质检低置信）时必须由人决定，不会自动重试。</li>
            </ul>
          </div>
          <div className="help-section">
            <h3>遇到问题</h3>
            <ul className="help-glossary">
              <li>页面数据不对 → 点右上角「刷新」。</li>
              <li>任务不动了 → 顶部状态入口会显示原因；「检查并修复」会重新体检。</li>
              <li>需要高级能力（模型、工作流、调度诊断）→ 顶部切换「专业模式」。</li>
            </ul>
          </div>
        </div>
      </div>}

      {notices.length > 0 && <div className="toast-stack" aria-live="polite">
        {notices.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>
            <span>{toast.kind === 'success' ? <Check size={15} /> : <X size={15} />}</span>
            <span className="toast-text">{toast.text}{toast.count > 1 && <em className="toast-count">×{toast.count}</em>}</span>
            <button type="button" aria-label="关闭通知" onClick={() => dismissNotice(toast.id)}><X size={14} /></button>
          </div>
        ))}
      </div>}
      {busy && <div className="busy-indicator"><ArrowDownToLine size={14} />处理中</div>}
    </div>
  );
}

export default App;
