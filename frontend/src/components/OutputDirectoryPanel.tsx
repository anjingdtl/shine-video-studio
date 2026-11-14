import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, FolderTree, HardDrive, RefreshCw } from 'lucide-react';
import { api, json } from '../api';

type Layout = { material: string; video: string; voice: string; material_center: string };

export type OutputDirectoryState = {
  root: string;
  default_root: string;
  custom: boolean;
  layout: Layout;
  projects: Array<{ project_id: string; title: string; folder: string }>;
  file_count: number;
  byte_size: number;
  free_gb: number;
  migration: { root?: string; started_at?: string; actor?: string } | null;
  blockers: string[];
};

type MigrationResult = {
  root: string;
  custom: boolean;
  moved: number;
  copied: number;
  bytes_copied: number;
  deleted: number;
  kept: string[];
  missing?: Array<{ label: string; path: string; reason: string }>;
  missing_count?: number;
  duration_s: number;
};

function sizeText(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** The one output root: where every project's 素材 / 视频 / 音色 live. */
export default function OutputDirectoryPanel() {
  const [state, setState] = useState<OutputDirectoryState | null>(null);
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [message, setMessage] = useState('');
  const [result, setResult] = useState<MigrationResult | null>(null);

  const load = useCallback(async () => {
    try {
      const value = await api<OutputDirectoryState>('/settings/output-directory');
      setState(value);
      setPath(value.custom ? value.root : '');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function save(target: string | null) {
    setBusy(true);
    setMessage('');
    setResult(null);
    try {
      const value = await api<MigrationResult>('/settings/output-directory', {
        ...json('PUT', { path: target }),
      });
      setResult(value);
      setMessage(value.moved
        ? `已迁移 ${value.moved} 个文件：${value.root}`
        : `输出目录已保存：${value.root}`);
      await load();
      window.dispatchEvent(new Event('shine-output-directory-changed'));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      await load();
    } finally {
      setBusy(false);
    }
  }

  /** 拉起系统的「选择文件夹」窗口；选中后直接保存并迁移。 */
  async function choose() {
    setPicking(true);
    setMessage('');
    try {
      const picked = await api<{ available: boolean; cancelled: boolean; path: string | null; message: string }>(
        '/settings/output-directory/select', json('POST', {}),
      );
      if (picked.cancelled) { setMessage('已取消选择。'); return; }
      if (!picked.path) { setMessage(picked.message || '没有选择目录。'); return; }
      setPath(picked.path);
      if (!window.confirm(`把作品输出目录切换到：\n${picked.path}\n\n会先复制并逐个核验已有文件，全部通过后才切换；有镜头正在生产时会拒绝。继续？`)) {
        setMessage(`已填入 ${picked.path}；需要时再点「保存并迁移」。`);
        return;
      }
      await save(picked.path);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setPicking(false);
    }
  }

  if (!state) {
    return <div className="settings-block" id="output-directory">
      <h3><FolderTree size={14} /> 作品输出目录</h3>
      <p className="guardrail-note">{message || '正在读取输出目录…'}</p>
    </div>;
  }

  const layout = state.layout ?? { material: '素材', video: '视频', voice: '音色', material_center: '素材中心' };
  const blockers = state.blockers ?? [];
  const projects = state.projects ?? [];
  const folders = [...projects.map((project) => project.folder), layout.material_center];

  return <div className="settings-block" id="output-directory">
    <h3><FolderTree size={14} /> 作品输出目录</h3>
    <p>素材、镜头视频、成片和音色都放在这个目录下，按「项目 → 类型」自动归档，文件名统一为「项目_类型_名称」。</p>
    <div className="guardrail-note">
      当前目录：{state.root}{state.custom ? '（自定义）' : '（默认）'}
      {' · '}{state.file_count ?? 0} 个文件 · {sizeText(state.byte_size ?? 0)} · 可用 {state.free_gb ?? 0} GB
    </div>
    <p className="guardrail-note"><HardDrive size={13} /> 结构：
      <code>{state.root}\&lt;项目&gt;\{layout.material}</code>、<code>{layout.video}</code>、<code>{layout.voice}</code>；
      无项目的素材放在 <code>{layout.material_center}</code> 项目内。</p>
    {folders.length > 0 && <div className="output-folder-preview">
      {folders.slice(0, 8).map((folder) => <span className="chip" key={folder}>{folder}</span>)}
      {folders.length > 8 && <span className="chip">…共 {folders.length} 个</span>}
    </div>}
    {state.migration && <div className="blocked-callout">
      <RefreshCw size={13} />
      <span>正在迁移输出目录：{state.migration.root ?? state.root}。迁移完成前不会写入新文件。</span>
    </div>}
    {blockers.length > 0 && <div className="blocked-callout">
      <RefreshCw size={13} />
      <span>现在还不能迁移：{blockers.join('；')}。等这些任务结束后再切换目录。</span>
    </div>}
    <div className="inline-actions">
      <button className="btn-primary" disabled={busy || picking || blockers.length > 0} onClick={() => void choose()}>
        <FolderTree size={13} /> {picking ? '正在等待选择…' : '选择目录'}
      </button>
      {state.custom && <button className="btn-secondary" disabled={busy || blockers.length > 0}
        onClick={() => void save(null)}>
        恢复默认目录
      </button>}
      <button className="btn-secondary" disabled={busy} onClick={() => void load()}>刷新</button>
      <details className="inline-manual-path">
        <summary>手动填写路径</summary>
        <label className="field-line">绝对路径
          <input aria-label="作品输出目录" value={path} placeholder={state.default_root}
            onChange={(event) => setPath(event.target.value)} />
        </label>
        <button className="btn-secondary" disabled={busy || !path.trim() || blockers.length > 0}
          onClick={() => void save(path.trim())}>保存并迁移</button>
      </details>
    </div>
    <p className="guardrail-note">
      切换目录会先复制并逐个核验文件，全部通过后才切换；任何一步失败都不会切换。有镜头正在拍摄或素材正在生成时会被拒绝，不会打断任务。
      源文件已缺失的台账记录会被如实报告并跳过（路径保持不变），不阻挡其余文件迁移。
    </p>
    {result && <div className="guardrail-note">
      已复制 {result.copied} 个 / {sizeText(result.bytes_copied)}，清理旧文件 {result.deleted} 个，
      用时 {result.duration_s} 秒{result.kept.length ? `；${result.kept.length} 个旧文件未能删除，已保留` : ''}。
    </div>}
    {result && (result.missing_count ?? 0) > 0 && <div className="blocked-callout">
      <AlertTriangle size={13} />
      <span>
        {result.missing_count} 条台账记录的源文件已缺失，未移动、路径保持不变：
        {(result.missing ?? []).slice(0, 3).map((row) => row.label).join('；')}
        {(result.missing_count ?? 0) > 3 ? ' 等' : ''}。这些记录在迁移前就已缺文件，如需清理可在素材/成片库处理。
      </span>
    </div>}
    {message && <div role="status">{message}</div>}
  </div>;
}
