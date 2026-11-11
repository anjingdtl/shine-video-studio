import { useEffect, useRef, useState, type ReactNode } from 'react';

/** 页内 armed 二次确认（设计方案 §5.4，替代阻塞整 tab 的 window.confirm）：
 *  第一次点击按钮变红显示「再点一次确认…」，timeoutMs 内未确认自动复原；
 *  第二次点击才真正执行。onConfirm 闭包每次渲染取最新值。 */
export function useArmedAction(onConfirm: () => void, timeoutMs = 3000) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | null>(null);
  const confirmRef = useRef(onConfirm);
  confirmRef.current = onConfirm;
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);
  const trigger = () => {
    if (armed) {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      setArmed(false);
      confirmRef.current();
    } else {
      setArmed(true);
      timer.current = window.setTimeout(() => { setArmed(false); timer.current = null; }, timeoutMs);
    }
  };
  return { armed, trigger };
}

export function ArmedButton({ label, armedLabel = '再点一次确认', onConfirm, disabled, icon, className = 'btn-danger' }: {
  label: string;
  armedLabel?: string;
  onConfirm: () => void;
  disabled?: boolean;
  icon?: ReactNode;
  className?: string;
}) {
  const { armed, trigger } = useArmedAction(onConfirm);
  return (
    <button type="button" className={`${className}${armed ? ' is-armed' : ''}`} disabled={disabled} onClick={trigger}>
      {icon}<span>{armed ? armedLabel : label}</span>
    </button>
  );
}