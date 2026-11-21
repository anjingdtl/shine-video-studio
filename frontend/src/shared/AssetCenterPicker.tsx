import { useCallback, useEffect, useRef, useState } from 'react';
import { ImageOff, RefreshCw, Search, X } from 'lucide-react';

/**
 * 素材中心导入选择器（七阶段与四阶段共用的中性 UI）。
 *
 * 只读浏览素材中心里**已生成并可分配**的素材，用户点选一项后由调用方走各自
 * 既有的唯一采纳执行者（七阶段 assignLibraryVersion / 四阶段 adoptM4LibraryVersion），
 * 本组件不发起任何写操作，也不另起平行链路。列表源由调用方注入：七阶段用
 * searchLibraryForProject(scope=library)，四阶段用 searchM4Library。组件本身
 * 不依赖任何一条业务线的 api，故置于 shared/ 供两线共用。
 */
export type AssetPickDisplay = {
  key: string;
  title: string;
  subtitle: string;
  mediaUrl: string | null;
};

export function AssetCenterPicker<T>({ title, kicker, emptyHint, fetchItems, display, onPick, onClose }: {
  title: string;
  kicker: string;
  emptyHint: string;
  fetchItems: (query: string) => Promise<T[]>;
  display: (item: T) => AssetPickDisplay;
  onPick: (item: T) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<T[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const searchRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async (q: string) => {
    setBusy(true);
    setError('');
    try {
      const result = await fetchItems(q);
      if (alive.current) setItems(result);
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [fetchItems]);

  useEffect(() => {
    alive.current = true;
    void load('');
    searchRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => { alive.current = false; window.removeEventListener('keydown', onKey); };
  }, [load, onClose]);

  return <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
    <div className="editor-modal asset-picker-modal" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}><X size={16} /></button>
      <header className="editor-head">
        <div>
          <div className="section-kicker">{kicker}</div>
          <h2>{title}</h2>
        </div>
      </header>
      <div className="editor-body">
        <form className="asset-picker-search" onSubmit={(event) => { event.preventDefault(); void load(query); }}>
          <Search size={14} />
          <input ref={searchRef} value={query} placeholder="按名称 / 标签搜索素材中心…"
            onChange={(event) => setQuery(event.target.value)} aria-label="搜索素材中心" />
          <button type="submit" className="btn-secondary" disabled={busy}>搜索</button>
          <button type="button" className="stack-monitor-refresh" title="刷新" aria-label="刷新素材中心"
            onClick={() => void load(query)}><RefreshCw size={13} /></button>
        </form>
        {error && <p role="alert" className="blocker-reason">{error}</p>}
        {busy && <p className="material-hint">正在读取素材中心…</p>}
        {!busy && !error && items.length === 0 && <p className="material-hint">{emptyHint}</p>}
        {!busy && items.length > 0 && <div className="asset-picker-grid" role="listbox" aria-label="素材中心可选素材">
          {items.map((item) => {
            const view = display(item);
            return <button type="button" role="option" aria-selected="false" className="asset-picker-card"
              key={view.key} onClick={() => onPick(item)} title={`选择 ${view.title}`}>
              {view.mediaUrl
                ? <img src={view.mediaUrl} alt={view.title} loading="lazy" />
                : <span className="asset-picker-noimg"><ImageOff size={20} /></span>}
              <b>{view.title}</b>
              <small>{view.subtitle}</small>
            </button>;
          })}
        </div>}
      </div>
      <footer className="editor-actions">
        <span className="material-hint">点选一项即导入并绑定到该素材需求；可分配素材须已在素材中心晋升且权属 approved。</span>
        <button type="button" className="btn-secondary" onClick={onClose}>取消</button>
      </footer>
    </div>
  </div>;
}

export default AssetCenterPicker;