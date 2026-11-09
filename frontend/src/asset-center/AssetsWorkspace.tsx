import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Archive, ArrowLeft, ArrowRight, AudioLines, BadgeCheck, Brush, Check, ChevronDown, CircleHelp,
  Copy, Eraser, FileAudio2, FileImage, ImagePlus, LibraryBig, LoaderCircle, Mic2,
  RefreshCw, RotateCcw, RotateCw, Search, ShieldCheck, Sparkles, Star, Upload, WandSparkles, X,
} from 'lucide-react';
import { ApiError } from '../api';
import type { Asset, Project } from '../api';
import {
  archiveLibraryAsset, assignLibraryVersion, createImageJobs, createLibraryAsset,
  createVoiceFreezeGrant, createVoiceJob, freezeLibraryVoice, getCapabilities, getLibraryAsset,
  getLibraryTask, grantVersionPromotion, listAssignments, listLibraryAssets, listLibraryVoices,
  newLibraryImageAttempt,
  patchLibraryAsset, promoteLibraryVersion, qcLibraryVersion, reconcileLibraryImageTask,
  saveProjectAssetToLibrary,
  uploadLibraryMedia,
} from './api';
import type { CapabilityReport, LibraryAsset, LibraryTask, LibraryVersion } from './api';
import './assets.css';

export type AssetCenterPage = 'home' | 'images' | 'voices' | 'library' | 'assignments';
type ReferenceMedia = {
  id: string; filename: string; sha256: string; width: number | null; height: number | null;
  media_url: string; rights_id: string; role: string; order: number; media_id?: string; version_id?: string;
};
type PendingIntent = { idempotency_key: string; fingerprint: string; body: Record<string, unknown> };

const PAGES: Array<{ id: AssetCenterPage; label: string; icon: typeof Sparkles }> = [
  { id: 'home', label: '创作首页', icon: Sparkles },
  { id: 'images', label: '图片工坊', icon: FileImage },
  { id: 'voices', label: '声音工坊', icon: AudioLines },
  { id: 'library', label: '全局素材库', icon: LibraryBig },
  { id: 'assignments', label: '项目分配', icon: Copy },
];

const LABELS: Record<string, string> = {
  BLOCKED_CAPABILITY: '等待本机能力验证', QUEUED: '排队中', WAITING_GPU: '等待显卡资源', RUNNING: '生成中',
  CANDIDATE_READY: '候选已生成', READY_FOR_REVIEW: '等待人工审核', PROMOTED: '已晋升', UNKNOWN: '结果待核对',
  RECONCILE_REQUIRED: '需要核对', FAILED: '未完成', CANCELLED: '已取消', CANCEL_REQUESTED: '正在安全取消',
  CANDIDATE: '待技术检查', QC_FAILED: '技术检查未通过', FROZEN: '已试听冻结', PREVIEW: '试听审核中',
};

