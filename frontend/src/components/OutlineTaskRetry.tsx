import { ERROR_CODE_LABELS } from '../api';
import { failureLabels } from '../constants';

export type OutlineRetry = {
  failure: { code: string; scene_key?: string | null; outcome?: string | null; http_status?: number | null;
    provider_code?: number | null } | null;
  can_retry: boolean;
  unknown_subtask_id: string | null;
  exhausted: boolean;
};

export function OutlineTaskRetry({ retry, busy, disabled = false, onRetry }: {
  retry: OutlineRetry;
  busy: boolean;
  disabled?: boolean;
  onRetry: (fields: { subtask_id?: string; evidence?: string }) => void;
}) {
  const failure = retry.failure;
  if (!failure) return null;
  const reason = ERROR_CODE_LABELS[failure.code] ?? failureLabels[failure.code] ?? '本次任务未完成，请按错误代码查看详情。';
  return <div className="outline-retry">
    <p role="status"><strong>上一次失败{failure.scene_key ? `（${failure.scene_key}）` : ''}</strong> · <code>{failure.code}</code>
      {failure.http_status != null && ` · HTTP ${failure.http_status}`}
      {failure.provider_code != null && ` · 服务商错误码 ${failure.provider_code}`} · {reason}
      {failure.outcome === 'UNKNOWN' && ' · 服务商结果未知'}</p>
    {retry.can_retry && <button type="button" className="btn-primary" disabled={busy || disabled} onClick={() => {
      if (!retry.unknown_subtask_id) { onRetry({}); return; }
      if (window.confirm('上一次模型请求的结果仍未知，发起一次新请求可能多计一次调用。确认重试？')) {
        onRetry({ subtask_id: retry.unknown_subtask_id, evidence: '本机用户确认对结果未知的子任务发起一次新请求' });
      }
    }}>{busy ? '正在提交重试…' : '重试大纲任务'}</button>}
    {retry.exhausted && <p>已达到重试上限，旧请求记录保留供核对。</p>}
  </div>;
}
