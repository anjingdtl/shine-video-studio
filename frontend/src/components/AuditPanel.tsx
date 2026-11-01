import { History, RefreshCw } from 'lucide-react';
import type { AuditPage } from '../api';
import { auditActorLabels, auditEntityLabels, auditEventLabels, label } from '../constants';

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
          <h2>操作记录（审计追踪）</h2>
          <p>谁、在什么时候、对什么、做了什么——每一步都留痕，可随时回查。</p>
        </div>
        <button className="icon-button" title="刷新操作记录" onClick={onLoad} disabled={busy}><RefreshCw size={14} /></button>
      </div>
      {!page ? <div className="empty-inline">加载操作记录…</div> : <>
        <div className="audit-meta"><History size={12} /> 共 {page.total} 条 · 当前显示 {page.items.length}</div>
        <div className="audit-list">
          {page.items.map((item) => (
            <div className="audit-row" key={item.id}>
              <span className="audit-time">{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
              <span className="audit-actor">{label(auditActorLabels, item.actor)}</span>
              <b>{label(auditEventLabels, item.event_type)}</b>
              <span className="audit-entity">{label(auditEntityLabels, item.entity_type)} · {item.entity_id.slice(0, 8)}…</span>
            </div>
          ))}
          {page.items.length === 0 && <div className="empty-inline">还没有操作记录。</div>}
        </div>
        {page.items.length < page.total && <button className="secondary-button load-more" disabled={busy} onClick={onMore}>加载更多</button>}
      </>}
    </section>
  );
}