const REFERENCE_ROLE_LABELS: Record<string, string> = {
  canvas: '编辑底图', subject: '人物主体', face: '面部', garment: '服装', hair: '发型',
  shoe: '鞋帽', bag: '包袋', hat: '帽饰', accessory: '配饰', prop: '道具', scene: '场景', style: '风格参考',
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function storedObject(key: string): Record<string, any> {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function readPendingIntent(key: string): PendingIntent | null {
  const value = storedObject(key);
  if (typeof value.idempotency_key !== 'string' || typeof value.fingerprint !== 'string'
    || !value.body || typeof value.body !== 'object' || Array.isArray(value.body)) return null;
  return value as PendingIntent;
}

function preparePendingIntent(key: string, body: Record<string, unknown>): PendingIntent {
  const fingerprint = JSON.stringify(body);
  const prior = readPendingIntent(key);
  if (prior) {
    if (prior.fingerprint !== fingerprint) {
      throw new Error('上一次请求结果还未确认。请先核对并恢复同一请求，再开始新的创作。');
    }
    return prior;
  }
  const intent = { idempotency_key: crypto.randomUUID(), fingerprint, body };
  window.localStorage.setItem(key, JSON.stringify(intent));
  return intent;
}

function clearPendingIntent(key: string, intent: PendingIntent, update: (value: PendingIntent | null) => void) {
  if (readPendingIntent(key)?.idempotency_key === intent.idempotency_key) {
    window.localStorage.removeItem(key);
    update(null);
  }
}

function isDefinitiveRejection(error: unknown) {
  return error instanceof ApiError && error.status >= 400 && error.status < 500
    && !error.message.includes('IDEMPOTENCY_KEY_REUSED');
}

function taskLabel(task: LibraryTask) {
  const specific = task.error_code ? `：${task.error_code}` : '';
  return `${LABELS[task.status] ?? task.status}${specific}`;
}

function useFreshList(query = '') {
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = () => setRefreshKey((n) => n + 1);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    listLibraryAssets(query).then((result) => { if (alive) { setAssets(result.items); setError(''); } })
      .catch((reason) => { if (alive) setError(errorText(reason)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [query, refreshKey]);
  return { assets, setAssets, loading, error, refresh };
}

function capabilityLabel(capability: { state: string; reason: string; can_generate?: boolean; can_queue?: boolean }) {
  if (capability.can_generate) return '本机已验证';
  if (capability.can_queue) return '可提交 · 生成后试听';
  if (capability.can_queue === false && capability.state !== 'READY') return '本机声音运行时未就绪';
  if (capability.state === 'PENDING_LOCAL_VALIDATION') return '待本机验证';
  return capability.state;
}

export function AssetsWorkspace({
  page, onNavigate, onWorkspace, projects, projectId, projectAssets,
}: {
  page: AssetCenterPage; onNavigate: (page: AssetCenterPage) => void;
  onWorkspace: (space: 'director' | 'expert') => void; projects: Project[]; projectId: string;
  projectAssets: Asset[];
}) {
  const [capabilities, setCapabilities] = useState<CapabilityReport | null>(null);
  const [capabilityError, setCapabilityError] = useState('');
  const [capabilityRevision, setCapabilityRevision] = useState(0);
  const refreshCapabilities = useCallback(() => setCapabilityRevision((revision) => revision + 1), []);
  useEffect(() => {
    let alive = true;
    getCapabilities().then((row) => { if (alive) { setCapabilities(row); setCapabilityError(''); } })
      .catch((error) => { if (alive) setCapabilityError(errorText(error)); });
    return () => { alive = false; };
  }, [capabilityRevision]);
  const title = PAGES.find((item) => item.id === page)?.label ?? '创作首页';

  function navigate(next: AssetCenterPage) {
    onNavigate(next);
    window.history.pushState(null, '', `#a/${next}`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  return (
    <div className="asset-center-shell">
      <aside className="asset-center-sidebar">
        <div className="asset-center-brand"><span className="asset-brand-mark"><Sparkles size={18} /></span><span><b>SHINE ASSETS</b><small>素材整备中心</small></span></div>
        <nav aria-label="素材中心导航" className="asset-center-nav">
          {PAGES.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} type="button" aria-current={page === item.id ? 'page' : undefined}
              className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}>
              <Icon size={17} /><span>{item.label}</span>{page === item.id && <i />}
            </button>;
          })}
        </nav>
        <div className="asset-center-sidebar-note"><ShieldCheck size={16} /><span>素材保存在本机，已晋升版本可分配给作品。</span></div>
        <div className="asset-center-workspace-links">
          <button onClick={() => onWorkspace('director')}><ArrowLeft size={14} />导演工作台</button>
          <button onClick={() => onWorkspace('expert')}><ChevronDown size={14} />专业设置</button>
        </div>
      </aside>
      <main className="asset-center-main">
        <header className="asset-center-header">
          <div><div className="asset-eyebrow">SHINE ASSETS · 独立素材空间</div><h1>{title}</h1></div>
          <div className="asset-center-header-actions">
            <span className="asset-local-badge"><i />本机素材库</span>
            <button className="asset-icon-button" aria-label="重新读取能力状态" onClick={refreshCapabilities}><RefreshCw size={15} /></button>
          </div>
        </header>
        {capabilityError && <div className="asset-inline-error" role="alert">暂时无法读取本机能力状态：{capabilityError}</div>}
        <div className="asset-center-page">
          {page === 'home' && <HomePage capabilities={capabilities} onNavigate={navigate} />}
          {page === 'images' && <ImageWorkshop capabilities={capabilities} onNavigate={navigate} onValidated={refreshCapabilities} />}
          {page === 'voices' && <VoiceWorkshop capabilities={capabilities} onNavigate={navigate} />}
          {page === 'library' && <LibraryPage />}
          {page === 'assignments' && <AssignmentsPage projects={projects} projectId={projectId} projectAssets={projectAssets} />}
        </div>
      </main>
    </div>
  );
}

function HomePage({ capabilities, onNavigate }: { capabilities: CapabilityReport | null; onNavigate: (page: AssetCenterPage) => void }) {
  const { assets, loading, error } = useFreshList();
  const [tasks, setTasks] = useState<LibraryTask[]>([]);
  const [taskError, setTaskError] = useState('');
  const taskIdsKey = 'shine-assets-recent-tasks';
  useEffect(() => {
    let ids: Array<{ id: string; kind: 'image' | 'voice' }> = [];
    try { ids = JSON.parse(window.localStorage.getItem(taskIdsKey) ?? '[]').slice(0, 8); } catch { ids = []; }
    let alive = true;
    Promise.all(ids.map((row) => getLibraryTask(row.id, row.kind).catch(() => null))).then((rows) => {
      if (alive) setTasks(rows.filter((row): row is LibraryTask => row !== null));
    }).catch((reason) => { if (alive) setTaskError(errorText(reason)); });
    return () => { alive = false; };
  }, []);
  const shortcuts = [
    { icon: WandSparkles, title: '从文字开始', description: '写下画面想法，准备生成图片候选。', page: 'images' as const, mode: 'T2I' },
    { icon: ImagePlus, title: '编辑一张图片', description: '保留原图，添加修改指令和参考。', page: 'images' as const, mode: 'I2I' },
    { icon: Brush, title: '局部修订', description: '用画布涂出要修改的像素区域。', page: 'images' as const, mode: 'INPAINT' },
    { icon: AudioLines, title: '整理声音', description: '导入对白、音色、音效或音乐。', page: 'voices' as const, mode: '' },
  ];
  return <>
    <section className="asset-welcome">
      <div><div className="asset-eyebrow">不需要先创建影视项目</div><h2>把灵感先准备成可复用的素材。</h2>
        <p>导入、创作、审核并整理图片与声音。需要时再把选定版本分配给导演工作台中的作品。</p></div>
      <div className="asset-welcome-art"><div className="asset-art-card art-one"><FileImage size={23} /></div><div className="asset-art-card art-two"><AudioLines size={22} /></div><div className="asset-art-orbit" /></div>
    </section>
    <section className="asset-section">
      <div className="asset-section-heading"><div><h2>开始创作</h2><p>选择一种常见操作。</p></div></div>
      <div className="asset-shortcuts">
        {shortcuts.map((item) => { const Icon = item.icon; return <button key={item.title} className="asset-shortcut" onClick={() => onNavigate(item.page)}>
          <span className="asset-shortcut-icon"><Icon size={19} /></span><b>{item.title}</b><span>{item.description}</span><i><ArrowRight size={15} /></i>
        </button>; })}
      </div>
    </section>
    <section className="asset-home-grid">
      <div className="asset-card asset-status-card"><div className="asset-card-heading"><div><h2>本机能力</h2><p>模型能力和真实推理状态分开显示。</p></div><CircleHelp size={17} /></div>
        <CapabilityRows capabilities={capabilities} />
      </div>
      <div className="asset-card"><div className="asset-card-heading"><div><h2>最近任务</h2><p>刷新页面后仍可继续查看任务状态。</p></div><button className="asset-text-button" onClick={() => onNavigate('images')}>打开工坊</button></div>
        {taskError && <p className="asset-error-text">{taskError}</p>}
        {tasks.length ? <div className="asset-task-list">{tasks.slice(0, 5).map((task) => <TaskRow key={task.id} task={task} />)}</div> : <div className="asset-empty-small">{capabilities ? '还没有任务。' : '正在读取任务状态…'}</div>}
      </div>
    </section>
    <section className="asset-card asset-recent-card"><div className="asset-card-heading"><div><h2>最近素材</h2><p>全局库中的素材及版本，不依赖任何影视项目。</p></div><button className="asset-text-button" onClick={() => onNavigate('library')}>查看全部 <ArrowRight size={14} /></button></div>
      {loading ? <p className="asset-muted">正在读取素材…</p> : error ? <p className="asset-error-text">{error}</p> : assets.length ? <div className="asset-compact-grid">{assets.slice(0, 6).map((asset) => <MiniAsset key={asset.id} asset={asset} />)}</div> : <div className="asset-empty-small">素材库是空的。可以先在图片工坊导入一张图片。</div>}
    </section>
  </>;
}

function CapabilityRows({ capabilities }: { capabilities: CapabilityReport | null }) {
  const rows = [
    ['文生图', capabilities?.image_capabilities.QWEN_IMAGE_T2I],
    ['图生图与多图合成', capabilities?.image_capabilities.QWEN_IMAGE_I2I],
    ['局部修订遮罩', capabilities?.image_capabilities.QWEN_IMAGE_INPAINT],
    ['透明 PNG 与 2K', capabilities?.image_capabilities.QWEN_IMAGE_RGBA],
    ['声音设计与普通话对白', capabilities?.voice_capabilities.TTS_MANDARIN],
  ] as const;
  return <div className="asset-capability-list">{rows.map(([label, capability]) => <div key={label} className="asset-capability-row">
    <span>{label}</span><b className={capability?.can_generate ? 'ready' : 'pending'}><i />{capability ? capabilityLabel(capability) : '读取中'}</b>
  </div>)}</div>;
}

function ImageWorkshop({ capabilities, onNavigate, onValidated }: {
  capabilities: CapabilityReport | null;
  onNavigate: (page: AssetCenterPage) => void;
  onValidated: () => void;
}) {
  const draftStorageKey = 'shine-assets-image-draft-v1';
  const restoredDraft = storedObject(draftStorageKey);
  const pendingIntentStorageKey = 'shine-assets-pending-image-intent-v1';
  const [mode, setMode] = useState(typeof restoredDraft.mode === 'string' ? restoredDraft.mode : 'T2I');
  const [title, setTitle] = useState(typeof restoredDraft.title === 'string' ? restoredDraft.title : '');
  const [prompt, setPrompt] = useState(typeof restoredDraft.prompt === 'string' ? restoredDraft.prompt : '');
  const [category, setCategory] = useState(typeof restoredDraft.category === 'string' ? restoredDraft.category : 'character');
  const [width, setWidth] = useState(Number(restoredDraft.width) || 1024);
  const [height, setHeight] = useState(Number(restoredDraft.height) || 1024);
  const [candidateCount, setCandidateCount] = useState(Number(restoredDraft.candidateCount) || 1);
  const [transparent, setTransparent] = useState(Boolean(restoredDraft.transparent));
  const [references, setReferences] = useState<ReferenceMedia[]>(Array.isArray(restoredDraft.references) ? restoredDraft.references : []);
  const [maskMedia, setMaskMedia] = useState<ReferenceMedia | null>(restoredDraft.maskMedia ?? null);
  const [targetAssetId, setTargetAssetId] = useState<string>(restoredDraft.targetAssetId ?? '');
  const [pendingIntent, setPendingIntent] = useState<PendingIntent | null>(() => readPendingIntent(pendingIntentStorageKey));
  const [reusableAssets, setReusableAssets] = useState<LibraryAsset[]>([]);
  const [tasks, setTasks] = useState<LibraryTask[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [savedMask, setSavedMask] = useState(Boolean(restoredDraft.maskMedia));
  const fileInput = useRef<HTMLInputElement>(null);
  const taskStorageKey = 'shine-assets-recent-tasks';
  useEffect(() => {
    let alive = true;
    listLibraryAssets('', '').then((result) => {
      if (alive) setReusableAssets(result.items.filter((asset) => asset.kind === 'image' && asset.current_version?.status === 'PROMOTED'));
    }).catch(() => { if (alive) setReusableAssets([]); });
    return () => { alive = false; };
  }, []);
  const t2iCapability = capabilities?.image_capabilities.QWEN_IMAGE_T2I;
  const selectedCapability = mode === 'T2I'
    ? (transparent ? capabilities?.image_capabilities.QWEN_IMAGE_RGBA : t2iCapability)
    : capabilities?.image_capabilities[`QWEN_IMAGE_${mode}`];
  const readyTaskIds = tasks.filter((task) => task.status === 'CANDIDATE_READY').map((task) => task.id).join('|');
  useEffect(() => {
    if (readyTaskIds) onValidated();
  }, [readyTaskIds, onValidated]);

  function fitEditSize(sourceWidth: number, sourceHeight: number) {
    const scale = Math.min(1, 2048 / Math.max(sourceWidth, sourceHeight));
    setWidth(Math.max(256, Math.round(sourceWidth * scale / 32) * 32));
    setHeight(Math.max(256, Math.round(sourceHeight * scale / 32) * 32));
  }

  function selectMode(nextMode: string) {
    if ((nextMode === 'RGBA' || mode === 'RGBA') && nextMode !== mode) {
      setMaskMedia(null);
      setSavedMask(false);
    }
    setMode(nextMode);
    if (nextMode === 'WARDROBE') {
      setReferences((rows) => rows.map((row, index) => (
        index === 0 && row.role === 'canvas' ? { ...row, role: 'subject' } : row
      )));
    }
  }

  useEffect(() => {
    try {
      const ids = JSON.parse(window.localStorage.getItem(taskStorageKey) ?? '[]') as Array<{ id: string; kind: 'image' | 'voice' }>;
      Promise.all(ids.filter((row) => row.kind === 'image').slice(0, 8).map((row) => getLibraryTask(row.id).catch(() => null)))
        .then((rows) => setTasks(rows.filter((row): row is LibraryTask => row !== null)));
    } catch { /* task IDs are a convenience; server rows remain authoritative */ }
  }, []);
  useEffect(() => {
    if (!tasks.length) return;
    const interval = window.setInterval(() => {
      Promise.all(tasks.map((task) => getLibraryTask(task.id).catch(() => task))).then(setTasks);
    }, 2500);
    return () => window.clearInterval(interval);
  }, [tasks.map((task) => task.id).join('|')]);
  useEffect(() => {
    window.localStorage.setItem(draftStorageKey, JSON.stringify({
      mode, title, prompt, category, width, height, candidateCount, transparent, references, maskMedia, targetAssetId,
    }));
  }, [mode, title, prompt, category, width, height, candidateCount, transparent, references, maskMedia, targetAssetId]);
  useEffect(() => {
    const pendingCanvas = (mode === 'INPAINT' || mode === 'RGBA') && references.length > 0 && !maskMedia;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (pendingCanvas) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [mode, references.length, maskMedia]);

  function saveTaskIds(next: LibraryTask[]) {
    const prior = (() => { try { return JSON.parse(window.localStorage.getItem(taskStorageKey) ?? '[]'); } catch { return []; } })();
    const merged = [...next.map((task) => ({ id: task.id, kind: 'image' as const })), ...prior.filter((item: any) => !next.some((task) => task.id === item.id))].slice(0, 8);
    window.localStorage.setItem(taskStorageKey, JSON.stringify(merged));
  }

  function selectTargetAsset(assetId: string) {
    setTargetAssetId(assetId);
    const asset = reusableAssets.find((row) => row.id === assetId);
    const version = asset?.current_version;
    if (!asset || !version) return;
    if (references.length >= 10 && !references.some((row) => row.version_id === version.id)) {
      setError('参考图最多 10 张；请先移除一张再选择已有素材。');
      return;
    }
    setTitle(asset.title);
    setCategory(asset.category);
    if (version.width && version.height) fitEditSize(version.width, version.height);
    setMode('I2I');
    setReferences((rows) => {
      const prior = rows.filter((row) => row.version_id !== version.id);
      const selected: ReferenceMedia = {
        id: `version:${version.id}`, version_id: version.id, filename: version.filename,
        sha256: version.content_sha256, width: version.width, height: version.height,
        media_url: version.media_url, rights_id: version.rights?.id ?? '', role: 'canvas', order: 0,
      };
      return [selected, ...prior].map((row, order) => ({ ...row, order }));
    });
  }

  async function uploadFile(file: File, purpose: 'reference' | 'mask') {
    setBusy(true); setError(''); setNotice('');
    try {
      const media = await uploadLibraryMedia(file, purpose);
      const image: ReferenceMedia = { id: media.id, media_id: media.id, filename: media.filename, sha256: media.sha256,
        width: media.width, height: media.height, media_url: media.media_url, rights_id: media.rights_id,
        role: purpose === 'mask' ? 'mask' : references.length ? 'garment' : 'canvas', order: purpose === 'mask' ? 0 : references.length };
      if (purpose === 'mask') { setMaskMedia(image); setSavedMask(true); setNotice('遮罩已上传并校验尺寸；正式图工作流仍需本机 mask 能力验证。'); }
      else {
        if (references.length === 0 && image.width && image.height) {
          if (mode === 'INPAINT' || mode === 'RGBA') {
            setWidth(image.width); setHeight(image.height);
          } else {
            fitEditSize(image.width, image.height);
          }
        }
        setReferences((rows) => [...rows, { ...image, order: rows.length }]); setNotice('参考图已导入本机素材库。');
      }
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  function reorder(index: number, direction: -1 | 1) {
    setReferences((rows) => {
      const copy = [...rows]; const target = index + direction;
      if (target < 0 || target >= copy.length) return rows;
      [copy[index], copy[target]] = [copy[target], copy[index]];
      if (index === 0 || target === 0) setMaskMedia(null);
      if (index === 0 || target === 0) setSavedMask(false);
      return copy.map((row, order) => ({ ...row, order }));
    });
  }

  async function exportMask(blob: Blob) {
    const file = new File([blob], 'shine-mask.png', { type: 'image/png' });
    await uploadFile(file, 'mask');
  }

  async function submit(validationOnly = false) {
    setBusy(true); setError(''); setNotice('');
    let intent: PendingIntent | null = null;
    try {
      const countNeedsCanvas = ['I2I', 'INPAINT', 'MULTIREF', 'WARDROBE'].includes(mode);
      if (mode === 'T2I' && references.length) throw new Error('文生图不会使用参考图；请移除参考图，或切换到图生图。');
      if (countNeedsCanvas && !references.length) throw new Error('请先导入底图或参考图片。');
      if (mode === 'INPAINT' && !maskMedia) throw new Error('请在画布涂出修改区域并上传真实 PNG 遮罩。');
      const manualCutout = mode === 'RGBA' && references.length === 1 && Boolean(maskMedia);
      if (mode === 'RGBA' && maskMedia && references.length !== 1) throw new Error('手动画布去背景需要且只需要一张 PNG 底图。');
      if (mode === 'RGBA' && manualCutout && !references[0].filename.toLowerCase().endsWith('.png')) throw new Error('手动画布去背景需要 PNG 底图；请导入 PNG 或移除当前遮罩。');
      if (mode === 'RGBA' && !manualCutout && !prompt.trim()) throw new Error('请输入透明图片生成或编辑指令，或导入一张 PNG 并绘制去背景遮罩。');
      const body = {
        title: title.trim(), mode, prompt, category,
        references: references.map(({ id, media_id, version_id, order, role }) => ({ media_id: media_id ?? (version_id ? undefined : id), version_id, order, role })),
        mask_media_id: mode === 'INPAINT' || manualCutout ? maskMedia?.id : null,
        output: { width, height }, seed: null, candidate_count: validationOnly ? 1 : candidateCount, validation_only: validationOnly,
        transparent: transparent || mode === 'RGBA',
        target_asset_id: targetAssetId || null,
        parent_version_id: reusableAssets.find((asset) => asset.id === targetAssetId)?.current_version?.id ?? null,
        expected_asset_state_version: reusableAssets.find((asset) => asset.id === targetAssetId)?.state_version ?? null,
      };
      intent = preparePendingIntent(pendingIntentStorageKey, body);
      setPendingIntent(intent);
      const response = await createImageJobs({ ...body, idempotency_key: intent.idempotency_key });
      setTasks((rows) => [...response.items, ...rows].slice(0, 8));
      saveTaskIds(response.items);
      clearPendingIntent(pendingIntentStorageKey, intent, setPendingIntent);
      setNotice(response.notice);
    } catch (reason) {
      if (intent && isDefinitiveRejection(reason)) clearPendingIntent(pendingIntentStorageKey, intent, setPendingIntent);
      setError(errorText(reason));
    }
    finally { setBusy(false); }
  }

  async function recoverPendingIntent() {
    if (!pendingIntent || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await createImageJobs({
        ...pendingIntent.body, idempotency_key: pendingIntent.idempotency_key,
      });
      setTasks((rows) => [...response.items, ...rows].slice(0, 8));
      saveTaskIds(response.items);
      clearPendingIntent(pendingIntentStorageKey, pendingIntent, setPendingIntent);
      setNotice('已按同一幂等请求恢复任务；不会重复创建生成任务。');
    } catch (reason) {
      if (isDefinitiveRejection(reason)) clearPendingIntent(pendingIntentStorageKey, pendingIntent, setPendingIntent);
      setError(errorText(reason));
    }
    finally { setBusy(false); }
  }

  const requiresReferences = ['I2I', 'INPAINT', 'MULTIREF', 'WARDROBE'].includes(mode);
  const maxReferenceCount = mode === 'INPAINT' ? 9 : 10;
  const manualCutoutReady = mode === 'RGBA' && references.length === 1 && Boolean(maskMedia)
    && references[0].filename.toLowerCase().endsWith('.png')
    && references[0].width === width && references[0].height === height;
  const canGenerate = Boolean(selectedCapability?.can_generate || manualCutoutReady);
  const canValidate = Boolean(selectedCapability?.can_validate);
  return <div className="asset-workshop-layout">
    <section className="asset-card asset-workshop-form">
      <div className="asset-card-heading"><div><div className="asset-eyebrow">图片工坊</div><h2>创作一组可审核的图片候选</h2><p>原图和参考图会保留来源，生成任务刷新后仍可查看。</p></div><span className="asset-model-chip">Qwen-Image-2.1</span></div>
      <div className="asset-mode-grid" role="group" aria-label="图片创作方式">
        {[
          ['T2I', '文生图'], ['I2I', '图生图'], ['INPAINT', '局部修订'],
          ['MULTIREF', '多图合成'], ['WARDROBE', '人物穿戴'], ['RGBA', '透明背景'],
        ].map(([value, label]) => <button key={value} type="button" className={mode === value ? 'active' : ''} onClick={() => selectMode(value)}>
          {label}{value === 'INPAINT' && <span className="pending-dot">待本机验证</span>}
        </button>)}
      </div>
      <label className="asset-field"><span>{mode === 'T2I' ? '画面描述' : '修改指令'}</span>
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={4} placeholder={mode === 'T2I' ? '例如：雨后的老街，暖黄色窗光，电影静帧构图……' : '说明要改变的内容，以及希望保持不变的部分……'} />
      </label>
      <label className="asset-field"><span>素材名称（可选）</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="留空时按分类命名" /></label>
      <label className="asset-field"><span>从已有素材继续并保留版本历史（可选）</span>
        <select value={targetAssetId} onChange={(event) => selectTargetAsset(event.target.value)}>
          <option value="">新建一项全局素材</option>
          {reusableAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.title} · V{asset.current_version?.ordinal}</option>)}
        </select>
        {targetAssetId && <small>新候选会关联到所选版本；人工晋升前不会替换正式版本。</small>}
      </label>
      <div className="asset-field-row">
        <label className="asset-field"><span>素材分类</span><select value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="character">人物</option><option value="scene">场景</option><option value="prop">道具</option><option value="costume">服装</option><option value="other">其他</option>
        </select></label>
        <label className="asset-field"><span>候选数量</span><select value={candidateCount} onChange={(event) => setCandidateCount(Number(event.target.value))}>
          {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} 张，依次排队</option>)}
        </select></label>
      </div>
      <div className="asset-field-row asset-size-row">
        <label className="asset-field"><span>宽度（像素）</span><input type="number" min={256} max={2048} step={32} value={width} onChange={(event) => setWidth(Number(event.target.value))} /></label>
        <label className="asset-field"><span>高度（像素）</span><input type="number" min={256} max={2048} step={32} value={height} onChange={(event) => setHeight(Number(event.target.value))} /></label>
        <label className="asset-checkbox"><input type="checkbox" checked={transparent} onChange={(event) => setTransparent(event.target.checked)} /><span>需要透明背景</span></label>
      </div>
      {(requiresReferences || mode === 'RGBA' || references.length > 0) && <div className="asset-reference-block">
        <div className="asset-reference-heading"><div><b>底图和参考图</b><span>{mode === 'INPAINT' ? '局部修订还会把遮罩作为一张输入图；底图与参考图最多 9 张。' : '最多 10 张；第 1 张是编辑底图。'}</span></div>
          <button type="button" className="asset-secondary-button" disabled={busy || references.length >= maxReferenceCount} onClick={() => { fileInput.current?.click(); }}><Upload size={15} />导入图片</button>
          <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadFile(file, 'reference'); event.target.value = ''; }} />
        </div>
        {references.length > 0 && <div className="asset-reference-list">{references.map((row, index) => <div key={row.id} className="asset-reference-row">
          <img src={row.media_url} alt="" /><div className="asset-reference-name"><b>第 {index + 1} 张 · {row.filename}</b><small>{row.width} × {row.height} px · {row.sha256.slice(0, 10)}</small></div>
          <select aria-label={`第 ${index + 1} 张图片用途`} value={row.role} onChange={(event) => setReferences((rows) => rows.map((item) => item.id === row.id ? { ...item, role: event.target.value } : item))}>
            {Object.entries(REFERENCE_ROLE_LABELS).map(([role, roleLabel]) => <option key={role} value={role}>{roleLabel}</option>)}
          </select>
          <button aria-label="上移参考图" disabled={index === 0} onClick={() => reorder(index, -1)}>↑</button><button aria-label="下移参考图" disabled={index === references.length - 1} onClick={() => reorder(index, 1)}>↓</button>
          <button aria-label="移除参考图" onClick={() => { if (index === 0) { setMaskMedia(null); setSavedMask(false); } setReferences((rows) => rows.filter((item) => item.id !== row.id).map((item, order) => ({ ...item, order }))); }}><X size={14} /></button>
        </div>)}</div>}
        {references.length === 0 && <div className="asset-drop-area" onClick={() => fileInput.current?.click()}><ImagePlus size={21} /><span>导入本机图片</span><small>PNG、JPG、WebP</small></div>}
      </div>}
      {mode === 'T2I' && references.length > 0 && <div className="asset-capability-notice pending" role="status"><span><i />文生图不会使用参考图</span><p>移除这些参考图后继续，或切换到图生图以使用它们。</p></div>}
      {(mode === 'INPAINT' || mode === 'RGBA') && references[0] && <MaskCanvas key={`${mode}:${references[0].id}`} source={references[0]} existingMask={maskMedia} savedMask={savedMask} busy={busy} semantics={mode === 'RGBA' ? 'keep' : 'edit'} onSave={exportMask} onDirty={() => { setMaskMedia(null); setSavedMask(false); }} />}
      {(mode === 'INPAINT' || (mode === 'RGBA' && manualCutoutReady)) && maskMedia && <div className="asset-mask-saved"><Check size={15} />真实遮罩已保存：{maskMedia.width} × {maskMedia.height} 像素</div>}
      {pendingIntent && <div className="asset-capability-notice pending" role="status">
        <span><i />有一项图片请求尚未收到确认</span>
        <p>刷新或断网后可以用原请求编号核对并恢复；恢复不会再次提交同一任务。</p>
        <button type="button" className="asset-secondary-button" disabled={busy} onClick={() => void recoverPendingIntent()}>核对并恢复上次请求</button>
      </div>}
      {manualCutoutReady ? <div className="asset-capability-notice ready"><span><i />遮罩去背景可用</span><p>白色区域保留主体，黑色区域生成透明像素；这是确定性本机像素处理，不依赖或冒充 Qwen 分割模型。</p></div>
        : selectedCapability && <div className={`asset-capability-notice ${selectedCapability.can_generate ? 'ready' : 'pending'}`}>
          <span><i />{capabilityLabel(selectedCapability)}</span><p>{selectedCapability.reason}</p>
        </div>}
      {canValidate && !canGenerate && <div className="asset-validation-note">本机验证会真实提交一张样例到当前 ComfyUI，并生成待审核候选；不会自动晋升或开放正式生成。</div>}
      {error && <div className="asset-inline-error" role="alert">{error}</div>}
      {notice && <div className="asset-success-note" role="status"><Check size={15} />{notice}</div>}
      <button className="asset-primary-button" disabled={busy || (mode === 'T2I' && references.length > 0) || (!canGenerate && !canValidate) || (mode !== 'RGBA' && !prompt.trim()) || (mode === 'RGBA' && !manualCutoutReady && !prompt.trim())} onClick={() => void submit(!manualCutoutReady && !canGenerate && canValidate)}>
        {busy ? <><LoaderCircle className="asset-spin" size={16} />正在保存请求…</> : manualCutoutReady ? <><Eraser size={16} />生成透明 PNG 候选</> : canGenerate ? <><WandSparkles size={16} />生成图片候选</> : canValidate ? <><ShieldCheck size={16} />运行一次本机验证样例</> : <><ShieldCheck size={16} />等待本机能力验证</>}
      </button>
    </section>
    <aside className="asset-workshop-side">
      <div className="asset-card"><div className="asset-card-heading"><div><h2>任务记录</h2><p>页面不会自动重提任何任务。</p></div><RefreshCw size={15} /></div>
        {tasks.length ? <div className="asset-task-list">{tasks.map((task) => <TaskRow key={task.id} task={task} onLibrary={() => onNavigate('library')} />)}</div>
          : <div className="asset-empty-small">保存任务后，状态会出现在这里。</div>}
      </div>
    </aside>
  </div>;
}

