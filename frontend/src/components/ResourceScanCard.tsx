import { useCallback, useEffect, useState } from 'react';
import { FolderSearch, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { api, json } from '../api';
import { Meter } from './Meter';

type Coverage = Record<string, { status: string; directories_visited: number; unreadable: string[] }>;

type ScanReport = {
  status: string;
  message: string | null;
  directories: Array<{ id: string; path: string }>;
  queue_remaining: number;
  coverage: Coverage;
  coverage_complete: boolean;
  resources: {
    comfy_roots: Array<{ root: string }>;
    node_dirs: Array<{ path: string; component_id: string }>;
    workflows: Array<{ path: string }>;
    model_candidate_count: number;
  };
  capabilities: Record<string, { found: unknown[]; missing: unknown[] }>;
};

/** 本机资源检索:固定盘深搜进度、用户检索目录与跨盘发现。
 *  检索是只读的;这里发现的字节仍是候选,核验登记由整备任务完成。 */
export function ResourceScanCard() {
  const [report, setReport] = useState<ScanReport | null>(null);
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const [pasted, setPasted] = useState('');

  const load = useCallback(async () => {
    try { setReport(await api<ScanReport>('/setup/resource-scan')); } catch { /* 保持上次结果 */ }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const scanning = report?.status === 'QUEUED' || report?.status === 'RUNNING';
  useEffect(() => {
    if (!scanning) return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [scanning, load]);

  async function startScan() {
    setBusy('scan'); setNote('');
    try {
      await api('/setup/resource-scan/start', json('POST', {}));
      await load();
    } catch (error) { setNote(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(''); }
  }

  async function addDirectory(path: string) {
    setBusy('add'); setNote('');
    try {
      await api('/setup/search-directories', json('POST', { path }));
      setPasted('');
      await load();
    } catch (error) { setNote(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(''); }
  }

  async function pickDirectory() {
    setBusy('pick'); setNote('');
    try {
      const picked = await api<{ available: boolean; cancelled: boolean; path: string | null; message?: string }>(
        '/setup/search-directories/select', json('POST', {}));
      if (picked.cancelled) return;
      if (!picked.path) { setNote(picked.message ?? '没有选到目录，请直接粘贴绝对路径。'); return; }
      await addDirectory(picked.path);
    } catch (error) { setNote(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(''); }
  }

  async function removeDirectory(id: string) {
    setBusy(id); setNote('');
    try {
      await api(`/setup/search-directories/${id}`, { method: 'DELETE' });
      await load();
    } catch (error) { setNote(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(''); }
  }

  const coverageRows = Object.values(report?.coverage ?? {});
  const doneRoots = coverageRows.filter((row) => row.status === 'DONE').length;
  const unreadable = coverageRows.flatMap((row) => row.unreadable ?? []);

  return <section className="card section-card resource-scan-card" id="resource-scan">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">本机资源检索</div>
        <h3><FolderSearch size={15} /> 散放在各盘的权重、节点与工作流</h3>
        <p>固定盘分批深搜，加上你指定的目录。发现的字节先作候选，整备时再逐字节核验；已有文件不会重复下载。</p>
      </div>
      <button type="button" className="btn-secondary" disabled={!!busy || scanning} onClick={() => void startScan()}>
        {scanning ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
        {scanning ? '检索中' : '重新检索'}
      </button>
    </div>

    {report && <p className="run-hint" role="status" data-testid="resource-scan-status">
      {report.message ?? '还没有开始检索'}
      {coverageRows.length > 0 && ` · 已完成 ${doneRoots}/${coverageRows.length} 个区域`}
      {report.queue_remaining > 0 && ` · 剩余 ${report.queue_remaining} 批`}
      {report.resources.comfy_roots.length > 0 && ` · 发现 ${report.resources.comfy_roots.length} 套 ComfyUI`}
      {report.resources.model_candidate_count > 0 && ` · ${report.resources.model_candidate_count} 个权重候选`}
      {report.resources.node_dirs.length > 0 && ` · ${report.resources.node_dirs.length} 个节点包`}
      {report.resources.workflows.length > 0 && ` · ${report.resources.workflows.length} 个工作流`}
    </p>}
    {scanning && <Meter value={doneRoots} max={coverageRows.length || 1}
      label="本机资源深搜" count={`${doneRoots}/${coverageRows.length || 0} 区域`} tone="busy" />}
    {unreadable.length > 0 && <details className="inline-manual-path">
      <summary>有 {unreadable.length} 个区域读不到（不影响其他区域）</summary>
      <ul>{unreadable.slice(0, 20).map((path) => <li key={path}>{path}</li>)}</ul>
    </details>}

    <div className="search-directories">
      <b>检索目录</b>
      {(report?.directories ?? []).length === 0 && <p className="run-hint">还没有指定目录；固定盘仍会自动深搜。</p>}
      <ul>
        {(report?.directories ?? []).map((row) => <li key={row.id}>
          <span>{row.path}</span>
          <button type="button" className="icon-button" aria-label={`删除检索目录 ${row.path}`}
            disabled={busy === row.id} onClick={() => void removeDirectory(row.id)}>
            <Trash2 size={13} />
          </button>
        </li>)}
      </ul>
      <div className="create-project">
        <input value={pasted} onChange={(event) => setPasted(event.target.value)}
          placeholder="粘贴本机绝对路径（支持中文与空格，可以是磁盘根或 models 目录）"
          onKeyDown={(event) => event.key === 'Enter' && pasted.trim() && void addDirectory(pasted.trim())} />
        <button type="button" className="btn-secondary" disabled={!!busy || !pasted.trim()}
          onClick={() => void addDirectory(pasted.trim())}>添加目录</button>
        <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => void pickDirectory()}>
          {busy === 'pick' ? <Loader2 size={13} className="spin" /> : <FolderSearch size={13} />} 选择目录…
        </button>
      </div>
    </div>
    {note && <p className="form-error" role="alert">{note}</p>}
  </section>;
}