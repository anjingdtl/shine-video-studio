import { SetupPanel } from './components/SetupPanel';
import DirectorSetupPanel from './components/DirectorSetupPanel';
import { PreproductionPanel } from './components/PreproductionPanel';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowDownToLine, BookOpenText, Check, ChevronDown, CircleHelp, Clapperboard, Clock3,
  Film, FolderKanban, ListChecks, LockKeyhole, PackageCheck, PenLine, RefreshCw, Settings2, ShieldCheck,
  SlidersHorizontal, Sparkles, Users, Video, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useStudio } from './useStudio';
import { DemoRibbon, ModeStatusEntry } from './components/ModeStatusEntry';
import { RealProductionWizard } from './components/RealProductionWizard';
import AssetPanel from './components/AssetPanel';
import { MaterialPreparationPanel } from './components/MaterialPreparationPanel';
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

/** 导演模式：四个制作页面 + 一个不占流程序号的基础配置页。 */
type DirectorPage = 'projects' | 'assets' | 'produce' | 'deliver' | 'setup';
const DIRECTOR_PAGES: Array<{ id: DirectorPage; text: string; icon: LucideIcon; hint: string }> = [
  { id: 'projects', text: '我的作品', icon: FolderKanban, hint: '新建与管理作品' },
  { id: 'assets', text: '素材与风格', icon: Film, hint: '人物 · 场景 · 道具 · 风格' },
  { id: 'produce', text: 'AI导演制作', icon: Clapperboard, hint: '分镜 → 制作 → 进度' },
  { id: 'deliver', text: '成片中心', icon: Video, hint: '看成片 · 确认 · 导出' },
];
const DIRECTOR_PAGE_LABELS: Record<DirectorPage, string> = {
  projects: '我的作品', assets: '素材与风格', produce: 'AI导演制作', deliver: '成片中心', setup: '基础配置',
};

