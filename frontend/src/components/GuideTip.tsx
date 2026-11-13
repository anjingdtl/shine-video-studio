import { Lightbulb } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * 新手指引：贴在「必须由人点一下」的按钮旁边。
 *
 * 和已有的说明文字分开：正常说明用橄榄色/灰色，指引统一用靛蓝色虚线胶囊，
 * 只出现在需要点击的位置，避免和普通提示抢注意力。
 */
export function GuideTip({ children, tight }: { children: ReactNode; tight?: boolean }) {
  return <span className={`guide-tip${tight ? ' tight' : ''}`} role="note">
    <Lightbulb size={11} aria-hidden="true" />
    <span>{children}</span>
  </span>;
}

export default GuideTip;
