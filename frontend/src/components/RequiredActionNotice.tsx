import { ApiError } from '../api';
import { reportCopilotFault } from '../copilotContext';
import { describeBlocker, type ErrorActionTarget } from '../errorActions';
import { RequiredAction } from './RequiredAction';

/**
 * 服务端拒绝时可能只回一个裸错误码（detail 是字符串），也可能回 {code, message}。
 * 这里把错误码取出来：必做动作失败后，屏上不能只剩一串裸码。
 */
export function errorCodeOf(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  if (error.code) return error.code;
  const detail = error.detail;
  if (typeof detail === 'string' && /^[A-Z][A-Z0-9_]{3,}$/.test(detail)) return detail;
  if (detail && typeof detail === 'object') {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === 'string') return code;
  }
  return undefined;
}

/**
 * 必做动作失败后的必做块：说清「要做什么」「为什么必须做」，并给一个可点的下一步。
 * 目标键由装配层解析成真实跳转，前端不硬编码路由。
 */
export function RequiredActionNotice({ error, onGoStage }: {
  error: unknown;
  onGoStage?: (target: ErrorActionTarget) => void;
}) {
  const code = errorCodeOf(error);
  if (!code) return null;
  const message = error instanceof ApiError ? error.message : String(error ?? '');
  const described = describeBlocker(code, message);
  const action = described.action;
  if (!action || !onGoStage) return null;
  return (
    <RequiredAction
      what={described.reason}
      why={described.why}
      action={{ label: action.label, go: () => onGoStage(action.target) }}
      extra={
        <button
          type="button"
          className="btn-text"
          onClick={() => reportCopilotFault({ errorCode: code, label: described.reason })}
        >
          交给 AI 管家诊断
        </button>
      }
    />
  );
}