function MaskCanvas({ source, existingMask, savedMask, busy, semantics, onSave, onDirty }: {
  source: ReferenceMedia; existingMask: ReferenceMedia | null; savedMask: boolean; busy: boolean; onSave: (blob: Blob) => Promise<void>; onDirty: () => void;
  semantics: 'edit' | 'keep';
}) {
  const imageRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const undoRef = useRef<ImageData[]>([]);
  const redoRef = useRef<ImageData[]>([]);
  const drawingRef = useRef(false);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [maskLoading, setMaskLoading] = useState(Boolean(existingMask));
  const [brushSize, setBrushSize] = useState(32);
  const [erase, setErase] = useState(false);
  const [error, setError] = useState('');

  function snapshot() {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    undoRef.current.push(context.getImageData(0, 0, canvas.width, canvas.height));
    if (undoRef.current.length > 20) undoRef.current.shift();
    redoRef.current = [];
  }

  function initialize() {
    const image = imageRef.current;
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!image || !canvas || !context) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    context.fillStyle = '#000';
    context.fillRect(0, 0, canvas.width, canvas.height);
    undoRef.current = [context.getImageData(0, 0, canvas.width, canvas.height)];
    redoRef.current = [];
    setDimensions({ width: canvas.width, height: canvas.height });
    if (existingMask) {
      const saved = new Image();
      saved.onload = () => {
        setMaskLoading(false);
        if (saved.naturalWidth !== canvas.width || saved.naturalHeight !== canvas.height) {
          setError('已保存遮罩与当前底图尺寸不同，请重新绘制并保存。');
          onDirty();
          return;
        }
        context.drawImage(saved, 0, 0);
        undoRef.current = [context.getImageData(0, 0, canvas.width, canvas.height)];
      };
      saved.onerror = () => { setMaskLoading(false); setError('无法读取已保存遮罩，请重新绘制。'); onDirty(); };
      saved.src = existingMask.media_url;
    } else {
      onDirty();
    }
  }

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * canvas.width / rect.width,
      y: (event.clientY - rect.top) * canvas.height / rect.height };
  }

  function begin(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!dimensions || busy || maskLoading) return;
    const canvas = canvasRef.current!;
    canvas.setPointerCapture(event.pointerId);
    snapshot(); onDirty(); drawingRef.current = true;
    const position = point(event); lastPointRef.current = position;
    const ctx = canvas.getContext('2d')!;
    ctx.beginPath(); ctx.fillStyle = erase ? '#000' : '#fff';
    ctx.arc(position.x, position.y, brushSize * canvas.width / canvas.getBoundingClientRect().width / 2, 0, Math.PI * 2); ctx.fill();
  }

  function move(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    const canvas = canvasRef.current!; const ctx = canvas.getContext('2d')!;
    const current = point(event); const previous = lastPointRef.current;
    if (!previous) return;
    ctx.beginPath(); ctx.moveTo(previous.x, previous.y); ctx.lineTo(current.x, current.y);
    ctx.strokeStyle = erase ? '#000' : '#fff'; ctx.lineWidth = brushSize * canvas.width / canvas.getBoundingClientRect().width;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.stroke(); lastPointRef.current = current;
  }

  function end() { drawingRef.current = false; lastPointRef.current = null; }
  function restore(stack: ImageData[], destination: ImageData[]) {
    const canvas = canvasRef.current; const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || stack.length < 1) return;
    destination.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    ctx.putImageData(stack.pop()!, 0, 0); onDirty();
  }
  function clear() {
    const canvas = canvasRef.current; const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    snapshot(); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height); onDirty();
  }
  async function save() {
    setError('');
    const canvas = canvasRef.current; const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < data.data.length; i += 4) {
      const white = data.data[i] >= 128;
      data.data[i] = data.data[i + 1] = data.data[i + 2] = white ? 255 : 0;
      data.data[i + 3] = 255;
    }
    const output = document.createElement('canvas'); output.width = canvas.width; output.height = canvas.height;
    output.getContext('2d')!.putImageData(data, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => output.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('无法导出遮罩，请重试。');
    try { await onSave(blob); }
    catch (reason) { setError(errorText(reason)); }
  }

  return <div className="asset-mask-editor">
    <div className="asset-mask-heading"><div><b>{semantics === 'edit' ? '局部修订画布' : '背景移除画布'}</b><span>{semantics === 'edit' ? '白色表示要改，黑色表示保留；遮罩按原图像素导出。' : '白色保留主体，黑色移除背景；按原图像素生成真实透明通道。'}</span></div><span>{dimensions ? `${dimensions.width} × ${dimensions.height}` : '读取底图…'}</span></div>
    <div className="asset-mask-toolbar">
      <button type="button" className={!erase ? 'selected' : ''} onClick={() => setErase(false)}><Brush size={14} />画笔</button>
      <button type="button" className={erase ? 'selected' : ''} onClick={() => setErase(true)}><Eraser size={14} />橡皮</button>
      <label>笔刷大小<input type="range" min={4} max={120} value={brushSize} onChange={(event) => setBrushSize(Number(event.target.value))} /></label>
      <button type="button" onClick={() => restore(undoRef.current, redoRef.current)} title="撤销"><RotateCcw size={14} /></button>
      <button type="button" onClick={() => restore(redoRef.current, undoRef.current)} title="重做"><RotateCw size={14} /></button>
      <button type="button" onClick={clear} title="清空遮罩"><X size={14} /></button>
    </div>
    <div className="asset-mask-preview" style={dimensions ? {
      aspectRatio: `${dimensions.width} / ${dimensions.height}`,
      maxWidth: `${420 * dimensions.width / dimensions.height}px`,
    } : undefined}>
      <img ref={imageRef} src={source.media_url} alt="局部修订底图" onLoad={initialize} />
      <canvas ref={canvasRef} aria-label="绘制局部修改区域，白色是修改区域" onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={end} />
    </div>
    {error && <div className="asset-inline-error" role="alert">{error}</div>}
    <div className="asset-mask-actions"><span>{savedMask ? '遮罩已保存' : '遮罩尚未保存'}</span><button type="button" className="asset-secondary-button" disabled={!dimensions || busy} onClick={() => void save()}><Check size={14} />导出并保存遮罩</button></div>
  </div>;
}

