import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Cpu, RefreshCw } from 'lucide-react';
import { fetchHardwareTelemetry, type GpuTelemetry, type HardwareTelemetry } from '../api';

const POLL_INTERVAL_MS = 3000;
const HISTORY_LENGTH = 20;

function formatPct(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value)}%`;
}

function formatGbPair(usedMb: number | null | undefined, totalMb: number | null | undefined): string {
  if (usedMb == null || totalMb == null || totalMb <= 0) return '—';
  return `${(usedMb / 1024).toFixed(1)}/${(totalMb / 1024).toFixed(1)} GB`;
}

function formatTemp(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value)}°C`;
}

function formatPower(draw: number | null | undefined, limit?: number | null | undefined): string {
  if (draw == null) return '—';
  if (limit != null && limit > 0) return `${Math.round(draw)}/${Math.round(limit)}W`;
  return `${Math.round(draw)}W`;
}

function toneForPct(value: number | null | undefined): string {
  if (value == null) return '';
  if (value >= 90) return 'danger';
  if (value >= 75) return 'warn';
  return '';
}

function shortGpuName(name: string): string {
  return name.replace(/^NVIDIA\s+GeForce\s+/i, '').replace(/^NVIDIA\s+/i, '').trim() || name;
}

function pushSample(prev: number[], next: number | null | undefined): number[] {
  if (next == null) return prev;
  const clamped = Math.min(100, Math.max(0, next));
  const combined = [...prev, clamped];
  return combined.length > HISTORY_LENGTH ? combined.slice(combined.length - HISTORY_LENGTH) : combined;
}

function formatClock(iso: string | undefined): string {
  if (!iso) return '--:--:--';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '--:--:--';
  return parsed.toTimeString().slice(0, 8);
}

/**
 * 轻量实时趋势折线（固定 0–100% 纵轴），每次 3s 采样推入最新点实现连续动态变化。
 */
function TrendSparkline({ values, label, tone }: {
  values: number[];
  label: string;
  tone?: string;
}) {
  const gradId = useId().replace(/:/g, '');
  const width = 180;
  const height = 42;
  const samples = values.length >= 2 ? values : values.length === 1 ? [values[0], values[0]] : [0, 0];
  const step = width / (HISTORY_LENGTH - 1);
  const startX = width - (samples.length - 1) * step;
  const pts = samples.map((val, idx) => {
    const x = +(startX + idx * step).toFixed(1);
    const y = +(height - 3 - (val / 100) * (height - 6)).toFixed(1);
    return `${x},${y}`;
  });
  const polyline = pts.join(' ');
  const firstX = startX.toFixed(1);
  const area = `${firstX},${height} ${polyline} ${width},${height}`;
  const lastPoint = pts[pts.length - 1].split(',');

  return <svg className={`hw-spark ${tone ?? ''}`} viewBox={`0 0 ${width} ${height}`}
    preserveAspectRatio="none" role="img" aria-label={label}>
    <defs>
      <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="currentColor" stopOpacity="0.34" />
        <stop offset="100%" stopColor="currentColor" stopOpacity="0.02" />
      </linearGradient>
    </defs>
    <line x1="0" y1={height * 0.5} x2={width} y2={height * 0.5} className="hw-spark-grid" />
    {values.length > 0 && <>
      <polygon points={area} fill={`url(#${gradId})`} />
      <polyline points={polyline} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx={lastPoint[0]} cy={lastPoint[1]} r="2.2" fill="currentColor" />
    </>}
  </svg>;
}

/**
 * 左侧常驻的本机硬件监控：位于工作栈监控上方，每 3 秒自动采样一次，
 * 同屏展示 CPU 与 GPU 占用率/功耗/温度/趋势曲线以及内存占用率；
 * 读不到传感器或后端未更新时如实显示「—」，绝不伪造数值。
 */
