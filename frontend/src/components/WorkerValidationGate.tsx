import { useState } from 'react';
import { startWorker, stopWorkerAndWaitOffline } from '../api';

/** 服务核验返回 STOP_WORKER_BEFORE_SERVICE_VALIDATION 时的内联入口：
 *  一键停 Worker 并重试验证；验证完成后一键恢复。停止走监督进程身份核验，
 *  有活工作会被拒绝，不会打断在途渲染。 */
export function WorkerValidationGate({ onRetry }: { onRetry: () => void }) {
  const [busy, setBusy] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [note, setNote] = useState('');
  return (
    <div className="blocked-callout" role="status">
      {!stopped ? (
        <>
          <span>重新验证需要先停止后台 Worker（API 保持运行）。</span>
          <button type="button" className="btn-secondary" disabled={busy}
            onClick={() => void (async () => {
              setBusy(true); setNote('');
              try {
                await stopWorkerAndWaitOffline();
                setStopped(true);
                onRetry();
              } catch (error) {
                setNote(error instanceof Error ? error.message : String(error));
              } finally { setBusy(false); }
            })()}>
            {busy ? '正在停止 Worker…' : '停止 Worker 并继续验证'}
          </button>
        </>
      ) : (
        <>
          <span>Worker 已停止；验证完成后记得恢复。</span>
          <button type="button" className="btn-secondary" disabled={busy}
            onClick={() => void (async () => {
              setBusy(true); setNote('');
              try {
                const result = await startWorker();
                setNote(result.code ? `恢复被拒绝：${result.detail || result.code}` : 'Worker 已恢复。');
              } catch (error) {
                setNote(error instanceof Error ? error.message : String(error));
              } finally { setBusy(false); }
            })()}>
            恢复 Worker
          </button>
        </>
      )}
      {note && <span>{note}</span>}
    </div>
  );
}

export default WorkerValidationGate;