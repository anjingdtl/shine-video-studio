import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { api } from '../api';
import { Meter } from './Meter';
import type { MachineProgress, SetupSummary } from './SetupEnvironmentStatus';

/**
 * 基础配置页顶部的唯一机器进度条。
 *
 * 事实来源是 /setup/summary 的 machine_progress：服务端把整备任务、资源检索、
 * 下载 attempt 与就绪相位汇总成一个百分比和一句人话。这里只渲染，不另算一套
 * 判定，也不维护第二套状态机——卡片上的「下载安装缺文件」提交的是同一个整备
 * 任务，因此同一个进度条自然跟着动。100% 只对应唯一裁决的「真实制作已就绪」。
 */
export function MachineProgressBanner({ refreshEpoch = 0 }: { refreshEpoch?: number }) {
  const [progress, setProgress] = useState<MachineProgress | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const summary = await api<SetupSummary>('/setup/summary');
      if (mounted.current) setProgress(summary.machine_progress ?? null);
    } catch { /* 读不到时保留上一次真实投影，不谎报 */ }
  }, []);

  useEffect(() => { void load(); }, [load, refreshEpoch]);

  // 后台在动才跟读；静止时不空转请求。首读失败（超时/重启窗口）要继续重试，
  // 否则一次失败就把进度条永久藏起来。
  const unknown = progress === null;
  useEffect(() => {
    if (!progress?.active && !unknown) return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [progress?.active, unknown, load]);

  if (!progress) return null;
  const ready = !progress.active && progress.percent === 100;
  return (
    <section className="machine-progress" role="status" data-testid="machine-progress"
      aria-label={`机器整备进度 ${progress.percent}%`}>
      <Meter value={progress.percent} max={100} tone="spectrum"
        label={progress.label} count={`${progress.percent}%`} />
      <div className="machine-progress-text">
        <b>{ready ? `整备完成 · ${progress.label}` : progress.label}</b>
        <span>{progress.detail}</span>
      </div>
      {progress.active && (
        <span className="machine-progress-live" data-testid="setup-background">
          <Loader2 size={13} className="spin" /> 后台运作中
        </span>
      )}
    </section>
  );
}

export default MachineProgressBanner;