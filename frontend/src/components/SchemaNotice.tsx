import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

type Health = { schema_revision?: string | null; schema_head?: string | null; schema_up_to_date?: boolean | null };

/**
 * 本机服务的数据库版本落后于程序时如实提示。
 *
 * 旧服务没有重启或数据库没有升级时，新功能的接口会直接 404；没有这条提示，
 * 页面看起来只是“某个按钮不工作”。数据不会因此删除，重新用一键启动即可完成升级。
 * 读不到版本信息时不显示任何结论。
 */
export function SchemaNotice() {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    let alive = true;
    fetch('/health')
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => { if (alive && body) setHealth(body as Health); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);
  if (!health || health.schema_up_to_date !== false) return null;
  return <div className="blocked-callout schema-notice" role="alert">
    <AlertTriangle size={14} />
    <span>
      本机服务的数据库版本（{health.schema_revision ?? '未知'}）落后于当前程序（{health.schema_head ?? '未知'}），
      部分新功能会不可用。请关闭工作台后用「一键启动」重新启动以完成升级；已有作品与数据不会删除。
    </span>
  </div>;
}
