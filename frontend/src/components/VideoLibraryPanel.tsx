import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Film, FolderOpen, RefreshCw, Trash2 } from 'lucide-react';
import { api, json } from '../api';
import { VideoPreview } from './GenerationSegmentsPanel';
import { OpenVideoFolderButton } from './OpenVideoFolderButton';

type Blocker = { code: string; message: string };
type LibraryEntry = {
  artifact_id: string;
  kind: 'shot' | 'delivery';
  project_id: string;
  project_title: string;
  episode_position: number | null;
  episode_label: string;
  shot_numbers: number[];
  shot_range_label: string;
  shot_names: string[];
  shot_name: string | null;
  job_id: string | null;
  take: number;
  version_label: string;
  review_state: string;
  adopted: boolean;
  file_state: 'ok' | 'missing' | 'changed' | 'path_invalid';
  deletable: boolean;
  delete_blockers: Blocker[];
  shared_file: boolean;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  generated_at: string | null;
  byte_size: number;
  file_name: string;
  folder: 'current' | 'history' | 'legacy';
  is_mock: boolean;
  deleted: boolean;
  preview_url: string | null;
  play_url: string | null;
};
type Library = {
  project_id: string;
  project_title: string;
  entries: LibraryEntry[];
  summary: { total: number; current: number; history: number; pending_review: number; missing: number; deletable: number };
};
type DeleteResult = {
  deleted: string[];
  already_deleted: string[];
  missing: string[];
  blocked: Array<{ artifact_id: string; blockers: Blocker[] }>;
  failed: Array<{ artifact_id: string; code: string; message: string }>;
  files_deleted: number;
  files_kept_shared: number;
};

const PAGE_SIZE = 12;
const FILE_STATE_LABELS: Record<string, string> = {
  missing: '文件缺失',
  changed: '文件与登记不一致',
  path_invalid: '路径异常',
};
const REVIEW_LABELS: Record<string, string> = {
  passed: '已通过',
  pending_review: '待审',
  running: '制作中',
  needs_attention: '需处理',
  mock: '演示样本',
};

function timeLabel(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value.endsWith('Z') ? value : `${value}Z`);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString('zh-CN', { hour12: false }) : null;
}