function VoiceWorkshop({ capabilities, onNavigate }: { capabilities: CapabilityReport | null; onNavigate: (page: AssetCenterPage) => void }) {
  const draftStorageKey = 'shine-assets-voice-draft-v1';
  const restoredDraft = storedObject(draftStorageKey);
  const pendingIntentStorageKey = 'shine-assets-pending-voice-intent-v1';
  const [category, setCategory] = useState(typeof restoredDraft.category === 'string' ? restoredDraft.category : 'dialogue');
  const [file, setFile] = useState<File | null>(null);
  const [sampleTranscript, setSampleTranscript] = useState(typeof restoredDraft.sampleTranscript === 'string' ? restoredDraft.sampleTranscript : '');
  const [title, setTitle] = useState(typeof restoredDraft.title === 'string' ? restoredDraft.title : '');
  const [mode, setMode] = useState<'DESIGN' | 'AUTHORIZED_CLONE' | 'TTS'>(
    ['DESIGN', 'AUTHORIZED_CLONE', 'TTS'].includes(restoredDraft.mode) ? restoredDraft.mode : 'DESIGN',
  );
  const [character, setCharacter] = useState(typeof restoredDraft.character === 'string' ? restoredDraft.character : '');
  const [description, setDescription] = useState(typeof restoredDraft.description === 'string' ? restoredDraft.description : '');
  const [text, setText] = useState(typeof restoredDraft.text === 'string' ? restoredDraft.text : '你好，欢迎来到我的故事。');
  const [sample, setSample] = useState<Record<string, any> | null>(restoredDraft.sample ?? null);
  const [sampleOptions, setSampleOptions] = useState<Array<Record<string, any>>>([]);
  const [voices, setVoices] = useState<Array<Record<string, any>>>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState(typeof restoredDraft.selectedVoiceId === 'string' ? restoredDraft.selectedVoiceId : '');
  const [heardVoiceId, setHeardVoiceId] = useState('');
  const [tasks, setTasks] = useState<LibraryTask[]>([]);
  const [pendingIntent, setPendingIntent] = useState<PendingIntent | null>(() => readPendingIntent(pendingIntentStorageKey));
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const { assets, refresh } = useFreshList();
  useEffect(() => {
    window.localStorage.setItem(draftStorageKey, JSON.stringify({
      category, sampleTranscript, title,
      mode, character, description, text, sample, selectedVoiceId,
    }));
  }, [category, sampleTranscript, title, mode, character, description, text, sample, selectedVoiceId]);
  const cap = mode === 'DESIGN' ? capabilities?.voice_capabilities.VOICE_DESIGN : mode === 'AUTHORIZED_CLONE'
    ? capabilities?.voice_capabilities.VOICE_CLONE : capabilities?.voice_capabilities.TTS_MANDARIN;
  useEffect(() => {
    listLibraryVoices().then((row) => {
      setVoices(row.items);
      setSelectedVoiceId((current) => current || row.items.find((voice) => voice.status === 'FROZEN')?.id || '');
    }).catch(() => setVoices([]));
  }, []);
  useEffect(() => {
    try {
      const ids = JSON.parse(window.localStorage.getItem('shine-assets-recent-tasks') ?? '[]') as Array<{ id: string; kind: 'image' | 'voice' }>;
      Promise.all(ids.filter((row) => row.kind === 'voice').slice(0, 8).map((row) => getLibraryTask(row.id, 'voice').catch(() => null)))
        .then((rows) => setTasks(rows.filter((row): row is LibraryTask => row !== null)));
    } catch { /* persisted IDs are a convenience; the server task remains authoritative */ }
  }, []);
  useEffect(() => {
    if (!tasks.length) return;
    const interval = window.setInterval(() => {
      Promise.all(tasks.map((task) => getLibraryTask(task.id, 'voice').catch(() => task))).then(setTasks);
    }, 2500);
    return () => window.clearInterval(interval);
  }, [tasks.map((task) => task.id).join('|')]);
  const importedAudio = assets.filter((asset) => asset.kind === 'audio');
  useEffect(() => {
    let alive = true;
    Promise.all(importedAudio.map((asset) => getLibraryAsset(asset.id).catch(() => null))).then((rows) => {
      if (!alive) return;
      const choices = rows.filter((row): row is LibraryAsset => row !== null).flatMap((asset) =>
        (asset.versions ?? []).filter((version) => version.kind === 'audio' && (version.provenance as any).media_purpose === 'voice_sample')
          .map((version) => ({ id: (version.provenance as any).source_media_id, rights_id: version.rights?.id,
            title: asset.title, filename: version.filename, transcript: version.prompt, sha256: version.content_sha256, media_url: version.media_url,
            rights_status: version.rights?.status, expires_at: version.rights?.expires_at })),
      ).filter((item) => item.id && item.rights_status !== 'REVOKED');
      setSampleOptions(choices);
    }).catch(() => { if (alive) setSampleOptions([]); });
    return () => { alive = false; };
  }, [importedAudio.map((asset) => asset.id).join('|')]);

  async function importAudio() {
    if (!file || !title.trim()) { setError('请选择音频并填写素材名称。'); return; }
    if (category === 'voice' && !sampleTranscript.trim()) { setError('请填写录音中的实际对白原文。'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      const media = await uploadLibraryMedia(file, category === 'voice' ? 'voice_sample' : 'audio_import');
      await createLibraryAsset({
        title: title.trim(), kind: 'audio', category, media_id: media.id,
        source_mode: category === 'voice' ? 'AUTHORIZED_CLONE' : 'UPLOAD',
        prompt: category === 'voice' ? sampleTranscript.trim() : '',
      });
      if (category === 'voice') {
        setSample({ id: media.id, title: title.trim(), filename: file.name });
        setText(sampleTranscript.trim());
      }
      setFile(null); setTitle(''); setSampleTranscript(''); refresh(); setNotice('音频已导入并保存到全局素材库。');
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function submitVoice() {
    setBusy(true); setError(''); setNotice('');
    let intent: PendingIntent | null = null;
    try {
      const body = {
        mode, title: title || `${character || '新音色'} 预览`, character: character || '新音色',
        description, text, reference_text: mode === 'AUTHORIZED_CLONE' ? text : '',
        reference_media_id: mode === 'AUTHORIZED_CLONE' ? sample?.id : null,
        voice_profile_id: mode === 'TTS' ? selectedVoiceId : null,
        speed: 1, pause: 0, emotion: 'neutral',
      };
      intent = preparePendingIntent(pendingIntentStorageKey, body);
      setPendingIntent(intent);
      const response = await createVoiceJob({ ...body, idempotency_key: intent.idempotency_key });
      const next = [response.item, ...tasks].slice(0, 8); setTasks(next);
      const prior = (() => { try { return JSON.parse(window.localStorage.getItem('shine-assets-recent-tasks') ?? '[]'); } catch { return []; } })();
      window.localStorage.setItem('shine-assets-recent-tasks', JSON.stringify([{ id: response.item.id, kind: 'voice' }, ...prior].slice(0, 8)));
      clearPendingIntent(pendingIntentStorageKey, intent, setPendingIntent);
      setNotice(response.notice);
    } catch (reason) {
      if (intent && isDefinitiveRejection(reason)) clearPendingIntent(pendingIntentStorageKey, intent, setPendingIntent);
      setError(errorText(reason));
    }
    finally { setBusy(false); }
  }

  async function recoverPendingIntent() {
    if (!pendingIntent || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await createVoiceJob({
        ...pendingIntent.body, idempotency_key: pendingIntent.idempotency_key,
      });
      setTasks((rows) => [response.item, ...rows.filter((row) => row.id !== response.item.id)].slice(0, 8));
      const prior = (() => { try { return JSON.parse(window.localStorage.getItem('shine-assets-recent-tasks') ?? '[]'); } catch { return []; } })();
      window.localStorage.setItem('shine-assets-recent-tasks', JSON.stringify([
        { id: response.item.id, kind: 'voice' }, ...prior.filter((row: any) => row.id !== response.item.id),
      ].slice(0, 8)));
      clearPendingIntent(pendingIntentStorageKey, pendingIntent, setPendingIntent);
      setNotice('已按同一幂等请求恢复声音任务；不会重复创建任务。');
    } catch (reason) {
      if (isDefinitiveRejection(reason)) clearPendingIntent(pendingIntentStorageKey, pendingIntent, setPendingIntent);
      setError(errorText(reason));
    }
    finally { setBusy(false); }
  }

  async function freezeVoice(voice: Record<string, any>) {
    if (heardVoiceId !== voice.id) { setError('请先播放并试听该音色，再进行人工冻结。'); return; }
    setBusy(true); setError('');
    try {
      const grant = await createVoiceFreezeGrant(voice.id);
      await freezeLibraryVoice(voice.id, { ...grant, listened: true, evidence: '已试听此音色并确认可用。' });
      setVoices((rows) => rows.map((item) => item.id === voice.id ? { ...item, status: 'FROZEN' } : item));
      setHeardVoiceId('');
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  return <div className="asset-voice-layout">
    <div className="asset-card asset-voice-form">
      <div className="asset-card-heading"><div><div className="asset-eyebrow">声音工坊</div><h2>准备可复用的声音素材</h2><p>试听与音色冻结由人工完成；音效和配乐只提供导入管理。</p></div><Mic2 size={22} /></div>
      <div className="asset-mode-grid voice-mode-grid">
        {([['DESIGN', '音色设计'], ['AUTHORIZED_CLONE', '声音克隆'], ['TTS', '普通话对白']] as const).map(([value, label]) => <button key={value} className={mode === value ? 'active' : ''} onClick={() => setMode(value)}>{label}</button>)}
      </div>
      <div className="asset-capability-notice pending"><span><i />{cap ? capabilityLabel(cap) : '读取中'}</span><p>{cap?.reason ?? '正在读取声音能力。'}</p></div>
      {mode !== 'TTS' && <label className="asset-field"><span>{mode === 'DESIGN' ? '声音特征描述' : '角色或说话人'}</span><input value={mode === 'DESIGN' ? description : character} onChange={(event) => mode === 'DESIGN' ? setDescription(event.target.value) : setCharacter(event.target.value)} placeholder={mode === 'DESIGN' ? '例如：温和、清晰、略带沙哑的中年男声' : '例如：林岚'} /></label>}
      {mode === 'AUTHORIZED_CLONE' && <label className="asset-field"><span>选择声音样本</span><select value={sample?.id ?? ''} onChange={(event) => {
        const selected = sampleOptions.find((item) => item.id === event.target.value);
        setSample(selected ?? null);
        setText(selected?.transcript ?? '');
      }}>
        <option value="">选择一段已导入的录音</option>{sampleOptions.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.filename}</option>)}
      </select></label>}
      {mode === 'TTS' && <label className="asset-field"><span>已冻结音色</span><select value={selectedVoiceId} onChange={(event) => setSelectedVoiceId(event.target.value)} disabled={!voices.some((voice) => voice.status === 'FROZEN')}>
        {voices.filter((voice) => voice.status === 'FROZEN').map((voice) => <option key={voice.id} value={voice.id}>{voice.title}</option>)}
        {!voices.some((voice) => voice.status === 'FROZEN') && <option>请先完成人工试听与冻结</option>}
      </select></label>}
      <label className="asset-field"><span>{mode === 'TTS' ? '普通话对白' : mode === 'AUTHORIZED_CLONE' ? '录音中的对白原文' : '试听文本'}</span><textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} /></label>
      {mode !== 'TTS' && <label className="asset-field"><span>音色名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：林岚·旁白音色" /></label>}
      <div className="asset-card asset-audio-import">
        <div><b>导入音频素材</b><p>对白、音效、配乐和声音样本均可分类、试听和跨项目管理。</p></div>
        <label className="asset-field"><span>分类</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="dialogue">对白</option><option value="voice">音色 / 参考样本</option><option value="sfx">音效</option><option value="music">音乐</option></select></label>
        <label className="asset-field"><span>素材名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="音频名称" /></label>
        {category === 'voice' && <label className="asset-field"><span>录音中的对白原文</span><textarea value={sampleTranscript} onChange={(event) => setSampleTranscript(event.target.value)} rows={3} placeholder="逐字填写样本中实际说出的内容" /></label>}
        <div className="asset-upload-line"><input type="file" accept={category === 'voice' ? 'audio/wav' : 'audio/wav,audio/mpeg,audio/flac'} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button className="asset-secondary-button" disabled={busy || !file || !title.trim()} onClick={() => void importAudio()}><Upload size={14} />导入音频</button></div>
        {category === 'voice' && <small>请使用单声道 16-bit PCM WAV，并填写样本中的对白原文。</small>}
      </div>
      {error && <div className="asset-inline-error" role="alert">{error}</div>}{notice && <div className="asset-success-note" role="status"><Check size={15} />{notice}</div>}
      {pendingIntent && <div className="asset-capability-notice pending" role="status">
        <span><i />有一项声音请求尚未收到确认</span>
        <p>刷新或断网后可用原请求编号核对并恢复；恢复不会重复创建任务。</p>
        <button type="button" className="asset-secondary-button" disabled={busy} onClick={() => void recoverPendingIntent()}>核对并恢复上次请求</button>
      </div>}
      <button className="asset-primary-button" disabled={busy || cap?.can_queue === false || !text.trim() || (mode === 'AUTHORIZED_CLONE' && !sample)} onClick={() => void submitVoice()}><Mic2 size={16} />保存声音任务</button>
    </div>
    <aside className="asset-workshop-side">
      <div className="asset-card"><div className="asset-card-heading"><div><h2>我的音色</h2><p>冻结前必须实际试听并确认。</p></div><AudioLines size={16} /></div>
        {voices.length ? <div className="asset-task-list">{voices.map((voice) => <div key={voice.id} className="asset-voice-row"><div><b>{voice.title}</b><small>{LABELS[voice.status] ?? voice.status} · {voice.voice_hash.slice(0, 10)}</small></div><audio controls src={voice.media_url} onPlay={() => setHeardVoiceId(voice.id)} />{voice.status === 'PREVIEW' && <button className="asset-text-button" disabled={busy || heardVoiceId !== voice.id} onClick={() => void freezeVoice(voice)}>试听后冻结</button>}</div>)}</div> : <div className="asset-empty-small">还没有全局音色。</div>}
      </div>
      <div className="asset-card"><h2>最近声音任务</h2>{tasks.length ? <div className="asset-task-list">{tasks.map((task) => <TaskRow key={task.id} task={task} onLibrary={() => onNavigate('library')} />)}</div> : <div className="asset-empty-small">声音任务会保留在这里，等待本机服务验证。</div>}</div>
    </aside>
  </div>;
}

function LibraryPage() {
  const [q, setQ] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [selected, setSelected] = useState<LibraryAsset | null>(null);
  const [loading, setLoading] = useState(true);
  const [editTitle, setEditTitle] = useState('');
  const [editCategory, setEditCategory] = useState('other');
  const [editTags, setEditTags] = useState('');
  const [editCollection, setEditCollection] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const imageInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    listLibraryAssets(q, categoryFilter).then((result) => { if (alive) { setAssets(result.items); setError(''); } })
      .catch((reason) => { if (alive) setError(errorText(reason)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [q, categoryFilter, refreshKey]);

  useEffect(() => {
    setEditTitle(selected?.title ?? '');
    setEditCategory(selected?.category ?? 'other');
    setEditTags((selected?.tags ?? []).join(', '));
    setEditCollection(selected?.collection ?? '');
  }, [selected?.id, selected?.state_version]);

  async function importImage(file: File) {
    setBusy(true); setError('');
    try {
      const media = await uploadLibraryMedia(file, 'source');
      const asset = await createLibraryAsset({
        title: file.name.replace(/\.[^.]+$/, '') || file.name, kind: 'image', category: 'other', media_id: media.id,
      });
      setSelected(asset);
      setQ('');
      setCategoryFilter('');
      setRefreshKey((n) => n + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function reviewVersion(version: LibraryVersion) {
    setBusy(true); setError('');
    try {
      let current = version;
      if (current.status === 'CANDIDATE' || current.status === 'QC_FAILED') current = await qcLibraryVersion(current.id);
      if (current.status !== 'READY_FOR_REVIEW') throw new Error(`该版本状态为${current.status}，不能晋升。`);
      const grant = await grantVersionPromotion(current.id);
      await promoteLibraryVersion(current.id, { ...grant, evidence: '已检查内容和技术质量，确认晋升为全局正式版本。' });
      setRefreshKey((n) => n + 1);
      if (selected) setSelected(await getLibraryAsset(selected.id));
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function toggleFavorite(asset: LibraryAsset) {
    setBusy(true); setError('');
    try {
      const updated = await patchLibraryAsset(asset.id, { expected_state_version: asset.state_version, favorite: !asset.favorite });
      setAssets((rows) => rows.map((row) => row.id === updated.id ? updated : row));
      setSelected(updated);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function saveMetadata() {
    if (!selected) return;
    setBusy(true); setError('');
    try {
      const updated = await patchLibraryAsset(selected.id, {
        expected_state_version: selected.state_version,
        title: editTitle.trim(), category: editCategory,
        tags: editTags.split(',').map((tag) => tag.trim()).filter(Boolean),
        collection: editCollection.trim() || null,
      });
      setSelected(updated);
      setAssets((rows) => rows.map((row) => row.id === updated.id ? updated : row));
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function archive(asset: LibraryAsset) {
    if (!window.confirm(`归档“${asset.title}”？已被作品引用的文件仍会保留。`)) return;
    setBusy(true); setError('');
    try { await archiveLibraryAsset(asset.id); setAssets((rows) => rows.filter((row) => row.id !== asset.id)); setSelected(null); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }

  return <div className="asset-library-layout">
    <section className="asset-library-main">
      <div className="asset-card"><div className="asset-library-toolbar"><div><div className="asset-eyebrow">全局素材库</div><h2>所有素材和版本都在这里</h2></div>
        <button type="button" className="asset-secondary-button" disabled={busy} onClick={() => imageInput.current?.click()}><Upload size={15} />导入图片</button>
        <input ref={imageInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void importImage(file);
        }} />
        <label className="asset-search"><Search size={15} /><input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索名称或标签" /></label>
        <select className="asset-category-filter" aria-label="按分类筛选素材" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
          <option value="">全部分类</option><option value="character">人物</option><option value="scene">场景</option><option value="prop">道具</option><option value="costume">服装</option><option value="voice">音色</option><option value="dialogue">对白</option><option value="sfx">音效</option><option value="music">音乐</option><option value="other">其他</option>
        </select>
      </div>
        {error && <div className="asset-inline-error" role="alert">{error}</div>}
        {loading ? <div className="asset-empty-small">正在读取素材…</div> : assets.length ? <div className="asset-library-grid">{assets.map((asset) => <button key={asset.id} className={`asset-library-item ${selected?.id === asset.id ? 'selected' : ''}`} onClick={() => { setSelected(asset); void getLibraryAsset(asset.id).then(setSelected); }}>
          <AssetPreview asset={asset} /><span className="asset-library-item-copy"><b>{asset.title}</b><small>{asset.category} · {asset.version_count} 个版本</small><em>{LABELS[asset.current_version?.status ?? 'CANDIDATE'] ?? asset.current_version?.status ?? '待审核'}</em></span><Star size={15} fill={asset.favorite ? 'currentColor' : 'none'} />
        </button>)}</div> : <div className="asset-empty"><LibraryBig size={28} /><b>还没有全局素材</b><span>导入图片或音频后，就能在没有影视项目的情况下整理和复用。</span></div>}
      </div>
    </section>
    <aside className="asset-library-detail">
      {selected ? <div className="asset-card asset-detail-card">
        <div className="asset-detail-actions"><button className="asset-icon-button" aria-label="收藏素材" onClick={() => void toggleFavorite(selected)}><Star size={16} fill={selected.favorite ? 'currentColor' : 'none'} /></button><button className="asset-icon-button" aria-label="归档素材" onClick={() => void archive(selected)}><Archive size={15} /></button></div>
        <AssetPreview asset={selected} large />
        <div className="asset-detail-title"><div className="asset-eyebrow">{selected.category} · {selected.kind}</div><h2>{selected.title}</h2><span>{selected.version_count} 个不可覆盖的版本</span></div>
        <details className="asset-metadata-editor"><summary>整理素材信息</summary>
          <label className="asset-field"><span>名称</span><input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} maxLength={200} /></label>
          <label className="asset-field"><span>分类</span><select value={editCategory} onChange={(event) => setEditCategory(event.target.value)}><option value="character">人物</option><option value="scene">场景</option><option value="prop">道具</option><option value="costume">服装</option><option value="voice">音色</option><option value="dialogue">对白</option><option value="sfx">音效</option><option value="music">音乐</option><option value="other">其他</option></select></label>
          <label className="asset-field"><span>标签（逗号分隔）</span><input value={editTags} onChange={(event) => setEditTags(event.target.value)} /></label>
          <label className="asset-field"><span>素材组</span><input value={editCollection} onChange={(event) => setEditCollection(event.target.value)} maxLength={120} /></label>
          <button className="asset-secondary-button full" disabled={busy || !editTitle.trim()} onClick={() => void saveMetadata()}>保存素材信息</button>
        </details>
        {selected.versions?.map((version) => <div className="asset-version-card" key={version.id}>
          <div className="asset-version-heading"><b>V{version.ordinal} · {LABELS[version.status] ?? version.status}</b><small>{version.width && version.height ? `${version.width} × ${version.height} · ` : ''}{version.content_sha256.slice(0, 12)}</small></div>
          <div className="asset-version-meta"><span>{version.source_mode}</span><span>{version.is_mock ? '模拟结果' : '真实文件'}</span></div>
          {version.prompt && <p className="asset-prompt-preview">{version.prompt}</p>}
          {version.kind === 'audio' && <audio className="asset-version-audio" controls preload="none" src={version.media_url}>当前浏览器不支持音频播放。</audio>}
          {version.qc && <details className="asset-diagnostics"><summary>技术检查详情</summary><pre>{JSON.stringify(version.qc, null, 2)}</pre></details>}
          {version.status !== 'PROMOTED' && !version.is_mock && <button className="asset-secondary-button full" disabled={busy} onClick={() => void reviewVersion(version)}><BadgeCheck size={14} />检查并人工晋升</button>}
          {version.status === 'PROMOTED' && <div className="asset-promoted-note"><Check size={13} />已人工晋升；项目版本引用不会随全局升级而改变。</div>}
        </div>)}
        <div className="asset-use-list"><b>项目使用记录</b>{selected.usage?.length ? selected.usage.map((use) => <small key={use.project_asset_id}>{use.project_id.slice(0, 8)} · {use.role} · {use.entity_id || '未指定身份'} · V{selected.versions?.find((v) => v.id === use.version_id)?.ordinal}</small>) : <small>尚未分配给影视项目。</small>}</div>
      </div> : <div className="asset-card asset-empty"><LibraryBig size={24} /><b>选择一项素材查看详情</b><span>可以审核候选、查看版本来源和项目使用情况。</span></div>}
    </aside>
  </div>;
}

function AssignmentsPage({ projects, projectId, projectAssets }: { projects: Project[]; projectId: string; projectAssets: Asset[] }) {
  const [query, setQuery] = useState('');
  const { assets, loading, error: loadError, refresh } = useFreshList(query);
  const [selectedVersion, setSelectedVersion] = useState('');
  const [targetProject, setTargetProject] = useState(projectId || projects[0]?.id || '');
  const [role, setRole] = useState('character');
  const [entity, setEntity] = useState('');
  const [assignments, setAssignments] = useState<Array<Record<string, any>>>([]);
  const [error, setError] = useState(loadError);
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [savingProjectAsset, setSavingProjectAsset] = useState('');

  useEffect(() => { listAssignments().then((row) => setAssignments(row.items)).catch((reason) => setError(errorText(reason))); }, []);
  const choices = assets.filter((asset) => asset.current_version?.status === 'PROMOTED' && !asset.current_version.is_mock);
  async function assign() {
    if (!targetProject || !selectedVersion) { setError('请选择作品和已晋升版本，并填写作品内素材身份。'); return; }
    setSaving(true); setError(''); setNotice('');
    try {
      const result = await assignLibraryVersion({ project_id: targetProject, library_version_id: selectedVersion,
        role, entity_id: entity.trim() || null, idempotency_key: crypto.randomUUID() });
      setNotice(`已分配到作品。项目素材编号：${result.project_asset_id}`);
      setAssignments((rows) => [result, ...rows]); refresh();
    } catch (reason) { setError(errorText(reason)); }
    finally { setSaving(false); }
  }

  async function saveProjectAsset(asset: Asset) {
    setSavingProjectAsset(asset.id); setError(''); setNotice('');
    try {
      const result = await saveProjectAssetToLibrary(asset.project_id, asset.id, {
        title: asset.entity_id || asset.filename.replace(/\.[^.]+$/, ''),
        category: !asset.role || ['generic', 'style_reference'].includes(asset.role) ? 'other' : asset.role,
        tags: asset.tags ?? [],
      });
      setNotice(`项目素材已保存到全局库：${result.title}。`);
      refresh();
    } catch (reason) { setError(errorText(reason)); }
    finally { setSavingProjectAsset(''); }
  }

  return <div className="asset-assignment-layout">
    <div className="asset-card"><div className="asset-eyebrow">全局素材 → 作品</div><h2>分配一个已晋升版本</h2><p className="asset-muted">每个作品会得到独立 Asset 身份；旧项目冻结引用保持原样。</p>
      {loading && <div className="asset-empty-small">正在读取全局素材…</div>}
      {projects.length ? <>
        <label className="asset-field"><span>目标作品</span><select value={targetProject} onChange={(event) => setTargetProject(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}</select></label>
        <label className="asset-field"><span>搜索素材</span><input value={query} onChange={(event) => {
          setQuery(event.target.value);
          setSelectedVersion('');
        }} placeholder="名称或标签" /></label>
        <label className="asset-field"><span>全局素材版本</span><select value={selectedVersion} onChange={(event) => {
          const versionId = event.target.value;
          const asset = choices.find((item) => item.current_version?.id === versionId);
          setSelectedVersion(versionId);
          if (asset) setRole(asset.category === 'other' ? 'generic' : asset.category);
        }}>
          <option value="">选择已晋升的正式版本</option>{choices.map((asset) => <option key={asset.id} value={asset.current_version!.id}>{asset.title} · V{asset.current_version!.ordinal}</option>)}
        </select></label>
        <div className="asset-field-row"><label className="asset-field"><span>作品中的用途</span><select value={role} onChange={(event) => setRole(event.target.value)}><option value="character">人物</option><option value="costume">服装</option><option value="scene">场景</option><option value="prop">道具</option><option value="voice">音色</option><option value="dialogue">对白音频</option><option value="sfx">音效</option><option value="music">音乐</option><option value="generic">其他</option></select></label>
          <label className="asset-field"><span>作品内身份名称</span><input value={entity} onChange={(event) => setEntity(event.target.value)} placeholder="例如：林岚" /></label></div>
        <button className="asset-primary-button" disabled={saving || !selectedVersion} onClick={() => void assign()}><ArrowRight size={16} />{saving ? '正在分配…' : '分配到作品'}</button>
      </> : <div className="asset-empty-small">尚无影视项目。可以先独立整理素材，创建作品后再分配。</div>}
      {error && <div className="asset-inline-error" role="alert">{error}</div>}{notice && <div className="asset-success-note" role="status"><Check size={14} />{notice}</div>}
    </div>
    <div className="asset-card"><div className="asset-eyebrow">作品 → 全局素材</div><h2>收藏项目已锁定素材</h2><p className="asset-muted">保存操作会先检查项目归属、审批、锁定状态、文件哈希。</p>
      {projectAssets.filter((asset) => asset.approved && asset.locked).length ? <>
        <div className="asset-task-list">{projectAssets.filter((asset) => asset.approved && asset.locked).map((asset) => <div key={asset.id} className="asset-project-save-row">
        <div><b>{asset.filename}</b><small>{asset.role ?? '其他'} · {asset.sha256.slice(0, 12)} · {asset.locked ? '已锁定' : '未锁定'}</small></div>
        <button className="asset-secondary-button" disabled={!!savingProjectAsset} onClick={() => void saveProjectAsset(asset)}><Copy size={14} />{savingProjectAsset === asset.id ? '正在保存…' : '收藏到全局库'}</button>
      </div>)}</div>
      </> : <div className="asset-empty-small">当前作品没有已批准并锁定的素材，或尚未选择作品。</div>}
    </div>
    <div className="asset-card asset-assignment-history"><div><h2>使用记录</h2><p className="asset-muted">每条记录都固定版本号和新的项目素材 ID。</p></div>
      {assignments.length ? <div className="asset-task-list">{assignments.map((item) => <div className="asset-task-row" key={item.id}><span className="asset-task-kind"><FileImage size={15} /></span><div><b>{item.title || '素材'} → {item.project_title || item.project_id.slice(0, 8)}</b><small>{item.role} · {item.entity_id || '未指定身份'} · {item.usage_scope} · {String(item.project_asset_id).slice(0, 8)}</small></div><Check size={15} /></div>)}</div> : <div className="asset-empty-small">还没有分配记录。</div>}
    </div>
  </div>;
}

function TaskRow({ task, onLibrary }: { task: LibraryTask; onLibrary?: () => void }) {
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMessage, setReconcileMessage] = useState('');
  async function reconcile() {
    setReconciling(true); setReconcileMessage('');
    try {
      const result = await reconcileLibraryImageTask(task.id);
      setReconcileMessage(result.next_action ?? '已读取既有 ComfyUI 结果；没有重新提交任务。');
    } catch (error) { setReconcileMessage(errorText(error)); }
    finally { setReconciling(false); }
  }
  async function createNewAttempt() {
    if (!window.confirm('原任务已确认没有提交到 ComfyUI。是否创建一个新的、可单独追踪的任务？')) return;
    setReconciling(true); setReconcileMessage('');
    try {
      const created = await newLibraryImageAttempt(task.id);
      const prior = (() => { try { return JSON.parse(window.localStorage.getItem('shine-assets-recent-tasks') ?? '[]'); } catch { return []; } })();
      window.localStorage.setItem('shine-assets-recent-tasks', JSON.stringify([{ id: created.id, kind: 'image' }, ...prior].slice(0, 8)));
      setReconcileMessage(`已创建新任务 ${created.id.slice(0, 8)}；刷新后可继续查看。`);
    } catch (error) { setReconcileMessage(errorText(error)); }
    finally { setReconciling(false); }
  }
  return <div className="asset-task-row"><span className={`asset-task-kind ${task.kind}`}>
    {task.kind === 'image' ? <FileImage size={15} /> : <FileAudio2 size={15} />}
  </span><div><b>{task.mode === 'T2I' ? '图片候选' : task.kind === 'voice' ? '声音任务' : task.mode}</b><small>{taskLabel(task)}{task.external_prompt_id ? ` · ${task.external_prompt_id.slice(0, 8)}` : ''}</small>
    {reconcileMessage && <small role="status">{reconcileMessage}</small>}</div>
    {task.kind === 'image' && ['UNKNOWN', 'RECONCILE_REQUIRED'].includes(task.status)
      ? <button className="asset-text-button" disabled={reconciling} title="只读取已有任务结果，不会重新提交" onClick={() => void reconcile()}>{reconciling ? '核对中…' : '对账'}</button>
      : task.can_new_attempt
        ? <button className="asset-text-button" disabled={reconciling} onClick={() => void createNewAttempt()}>{reconciling ? '创建中…' : '创建新任务'}</button>
        : task.result_version_id && onLibrary
        ? <button className="asset-text-button" onClick={onLibrary}>审核候选</button>
        : <span className={`asset-task-status ${task.status === 'BLOCKED_CAPABILITY' ? 'pending' : ''}`}>{LABELS[task.status] ?? task.status}</span>}
  </div>;
}

function AssetPreview({ asset, large = false }: { asset: LibraryAsset; large?: boolean }) {
  const current = asset.current_version;
  if (current?.kind === 'image') return <img className={`asset-preview-image ${large ? 'large' : ''}`} src={current.media_url} alt={asset.title} loading="lazy" />;
  if (current?.kind === 'audio') return <div className={`asset-preview-audio ${large ? 'large' : ''}`}><AudioLines size={large ? 28 : 20} /><span>音频素材</span></div>;
  return <div className={`asset-preview-empty ${large ? 'large' : ''}`}><FileImage size={large ? 26 : 19} /><span>候选素材</span></div>;
}

function MiniAsset({ asset }: { asset: LibraryAsset }) {
  return <div className="asset-mini-item"><AssetPreview asset={asset} /><div><b>{asset.title}</b><small>{asset.current_version ? `V${asset.current_version.ordinal} · ${LABELS[asset.current_version.status] ?? asset.current_version.status}` : '待审核'}</small></div></div>;
}
