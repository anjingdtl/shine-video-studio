import { History, RefreshCw } from 'lucide-react';
import type { AuditPage } from '../api';

type Props = {
  page: AuditPage | null;
  busy: boolean;
  onLoad: () => void;
  onMore: () => void;
};

export default function AuditPanel({ page, busy, onLoad, onMore }: Props) {
  return (
    <section className="card section-card" id="audit">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">06 / AUDIT TRAIL</div>
          <h2>项目级审计追踪</h2>
          <p>按项目检索创建、素材审批、风格冻结、分镜修改、任务执行、QC 与返工事件。</p>
        </div>
        <button className="icon-button" title="刷新审计" onClick={onLoad} disabled={busy}><RefreshCw size={14} /></button>
      </div>
      {!page ? <div className="empty-inline">加载审计事件…</div> : <>
        <div className="audit-meta"><History size={12} /> 共 {page.total} 条 · 当前显示 {page.items.length}</div>
        <div className="audit-list">
          {page.items.map((item) => (
            <div className="audit-row" key={item.id}>
              <span className="audit-time">{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
              <span className="audit-actor">{item.actor}</span>
              <b>{item.event_type}</b>
              <span className="audit-entity">{item.entity_type} · {item.entity_id.slice(0, 8)}</span>
            </div>
          ))}
          {page.items.length === 0 && <div className="empty-inline">尚无审计事件。</div>}
        </div>
        {page.items.length < page.total && <button className="secondary-button load-more" disabled={busy} onClick={onMore}>加载更多</button>}
      </>}
    </section>
  );
}
