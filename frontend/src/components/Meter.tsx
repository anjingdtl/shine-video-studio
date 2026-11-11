/** 统一进度组件（设计方案 §2.3）：所有阶段完成度/任务进度复用 .meter，
 *  禁止各面板自绘进度。保留 progressbar 角色与 aria-label，无障碍查询不变。 */
export function Meter({ value, max = 100, label, count, tone }: {
  value: number;
  max?: number;
  label?: string;
  count?: string;
  tone?: 'ok' | 'busy';
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className={`meter${tone ? ` is-${tone}` : ''}`} role="progressbar"
      aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label}>
      <span className="meter-track"><span className="meter-fill" style={{ width: `${pct}%` }} /></span>
      {count && <span className="meter-count">{count}</span>}
    </div>
  );
}

export default Meter;