function resolveHash(hash: string): { ui: 'director' | 'expert'; view: ViewId; page: DirectorPage; anchor?: string } {
  const raw = hash.replace(/^#/, '');
  if (raw.startsWith('d/')) {
    const page = raw.slice(2) as DirectorPage;
    if (DIRECTOR_PAGES.some((item) => item.id === page) || page === 'setup') {
      return { ui: 'director', view: 'production', page };
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
    notice, report, dismissNotice, busy, dataEpoch, queueProvider, queueProfile, setQueueProfile, reviewScope,
    setReviewScope, reviewWidth, setReviewWidth, reviewHeight, setReviewHeight,
    realSubmissionAllowed, demoOverride, setDemoOverride, loadAudit, refreshAll,
    createProject, saveStyle, freezeStyle, uploadAsset, confirmAssets, patchAssetMetadata,
    createShot, saveBindings, previewShot, queueShot, togglePause, loadJobQC, loadJobGates,
    suggestRepair, applyRepair, recheckReview, updateProjectSettings, createProfile, testProfile,
    setProfileKey, deleteProfile, routeProfile, testComfy, registerWorkflow, validateWorkflow, deleteWorkflow,
    bindStandardParameters, abandonJob,
  } = studio;

  const [ui, setUi] = useState<'director' | 'expert'>(() => {
    const saved = window.localStorage.getItem('shine-interface');
    return saved === 'expert' ? 'expert' : 'director';
  });
  const [directorPage, setDirectorPage] = useState<DirectorPage>(() => resolveHash(window.location.hash).page);
  const [setupFocusTab, setSetupFocusTab] = useState<'env' | 'models' | 'voice'>('env');
  /** 制作页单任务聚焦：默认自动落在第一个未完成阶段，用户可用阶段条/摘要行切换；
   *  自动阶段推进时回到正轨。同一时刻只展开当前该做的那一个阶段。 */
  const [stageOverride, setStageOverride] = useState<'storyboard' | 'produce' | 'monitor' | null>(null);
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

  useEffect(() => { window.localStorage.setItem('shine-interface', ui); }, [ui]);

  /** 导演模式的全旅程：五步是否完成，全部由真实项目状态驱动，页面与步骤条共用同一份事实。 */
  const deliverableReady = useMemo(
    () => jobs.some((job) => job.status === 'PASSED' && job.video_created),
    [jobs],
  );
  const journey = useMemo(() => [
    { page: 'projects' as DirectorPage, cta: '创建我的第一部AI短剧', hint: '从一个作品开始：给它起个名字就行。', done: !!project },
    { page: 'assets' as DirectorPage, cta: '添加素材', hint: '上传人物、场景、道具参考，然后点击「确认使用」。', done: approvedAssets.length > 0 },
    { page: 'assets' as DirectorPage, cta: '确定作品风格', hint: '选好或确认七项风格约定并冻结，之后所有镜头都按它执行。', done: !!currentStyle },
    { page: 'produce' as DirectorPage, cta: 'AI 生成分镜', hint: 'AI 导演会把剧本拆成一个个镜头，你可以逐条修改。', done: shots.length > 0 },
    { page: 'produce' as DirectorPage, cta: '开始制作影片', hint: '勾选镜头，点击开始制作；本机自动生成、检查、返工。', done: deliverableReady },
  ], [project, approvedAssets.length, currentStyle, shots.length, deliverableReady]);
  /** 第一个未完成的旅程步骤；null 表示成片已生成，只剩查看与导出。 */
  const nextStep = journey.find((step) => !step.done) ?? null;
  /** 首页大按钮：有下一步就引导下一步，全部完成则指去成片中心。 */
  const heroGuidance = nextStep ?? { page: 'deliver' as DirectorPage, cta: '查看成片', hint: '影片已生成，去成片中心查看、确认并导出。' };
  /** 顶部步骤条的页面级完成态：✓ 只代表该页目标达成，随时可以回来调整。 */
  const navDone: Record<DirectorPage, boolean> = {
    projects: !!project,
    assets: approvedAssets.length > 0 && !!currentStyle,
    produce: deliverableReady,
    deliver: deliverableReady,
    setup: false,
  };
  /** 制作页自动聚焦：没镜头→分镜；正在制作→排产（盯进度）；制作结束→看结果。 */
  const makingNow = jobs.some((job) => ['SUBMITTING', 'RUNNING', 'QC_PENDING'].includes(job.status));
  const autoStage = !project || shots.length === 0 ? 'storyboard' : makingNow || !deliverableReady ? 'produce' : 'monitor';
  const focusStage = stageOverride ?? autoStage;
  // The person's own stage choice is theirs: it survives a page switch and the
  // auto advance, and only a new work replaces it.
  useEffect(() => { setStageOverride(null); }, [projectId]);

  function handleModeAction(action: 'enable_real' | 'start_making' | 'view_progress' | 'check_fix') {
    if (action === 'enable_real') setWizard('enable');
    else if (action === 'check_fix') setWizard('diagnose');
    else if (ui !== 'director') { setUi('director'); setDirectorPage('produce'); }
    else setDirectorPage('produce');
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

  function goToDirectorPage(page: DirectorPage) {
    setUi('director');
    setDirectorPage(page);
    window.history.pushState(null, '', `#d/${page}`);
    window.scrollTo({ top: 0 });
  }

  /** 进入导演层基础配置，可直达指定分区；普通入口回到①安装与下载。
   *  导演模式内所有“去配置模型/生成方案”的引导都走这里，不再甩去专业模式。 */
  function goToDirectorSetup(tab: 'env' | 'models' | 'voice' = 'env') {
    setSetupFocusTab(tab);
    setUi('director');
    setDirectorPage('setup');
    window.history.pushState(null, '', '#d/setup');
    window.scrollTo({ top: 0 });
  }

  /** 制作页内三个阶段的锚点滚动（面板保持挂载，运行中的任务不中断）。 */
  function scrollToStage(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
      if (target.ui === 'director') {
        setUi('director');
        setDirectorPage(target.page);
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
    return () => window.removeEventListener('popstate', onPop);
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

  const modeLabel = production?.label ?? '状态未知';

  const workspaceReconnect = <div className="workspace-reconnect" aria-busy={reconnecting}>
    <button type="button" className="primary-button" disabled={reconnecting} onClick={reconnectWorkbench}>
      {reconnecting ? reconnectLabel : '重新连接'}
    </button>
    {reconnecting && <div role="status"><progress aria-label={reconnectLabel} /></div>}
    {reconnectError && <p role="alert">{reconnectError}</p>}
  </div>;

  return (
    <div className={`app-shell ${ui === 'director' ? 'ui-director' : 'ui-expert'}`}>
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
        <div className="privacy-card"><ShieldCheck size={17} /><div><b>本地优先</b><span>素材与密钥不离开本机</span></div><Check size={15} /></div>
        <div className="profile"><div className="avatar">S</div><div><b>本地工作区</b><span>{studio.apiOnline ? '已连接本机服务' : '本机服务未连接'}</span></div><ChevronDown size={16} /></div>
      </aside>}

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作区</span><span className="crumb-slash">/</span>
            {ui === 'director'
              ? <b>{DIRECTOR_PAGE_LABELS[directorPage]}</b>
              : <><b>{VIEW_TITLES[view]}</b>{project && <><span className="crumb-slash">/</span><span>{project.title}</span></>}</>}
            {ui === 'director' && project && <><span className="crumb-slash">/</span><span>{project.title}</span></>}
          </div>
          <div className="top-actions">
            <span className="service-status" aria-live="polite">
              <i aria-hidden="true" className={`status-led ${apiOnline ? '' : systemChecked ? 'offline' : 'checking'}`} />
              {apiOnline ? '工作台后端在线' : systemChecked ? '工作台后端失联' : '正在检查工作台后端'}
            </span>
            {systemChecked && (!apiOnline || reconnecting || !!projectsLoadError) && <button type="button" className="secondary-button"
              disabled={reconnecting}
              title="后端未启动时，请先启动本机工作台"
              onClick={reconnectWorkbench}>{reconnecting ? reconnectLabel : '重连工作台'}</button>}
            {reconnectError && projectsLoaded && <span role="alert" className="service-status">{reconnectError}</span>}
            {projects.length > 1 && ui === 'director' && <select className="topbar-project" aria-label="快速切换作品" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
            </select>}
            {ui === 'director' && <button
              type="button"
              className={`setup-entry ${directorPage === 'setup' ? 'active' : ''} ${production && !['READY', 'RUNNING'].includes(production.mode) ? 'needs-setup' : ''}`}
              title="配置文本模型、安装本机环境、登记生成方案（不需要切换到专业模式）"
              onClick={() => goToDirectorSetup('env')}
            >
              <Settings2 size={13} /><span>基础配置</span>
              {production && !['READY', 'RUNNING'].includes(production.mode) && <i className="setup-entry-dot" aria-label="还有配置未完成" />}
            </button>}
            <ModeStatusEntry system={system} onAction={handleModeAction} />
            <button
              type="button"
              className={`ui-toggle ${ui === 'director' ? 'on-director' : 'on-expert'}`}
              title={ui === 'director' ? '切换到专业模式：模型、工作流、调度诊断等全部高级能力' : '回到导演模式：普通用户的简洁制作流程'}
              onClick={() => { setUi(ui === 'director' ? 'expert' : 'director'); window.scrollTo({ top: 0 }); }}
            >
              <Clapperboard size={13} /><span>导演模式</span>
              <i className="ui-toggle-sep" />
              <SlidersHorizontal size={13} /><span>专业模式</span>
            </button>
            <button className="icon-button" title="刷新全部数据" onClick={() => refreshAll().catch((error: Error) => report(error.message, 'error'))}><RefreshCw size={15} /></button>
            <button className="help-button" onClick={() => setHelpOpen(true)}><CircleHelp size={15} /> 帮助</button>
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
            {directorPage !== 'setup' && !project && <section className="first-run card">
              <div className="first-run-art"><Clapperboard size={30} /></div>
              {projectsLoaded ? <>
                <h2>创建我的第一部AI短剧</h2>
                <p>五步走完一部片子，所有内容都保存在你自己的电脑上。现在只需要做第一步：</p>
                <ol className="first-run-journey" aria-label="制作全旅程">
                  <li className="now"><b>① 起个名字</b><span>建作品（现在做）</span></li>
                  <li><b>② 调好机器</b><span>可跳过，开拍前补上</span></li>
                  <li><b>③ 传素材 · 定风格</b><span>人物 / 场景 / 道具</span></li>
                  <li><b>④ AI 分镜</b><span>剧本交给 AI 拆镜头</span></li>
                  <li><b>⑤ 制作 · 导出</b><span>本机生成成片</span></li>
                </ol>
                <div className="create-project">
                  <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="给作品起个名字（如：巷口的灯）" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
                  <button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>创建作品</button>
                </div>
                <div className="first-run-setup">
                  <b>第一次使用？先花两分钟把机器调好。</b>
                  <span>配置文本模型、检测/安装本机 ComfyUI 与 H3、登记生成方案——都在导演模式内完成。</span>
                  <button className="secondary-button" onClick={() => goToDirectorSetup('env')}>
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
              <nav className="director-steps" aria-label="制作流程" hidden={directorPage === 'setup'}>
                {DIRECTOR_PAGES.map((page, index) => {
                  const Icon = page.icon;
                  const active = directorPage === page.id;
                  const isGuidance = nextStep?.page === page.id;
                  const stepDone = navDone[page.id];
                  return (
                    <button
                      key={page.id}
                      type="button"
                      className={`director-step ${active ? 'active' : ''} ${isGuidance ? 'next-up' : ''} ${stepDone ? 'is-done' : ''}`}
                      onClick={() => goToDirectorPage(page.id)}
                    >
                      <span className="director-step-no" title={stepDone ? '这一页的目标已完成' : `第 ${index + 1} 步`}>
                        {stepDone ? <Check size={11} /> : index + 1}
                      </span>
                      <Icon size={16} />
                      <span className="director-step-text">{page.text}</span>
                      {isGuidance && <em className="director-step-flag">下一步</em>}
                    </button>
                  );
                })}
              </nav>
              {directorPage !== 'projects' && directorPage !== 'setup' && (nextStep || directorPage !== 'deliver') && (nextStep ? (
                <div className="next-step-bar" role="status">
                  <span className="next-step-tag">下一步</span>
                  <div className="next-step-text">
                    <b>{nextStep.cta}</b>
                    <span>{nextStep.hint}</span>
                  </div>
                  <button type="button" className="must-button" onClick={() => goToDirectorPage(nextStep.page)}>
                    去这一步
                  </button>
                </div>
              ) : (
                <div className="next-step-bar done" role="status">
                  <Check size={15} />
                  <div className="next-step-text">
                    <b>全部制作步骤已完成</b>
                    <span>到「成片中心」查看、确认并导出影片。</span>
                  </div>
                  <button type="button" className="secondary-button" onClick={() => goToDirectorPage('deliver')}>去看成片</button>
                </div>
              ))}
              <div hidden={directorPage === 'setup'}><DemoRibbon production={production ?? {
                mode: 'BLOCKED', label: '状态未知', detail: '暂无法确认制作环境。', action: null, code: null,
                render_provider: 'mock', runtime_status: 'NOT_ACTIVATED', renderer_online: false,
                worker_online: false, running_jobs: 0, series_active: 0,
              }} onEnable={() => setWizard('enable')} /></div>

              {directorPage === 'projects' && <div className="director-page">
                <section className="welcome-row">
                  <div>
                    <div className="eyebrow"><span className="eyebrow-line" /> 我的作品</div>
                    <h1>让好故事，<em>有章可循。</em></h1>
                    <p>{heroGuidance.hint}</p>
                  </div>
                  <button className="primary-button big" onClick={() => goToDirectorPage(heroGuidance.page)}>{heroGuidance.cta}</button>
                </section>
                <section className="metrics-grid">
                  <div className="metric-card"><div className="metric-top"><span>作品素材</span><div className="metric-icon mint"><Film size={15} /></div></div><div className="metric-value">{assets.length}<small>项</small></div><div className="metric-foot">{approvedAssets.length} 项已确认可用</div></div>
                  <div className="metric-card"><div className="metric-top"><span>作品风格</span><div className="metric-icon sand"><LockKeyhole size={15} /></div></div><div className="metric-value">{currentStyle ? `第 ${currentStyle.version} 版` : '—'}</div><div className="metric-foot">{currentStyle ? '已冻结，全部镜头按此执行' : '待确认冻结'}</div></div>
                  <div className="metric-card"><div className="metric-top"><span>分镜</span><div className="metric-icon blue"><Activity size={15} /></div></div><div className="metric-value">{shots.length}<small>个</small></div><div className="metric-foot">{jobs.filter((job) => job.status === 'PASSED').length} 个镜头已完成</div></div>
                  <div className="metric-card accent-metric"><div className="metric-top"><span>制作方式</span><div className="metric-icon cream"><Sparkles size={15} /></div></div><div className="metric-value small-value">{modeLabel}</div><div className="metric-foot">{production?.detail ?? ''}</div></div>
                </section>
                <section className="project-selector card" id="overview">
                  <div className="project-icon"><Clapperboard size={20} /></div>
                  <div className="project-meta"><span>当前作品</span><b>{project.title}</b></div>
                  <select aria-label="切换作品" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select>
                  <div className="selector-divider" />
                  <input className="new-project-inline" value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="新作品名称" />
                  <button className="subtle-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建作品</button>
                </section>
                <div className="module-guide">
                  <CircleHelp size={13} />
                  <div>
                    <p><b>五步走完一部片子：</b>建作品 → 传素材、定风格（在「素材与风格」）→ AI 分镜（在「AI导演制作」）→ 勾选镜头开始制作 → 到「成片中心」导出。顶部步骤条会打勾标记已完成的页面。</p>
                    <p><b>每一步都有必须点一次的按钮：</b>它们是橙色高亮的；不知道做什么时，看页面顶部的「下一步」提示即可。</p>
                  </div>
                </div>
              </div>}

              <div className="director-page" id="assets" hidden={directorPage !== 'assets'}>
                <MaterialPreparationPanel
                  key={`material-${projectId}`}
                  projectId={projectId}
                  onMessage={report}
                  frozenStyle={currentStyle}
                  onPromoted={() => { refreshAll().catch(() => undefined); }}
                />
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
                  onDraftApplied={() => { refreshAll().catch(() => undefined); }}
                  onNavigate={(id) => (id === 'settings' ? goToDirectorSetup('models') : goTo(id))}
                  onMessage={report}
                />
              </div>

              <div className="director-page" id="produce" hidden={directorPage !== 'produce'}>
                <nav className="produce-stages" aria-label="制作阶段">
                  {([
                    { id: 'storyboard', icon: PenLine, label: '① 剧本分镜', done: shots.length > 0 },
                    { id: 'produce', icon: Video, label: '② 排产制作', done: deliverableReady },
                    { id: 'monitor', icon: Activity, label: '③ 进度与结果', done: deliverableReady },
                  ] as const).map((stage) => {
                    const Icon = stage.icon;
                    return (
                      <button type="button" key={stage.id}
                        className={focusStage === stage.id ? 'active' : ''}
                        onClick={() => { setStageOverride(stage.id); scrollToStage(stage.id === 'storyboard' ? 'storyboard' : stage.id === 'produce' ? 'production-series' : 'production-monitor'); }}>
                        <Icon size={13} />{stage.label}
                        {stage.done && <Check size={12} className="stage-done-mark" />}
                      </button>
                    );
                  })}
                </nav>
                <details className="stage-dock" id="storyboard-dock" open={focusStage === 'storyboard'}
                  onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) setStageOverride('storyboard'); }}>
                  <summary>
                    {shots.length > 0
                      ? <><Check size={13} className="stage-done-mark" /> ① 剧本分镜 · 已应用 {shots.length} 镜（点击展开修改）</>
                      : '① 剧本分镜 · 当前任务'}
                  </summary>
                  <StoryboardPanel key={`storyboard-${projectId}`} projectId={projectId} onRefresh={refreshAll}
                    backendDisconnected={systemChecked && !apiOnline}
                    visible={directorPage === 'produce' && focusStage === 'storyboard'}
                    onNavigate={(page) => goToDirectorPage(page as DirectorPage)} />
                  {routes.length === 0 && <div className="blocked-callout">
                    <Sparkles size={15} />
                    <span>AI 分镜能力未就绪：还没有配置模型服务。你仍然可以手工新建分镜，或</span>
                    <button type="button" className="text-button" onClick={() => goToDirectorSetup('models')}>去配置模型服务</button>
                  </div>}
                </details>
                <details className="stage-dock" id="series-dock" open={focusStage === 'produce'}
                  onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) setStageOverride('produce'); }}>
                  <summary>
                    {makingNow
                      ? `② 排产制作 · 制作进行中 ${jobs.filter((job) => job.status === 'PASSED').length}/${jobs.filter((job) => job.status !== 'CANCELLED').length} 镜（点击查看进度）`
                      : deliverableReady
                        ? <><Check size={13} className="stage-done-mark" /> ② 排产制作 · 已出片（点击展开再拍）</>
                        : '② 排产制作 · 当前任务'}
                  </summary>
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
                    onModeAction={handleModeAction}
                    onGoSetup={() => goToDirectorSetup('models')}
                  />
                <details className="advanced-fold">
                  <summary>单镜头制作与任务明细（高级）</summary>
                  <div className="queue-provider-card card">
                    <div className="section-kicker">生成方式</div>
                    <p className="queue-provider-hint">
                      {realSubmissionAllowed
                        ? '默认真实生成（本机显卡 + H3）。也可以临时切回演示，演练单个镜头。'
                        : '当前为演示体验：不消耗显卡、不出真视频。点击顶部「启用真实制作」后可真实生成。'}
                    </p>
                    <label>生成方式
                      {realSubmissionAllowed
                        ? <select value={demoOverride ? 'mock' : 'comfyui'} onChange={(event) => setDemoOverride(event.target.value === 'mock')}>
                            <option value="comfyui">真实生成（默认）</option>
                            <option value="mock">演示（不出真视频）</option>
                          </select>
                        : <select value={queueProvider} disabled={queueProvider === 'mock'} onChange={(event) => setDemoOverride(event.target.value === 'mock')}>
                            <option value="comfyui" disabled>真实制作受阻，请检查环境</option>
                            <option value="mock">演示体验（真实制作未启用）</option>
                          </select>}
                    </label>
                    {queueProvider === 'comfyui' && <>
                      <label>生成方案
                        <select value={queueProfile} onChange={(event) => setQueueProfile(event.target.value)}>
                          <option value="">请选择…</option>
                          {workflowProfiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </select>
                      </label>
                      <label>审片建议（可选）
                        <select value={reviewScope} onChange={(event) => setReviewScope(event.target.value as 'basic' | 'full')}>
                          <option value="full">完整建议：画面、对白与风格（只记录，不阻断）</option>
                          <option value="basic">基础建议：人物与对白（只记录，不阻断）</option>
                        </select>
                      </label>
                      {reviewScope === 'basic' && <>
                        <label>预期画面宽度<input type="number" min="2" max="8192" step="1" value={reviewWidth} onChange={(event) => setReviewWidth(event.target.value)} /></label>
                        <label>预期画面高度<input type="number" min="2" max="8192" step="1" value={reviewHeight} onChange={(event) => setReviewHeight(event.target.value)} /></label>
                        <p>宽高只做视频技术校验，不会改动生成设置；小模型人物和对白建议不会阻止制作。</p>
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
                </details>
                </details>
                <details className="stage-dock" id="production-monitor" open={focusStage === 'monitor'}
                  onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) setStageOverride('monitor'); }}>
                  <summary>
                    {jobs.length === 0 ? '③ 进度与结果 · 暂无任务'
                      : makingNow ? `③ 进度与结果 · 制作中（点击查看）`
                        : jobs.every((job) => job.status === 'PASSED' || job.status === 'CANCELLED')
                          ? <><Check size={13} className="stage-done-mark" /> ③ 进度与结果 · 全部完成</>
                          : `③ 进度与结果 · ${jobs.filter((job) => job.status === 'PASSED').length}/${jobs.length} 完成（点击查看）`}
                  </summary>
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
              </div>

              <div className="director-page" id="deliver" hidden={directorPage !== 'deliver'}>
                <DeliveryPanel key={`delivery-${projectId}`} projectId={projectId} jobs={jobs} active={directorPage === 'deliver'} refreshEpoch={dataEpoch} />
              </div>

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
                  <button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建项目</button>
                </div>}
                {!projectsLoaded && workspaceReconnect}
                {projectsLoaded && view !== 'setup' && <p className="empty-setup-hint">
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
                  <div className="metric-card accent-metric"><div className="metric-top"><span>制作方式</span><div className="metric-icon cream"><Sparkles size={15} /></div></div><div className="metric-value small-value">{modeLabel}</div><div className="metric-foot">{production?.renderer_online ? '本机渲染器在线' : production?.detail ?? ''}</div></div>
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
                  onDraftApplied={() => { refreshAll().catch(() => undefined); }}
                  onNavigate={goTo}
                  onMessage={report}
                />

                <PreproductionPanel key={`prep-${projectId}`} projectId={projectId} onRefresh={refreshAll} reviewScope={reviewScope} />

                <StoryboardPanel key={`storyboard-${projectId}`} projectId={projectId} onRefresh={refreshAll}
                  backendDisconnected={systemChecked && !apiOnline}
                  visible={view === 'production'}
                  onNavigate={(page) => goToDirectorPage(page as DirectorPage)} />

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
                  onModeAction={handleModeAction}
                  onGoSetup={() => goToDirectorPage('setup')}
                />

                <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> V1.1 DEVELOPMENT</span><span><ShieldCheck size={12} /> 本地数据库 · 素材由你掌控</span></footer>
              </div>

              {/* 设置板块：装机、模型与系统、个人偏好 */}
              <div className={`view-page ${view === 'setup' ? '' : 'hidden'}`} data-view="setup">
                {viewHero('设置板块', '先把工具调教顺手，再开工。', 'SYSTEM SETUP')}
                <SetupPanel key={`setup-${projectId}`} projectId={projectId} onRefresh={refreshAll} refreshEpoch={dataEpoch} />
                <SettingsPanel
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
          <p className="help-lead">这是一台装在你自己电脑上的「迷你制片厂」：你准备素材和风格，AI 按你的安排一镜一镜生成视频。所有素材、记录都留在本机，不经过云端。</p>
          <div className="help-section">
            <h3>推荐的顺序（和顶部步骤条一致）</h3>
            <ol className="help-steps">
              <li><b>建作品</b> — 「我的作品」起个名字就行。</li>
              <li><b>调机器</b>（可跳过）— 「基础配置」装本机环境、配文本模型、选生成方案；开拍前补上即可。</li>
              <li><b>传素材 · 定风格</b> — 「素材与风格」上传人物、场景、道具参考并「确认使用」，再选一套风格「冻结」。</li>
              <li><b>AI 分镜</b> — 「AI导演制作」把剧本拆成一个个镜头，逐条核对后应用。</li>
              <li><b>制作 · 导出</b> — 勾选镜头点「开始制作影片」，完成后到「成片中心」播放并导出。</li>
            </ol>
            <p className="help-note">不知道下一步做什么时，看页面顶部的「下一步」提示条；完成的页面会在步骤条上打勾。</p>
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

      {notice && <div className={`toast ${notice.kind}`}><span>{notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}</span>{notice.text}<button onClick={dismissNotice}><X size={14} /></button></div>}
      {busy && <div className="busy-indicator"><ArrowDownToLine size={14} />处理中</div>}
    </div>
  );
}

export default App;