export function HardwareMonitor() {
  const [data, setData] = useState<HardwareTelemetry | null>(null);
  const [cpuHistory, setCpuHistory] = useState<number[]>([]);
  const [gpuHistory, setGpuHistory] = useState<number[]>([]);
  const [offline, setOffline] = useState(false);
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState(true);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const value = await fetchHardwareTelemetry();
      if (!alive.current) return;
      setData(value);
      setOffline(false);
      setTick((count) => count + 1);
      setCpuHistory((prev) => pushSample(prev, value.cpu.utilization_pct));
      setGpuHistory((prev) => pushSample(prev, value.gpus[0]?.utilization_pct));
    } catch {
      if (alive.current) setOffline(true);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    const timer = window.setInterval(() => { void load(); }, POLL_INTERVAL_MS);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [load]);

  const cpuPct = data?.cpu.utilization_pct ?? null;
  const memPct = data?.memory.utilization_pct ?? null;
  const gpus: GpuTelemetry[] = data?.gpus ?? [];
  const primaryGpu: GpuTelemetry | undefined = gpus[0];
  const headline = data
    ? `CPU ${formatPct(cpuPct)} · GPU ${formatPct(primaryGpu?.utilization_pct)}`
    : '采样中';

  return <section className={`stack-monitor hardware-monitor${open ? ' open' : ''}`} aria-label="硬件监控">
    <button type="button" className="stack-monitor-head" onClick={() => setOpen((value) => !value)}
      aria-expanded={open}>
      <Cpu size={13} />
      <span>硬件资源</span>
      <em className={((cpuPct ?? 0) >= 80 || (primaryGpu?.utilization_pct ?? 0) >= 80) ? 'busy' : ''}>{headline}</em>
      <i className="stack-monitor-caret" aria-hidden="true">{open ? '−' : '+'}</i>
    </button>
    {open && <div className="stack-monitor-body hardware-monitor-body">
      <div className="stack-monitor-meta hardware-live-bar">
        <span className={`hw-live-pill${offline ? ' offline' : ''}`} title={`每 3 秒自动采样（已采样 ${tick} 次）`}>
          <i key={tick} className="hw-live-dot" aria-hidden="true" />
          3s 实时 · {formatClock(data?.checked_at)}
        </span>
        <button type="button" className="stack-monitor-refresh" title="立即刷新硬件监控" aria-label="刷新硬件监控"
          onClick={() => void load()}>
          <RefreshCw size={11} />
        </button>
      </div>

      {offline && <p className="stack-monitor-empty">
        硬件监控接口暂不可用（若刚更新代码，请重启本机后台生效）。
      </p>}

      <div className="hw-row hw-card">
        <div className="hw-row-head">
          <b>CPU</b>
          <span className="hw-pct">{cpuPct == null ? '—' : `${cpuPct.toFixed(1)}%`}</span>
        </div>
        <TrendSparkline values={cpuHistory} label="CPU 实时占用趋势" tone={toneForPct(cpuPct)} />
        <div className="hw-bar" aria-hidden="true">
          <i className={toneForPct(cpuPct)} style={{ width: `${Math.min(100, Math.max(0, cpuPct ?? 0))}%` }} />
        </div>
        <div className="hw-metrics">
          <span title={data?.cpu.temperature_c == null ? '当前主板未向 Windows 开放 ACPI 温度区；桌面端若需读取 CPU 核心温度，请运行 LibreHardwareMonitor 或 OpenHardwareMonitor' : undefined}>
            温度 {formatTemp(data?.cpu.temperature_c)}
          </span>
          <span title={data?.cpu.power_w == null ? '未检测到 CPU 能耗计数器（Energy Meter / RAPL）' : undefined}>
            功耗 {formatPower(data?.cpu.power_w)}
          </span>
        </div>
      </div>

      {gpus.length === 0 && <div className="hw-row hw-card">
        <div className="hw-row-head">
          <b>GPU</b>
          <span className="hw-pct">—</span>
        </div>
        <TrendSparkline values={[]} label="GPU 实时占用趋势" />
        <div className="hw-metrics">
          <span>温度 —</span>
          <span>功耗 —</span>
          <span>显存 —</span>
        </div>
        {data && <p className="stack-monitor-empty">
          {data.gpu_error ? '未检测到 NVIDIA GPU（nvidia-smi 不可用）。' : '未发现可用 GPU 设备。'}
        </p>}
      </div>}

      {gpus.map((gpu, idx) => <div className="hw-row hw-card" key={gpu.index}>
        <div className="hw-row-head">
          <b title={gpu.name}>{gpus.length > 1 ? `GPU${gpu.index}` : 'GPU'} · {shortGpuName(gpu.name)}</b>
          <span className="hw-pct">{formatPct(gpu.utilization_pct)}</span>
        </div>
        {idx === 0 && (
          <TrendSparkline values={gpuHistory} label="GPU 实时占用趋势" tone={toneForPct(gpu.utilization_pct)} />
        )}
        <div className="hw-bar" aria-hidden="true">
          <i className={toneForPct(gpu.utilization_pct)}
            style={{ width: `${Math.min(100, Math.max(0, gpu.utilization_pct ?? 0))}%` }} />
        </div>
        <div className="hw-metrics">
          <span>温度 {formatTemp(gpu.temperature_c)}</span>
          <span>功耗 {formatPower(gpu.power_w, gpu.power_limit_w)}</span>
          <span>显存 {formatGbPair(gpu.memory_used_mb, gpu.memory_total_mb)}</span>
        </div>
      </div>)}

      <div className="hw-row hw-card hw-mem-card">
        <div className="hw-row-head">
          <b>内存</b>
          <span>
            {formatGbPair(data?.memory.used_mb, data?.memory.total_mb)} ({formatPct(memPct)})
          </span>
        </div>
        <div className="hw-bar" aria-hidden="true">
          <i className={toneForPct(memPct)} style={{ width: `${Math.min(100, Math.max(0, memPct ?? 0))}%` }} />
        </div>
      </div>
    </div>}
  </section>;
}

export default HardwareMonitor;