function sizeLabel(bytes: number): string {
  if (!bytes || bytes < 0) return '大小未登记';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function rangeLabel(entry: LibraryEntry): string {
  return entry.shot_range_label === '镜号未登记' ? entry.shot_range_label : `${entry.shot_range_label} 镜`;
}

function entryState(entry: LibraryEntry): { text: string; tone: string; review?: string } {
  if (entry.file_state !== 'ok') return { text: FILE_STATE_LABELS[entry.file_state] ?? '文件异常', tone: 'broken' };
  if (entry.is_mock) return { text: entry.adopted ? '当前采用 · 演示' : '历史 · 演示', tone: 'mock' };
  const review = entry.review_state !== 'passed'
    ? (REVIEW_LABELS[entry.review_state] ?? entry.review_state)
    : undefined;
  return entry.adopted
    ? { text: '当前采用', tone: 'current', review }
    : { text: '历史', tone: 'history', review };
}

type Props = {
  projectId: string;
  projectTitle: string;
  active: boolean;
  refreshEpoch: number;
  onMessage: (text: string, kind?: 'success' | 'error') => void;
};

export default function VideoLibraryPanel({ projectId, projectTitle, active, refreshEpoch, onMessage }: Props) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [organizing, setOrganizing] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<DeleteResult | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;

  const reload = useCallback(async () => {
    const requested = projectId;
    const body = await api<Library>(`/projects/${projectId}/video-library`);
    if (projectRef.current !== requested) return;
    setLibrary(body);
    setLoadError('');
    setSelected((current) => new Set([...current].filter((id) => body.entries.some((entry) => entry.artifact_id === id))));
  }, [projectId]);

  useEffect(() => {
    if (!active) return;
    void reload().catch((error: Error) => {
      setLoadError(String(error.message || error));
      setLibrary(null);
    });
  }, [active, reload, refreshEpoch]);

  useEffect(() => { setPage(0); setSelected(new Set()); setResult(null); }, [projectId]);

  const entries = library?.entries ?? [];
  const pageCount = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = entries.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const visibleIds = useMemo(() => visible.map((entry) => entry.artifact_id), [visible]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const selectedEntries = entries.filter((entry) => selected.has(entry.artifact_id));
  const deletableSelected = selectedEntries.filter((entry) => entry.deletable);
  const blockedSelected = selectedEntries.filter((entry) => !entry.deletable);
  const pageFirst = entries.length ? currentPage * PAGE_SIZE + 1 : 0;
  const pageLast = Math.min((currentPage + 1) * PAGE_SIZE, entries.length);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleVisible() {
    setSelected((current) => {
      const next = new Set(current);
      if (allVisibleSelected) visibleIds.forEach((id) => next.delete(id));
      else visibleIds.forEach((id) => next.add(id));
      return next;
    });
  }

  async function confirmDelete() {
    if (busy || deletableSelected.length === 0) return;
    setBusy(true);
    try {
      const ids = deletableSelected.map((entry) => entry.artifact_id);
      const outcome = await api<DeleteResult>(`/projects/${projectId}/video-library/delete`,
        json('POST', { artifact_ids: ids }));
      setResult(outcome);
      await reload();
      const retry = new Set([
        ...outcome.blocked.map((item) => item.artifact_id),
        ...outcome.failed.map((item) => item.artifact_id),
      ]);
      setSelected(retry);
      const deletedCount = outcome.deleted.length + outcome.missing.length;
      onMessage(
        outcome.blocked.length || outcome.failed.length
          ? `已删除 ${deletedCount} 条，${outcome.blocked.length + outcome.failed.length} 条未能删除（已保留勾选，可处理后重试）。`
          : `已删除 ${deletedCount} 条本机视频。`,
        outcome.blocked.length || outcome.failed.length ? 'error' : 'success',
      );
      setConfirming(false);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : String(error), 'error');
    } finally {
      setBusy(false);
    }
  }

  async function organizeCurrent() {
    if (organizing || busy) return;
    setOrganizing(true);
    try {
      const outcome = await api<{ files_moved: number; errors: Array<{ message: string }> }>(
        `/projects/${projectId}/video-library/organize-current`, json('POST', {}));
      await reload();
      onMessage(
        outcome.errors.length
          ? `已整理 ${outcome.files_moved} 个文件，${outcome.errors.length} 个文件被占用未能移动（可稍后重试）。`
          : `已整理 ${outcome.files_moved} 个文件：当前采用版本在 current/，未采用版本在 history/。`,
        outcome.errors.length ? 'error' : 'success',
      );
    } catch (error) {
      onMessage(error instanceof Error ? error.message : String(error), 'error');
    } finally {
      setOrganizing(false);
    }
  }

  const summary = library?.summary;

  return <div className="video-library" aria-label="成片库">
    <section className="card">
      <div className="panel-header">
        <h2><Film size={18} aria-hidden="true" /> 成片库 · {library?.project_title ?? projectTitle}</h2>
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => void reload().catch((error: Error) => setLoadError(String(error.message || error)))}>
          <RefreshCw size={13} /> 刷新
        </button>
      </div>
      <div className="module-guide">
        <FolderOpen size={13} />
        <div>
          <p><b>这里管理这部作品在本机落盘的全部视频</b>：单镜、分段、分集与成片，含历史与重拍版本。预览读取的是本机原文件，不会下载或复制第二份。</p>
          <p>「当前采用」是镜头现在展示的版本；历史版本可删除，但被有效成片、连续性关系或当前版本引用的文件会被拦下。</p>
        </div>
      </div>
      {summary && <div className="library-summary" role="status">
        <span><b>{summary.total}</b> 条视频</span>
        <span>当前采用 {summary.current}</span>
        <span>历史 {summary.history}</span>
        <span>待审 {summary.pending_review}</span>
        <span className={summary.missing ? 'failure' : ''}>缺失/异常 {summary.missing}</span>
      </div>}
      {loadError && <p className="failure" role="alert">成片库读取失败：{loadError}</p>}
      {!library && !loadError && <p>正在读取本机视频台账…</p>}
      {library && entries.length === 0 && <p className="material-hint">这部作品还没有落盘视频。完成一次真实出片后，单镜和成片都会出现在这里。</p>}
    </section>

    {entries.length > 0 && <section className="card library-manage">
      <div className="library-toolbar">
        <label className="library-select-all">
          <input type="checkbox" checked={allVisibleSelected} onChange={toggleVisible} />
          全选本页（{visible.length} 条）
        </label>
        <span className="library-selection-note" role="status">
          已勾选 <b>{selected.size}</b> 条（跨全部页）；其中 {deletableSelected.length} 条可删除
          {blockedSelected.length > 0 && <>，{blockedSelected.length} 条受保护</>}
        </span>
        <span className="library-page-note">第 {pageFirst}–{pageLast} 条 / 共 {entries.length} 条</span>
        <button type="button" className="btn-secondary" disabled={busy || organizing}
          title="把当前采用版本移入 current/，把未采用版本移入 history/；只移动文件，不删除、不改 SHA"
          onClick={() => void organizeCurrent()}>
          <FolderOpen size={14} /> {organizing ? '正在整理…' : '整理采用目录'}
        </button>
        <button type="button" className="btn-danger" disabled={busy || deletableSelected.length === 0}
          onClick={() => { setResult(null); setConfirming(true); }}>
          <Trash2 size={14} /> 批量删除（{deletableSelected.length}）
        </button>
      </div>
      <div className="library-grid">
        {visible.map((entry) => {
          const state = entryState(entry);
          const generated = timeLabel(entry.generated_at);
          const checked = selected.has(entry.artifact_id);
          return <article className={`library-card ${checked ? 'selected' : ''} ${entry.file_state !== 'ok' ? 'broken' : ''}`} key={entry.artifact_id}>
            <label className="library-check" title="勾选这条视频">
              <input type="checkbox" checked={checked} onChange={() => toggle(entry.artifact_id)}
                aria-label={`选择 ${entry.episode_label} ${rangeLabel(entry)} ${entry.version_label}`} />
            </label>
            {entry.preview_url && entry.play_url
              ? <VideoPreview poster={entry.preview_url} video={entry.play_url}
                  label={`${entry.episode_label} ${rangeLabel(entry)} ${entry.version_label}`} />
              : <div className="library-placeholder"><Film size={22} aria-hidden="true" /><span>{FILE_STATE_LABELS[entry.file_state] ?? '预览不可用'}</span></div>}
            <div className="library-card-head">
              <b>{entry.episode_label} · {rangeLabel(entry)}</b>
              <span className="library-states">
                <span className={`library-state ${state.tone}`}>{state.text}</span>
                {state.review && <span className="library-state pending">{state.review}</span>}
              </span>
            </div>
            <p className="library-shot-name">{entry.kind === 'delivery'
              ? (entry.shot_names.length ? entry.shot_names.join(' / ') : '成片（镜头来源未登记）')
              : (entry.shot_name ?? '镜头未登记')}</p>
            <div className="library-meta">
              <span>{entry.kind === 'delivery' ? '成片' : '单镜'} · {entry.version_label}</span>
              {entry.duration_s != null && <span>{entry.duration_s.toFixed(2)} 秒</span>}
              {entry.width && entry.height && <span>{entry.width}×{entry.height}</span>}
              {entry.fps != null && <span>{Math.round(entry.fps)} fps</span>}
              <span>{sizeLabel(entry.byte_size)}</span>
              {generated && <span>{generated}</span>}
            </div>
            <small className="library-file" title={entry.file_name}>
              磁盘文件：{entry.folder === 'current' ? '当前采用/' : entry.folder === 'history' ? '未采用/' : ''}{entry.file_name}
            </small>
            <div className="library-actions">
              <OpenVideoFolderButton artifactId={entry.artifact_id} />
            </div>
            {!entry.deletable && entry.delete_blockers.length > 0 && <p className="library-blocked">
              <AlertTriangle size={12} aria-hidden="true" />
              不可删除：{entry.delete_blockers.map((blocker) => blocker.message).join('；')}
            </p>}
            {entry.deletable && entry.shared_file && <p className="library-blocked soft">与其他条目共用同一个文件；删除本条不会删除磁盘文件。</p>}
          </article>;
        })}
      </div>
      <div className="library-pager">
        <button type="button" className="btn-secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
        <span>第 {currentPage + 1} / {pageCount} 页</span>
        <button type="button" className="btn-secondary" disabled={currentPage >= pageCount - 1} onClick={() => setPage(currentPage + 1)}>下一页</button>
        {selected.size > 0 && <button type="button" className="btn-secondary" onClick={() => setSelected(new Set())}>清空勾选</button>}
      </div>
    </section>}

    {result && <section className="card" role="status">
      <div className="panel-header"><h2>上次批量删除结果</h2></div>
      <p>已删除 {result.deleted.length} 条，清理已缺失记录 {result.missing.length} 条，磁盘文件删除 {result.files_deleted} 个
        {result.files_kept_shared > 0 && `，因共用保留文件 ${result.files_kept_shared} 个`}
        {result.already_deleted.length > 0 && `，${result.already_deleted.length} 条之前已删除`}。</p>
      {result.blocked.length > 0 && <div className="library-result-list">
        <b>未能删除：</b>
        {result.blocked.map((item) => <p key={item.artifact_id}>
          {entries.find((entry) => entry.artifact_id === item.artifact_id)?.file_name ?? item.artifact_id}：
          {item.blockers.map((blocker) => blocker.message).join('；')}
        </p>)}
      </div>}
      {result.failed.length > 0 && <div className="library-result-list">
        <b>删除失败：</b>
        {result.failed.map((item) => <p key={item.artifact_id}>{item.message}</p>)}
      </div>}
    </section>}

    {confirming && <dialog className="library-confirm" open aria-label="确认批量删除">
      <h3>确认删除本机视频</h3>
      <p>作品：<b>{library?.project_title ?? projectTitle}</b></p>
      <p>选中 <b>{selected.size}</b> 条，其中 <b>{deletableSelected.length}</b> 条将删除磁盘文件，{blockedSelected.length} 条受保护不会处理。</p>
      <div className="library-confirm-files">
        <b>将删除的文件：</b>
        {deletableSelected.length === 0 && <p>没有可删除的文件。</p>}
        {deletableSelected.map((entry) => <p key={entry.artifact_id}>
          {entry.file_name}{entry.shared_file ? '（共用文件，只移除本条记录）' : ''}
          {entry.file_state === 'missing' ? '（文件已缺失，只清理记录）' : ''}
        </p>)}
      </div>
      {blockedSelected.length > 0 && <div className="library-confirm-files">
        <b>无法删除：</b>
        {blockedSelected.map((entry) => <p key={entry.artifact_id}>
          {entry.file_name}：{entry.delete_blockers.map((blocker) => blocker.message).join('；')}
        </p>)}
      </div>}
      <p className="library-confirm-note">删除只清理本机视频文件；Job、交付回执与审计保留，并记录这次删除。此操作不可撤销。</p>
      <div className="library-actions">
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => setConfirming(false)}>取消</button>
        <button type="button" className="btn-danger" disabled={busy || deletableSelected.length === 0} onClick={() => void confirmDelete()}>
          {busy ? '正在删除…' : `确认删除 ${deletableSelected.length} 条`}
        </button>
      </div>
    </dialog>}
  </div>;
}
