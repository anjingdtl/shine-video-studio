/** 轻量的故障上下文总线：把页面里真实发生的失败带到全局 AI 管家。
 *
 * 只传受控标识（页面、项目、任务、尝试、错误码和一句具名原因），不抓取
 * DOM、不复制整页文本，也不新建第二套全局错误台账——错误码仍以服务端与
 * 原任务记录为准。 */
export type CopilotFault = {
  projectId?: string;
  jobId?: string;
  attemptId?: string;
  taskId?: string;
  errorCode?: string;
  label?: string;
  at: number;
};

type Listener = (fault: CopilotFault | null) => void;

let current: CopilotFault | null = null;
const listeners = new Set<Listener>();

export function reportCopilotFault(fault: Omit<CopilotFault, 'at'>): void {
  current = { ...fault, at: Date.now() };
  listeners.forEach((listener) => listener(current));
}

export function clearCopilotFault(): void {
  current = null;
  listeners.forEach((listener) => listener(null));
}

export function currentCopilotFault(): CopilotFault | null {
  return current;
}

export function subscribeCopilotFault(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 一组故障绑定的稳定键：同一项目/任务/错误码恢复同一段会话。 */
export function faultKey(fault: CopilotFault | null): string {
  if (!fault) return 'none.none.none';
  const job = fault.jobId || fault.taskId || 'none';
  const code = fault.errorCode || 'none';
  return `${job}.${fault.attemptId || 'none'}.${code}`;
}

/** 一句可读的绑定说明，显示在管家标题栏，避免用户误以为还在诊断上一个问题。 */
export function faultLabel(fault: CopilotFault | null): string | null {
  if (!fault) return null;
  const parts: string[] = [];
  if (fault.label) parts.push(fault.label);
  if (fault.errorCode) parts.push(fault.errorCode);
  if (fault.jobId) parts.push(`任务 ${fault.jobId.slice(0, 8)}…`);
  else if (fault.taskId) parts.push(`任务 ${fault.taskId.slice(0, 8)}…`);
  return parts.length ? parts.join(' · ') : null;
}
