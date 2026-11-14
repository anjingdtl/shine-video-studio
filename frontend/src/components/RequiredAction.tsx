import type { ReactNode } from 'react';

export type RequiredActionSpec = {
  /** 要做什么：一句话说明这一步要完成的动作。 */
  what: string;
  /** 为什么必须做：不完成会挡住什么。 */
  why?: string;
  /** 可点的下一步。 */
  action?: { label: string; go: () => void };
  /** 额外说明（可选）。 */
  extra?: ReactNode;
};

/**
 * 必做动作块：说清「这一步要做什么」与「为什么必须做」，并给一个可点的下一步。
 *
 * 导演模式与专业模式共用这一块：AGENTS.md 的口径是「用户必须做的每个动作都要响」，
 * 裸错误码或一句没有出口的原因都不算数。
 */
export function RequiredAction({ what, why, action, extra }: RequiredActionSpec) {
  return (
    <div className="required-action" role="note">
      <p><b>这一步要做什么：</b>{what}</p>
      {why && <p><b>为什么必须做：</b>{why}</p>}
      {extra}
      {action && (
        <button type="button" className="must-button" onClick={action.go}>{action.label}</button>
      )}
    </div>
  );
}

/**
 * 模块引导块：每个模块都带一句短的说明，告诉人这一步要做什么、为什么必须做。
 */
export function ModuleGuide({ what, why }: { what: string; why?: string }) {
  return (
    <div className="module-guide">
      <b>这一步要做什么：</b>{what}
      {why && <span>为什么必须做：{why}</span>}
    </div>
  );
}
