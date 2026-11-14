/** D5 回归：最后阶段页尾的兜底文案写着「全部阶段已完成」，与同屏「0 / 7 阶段完成」矛盾。
 *
 * 根因链：JourneyBridge 只拿到 stages 数组与当前阶段，拿不到服务端 /journey 的完成态；
 * next 为 null 只表示「没有下一阶段」，却被写成了「全部阶段已完成」——
 * 于是「全部完成」这个结论由前端凭空推断出来，正好违反产品口径
 * 「没有服务端状态时不得显示全部完成」。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Film, Scissors, Wand2 } from 'lucide-react';

import { JourneyBridge } from '../components/JourneyRail';

const stages = [
  { id: 'creative', text: '创意与剧本', icon: Wand2, page: 'creative', hint: '写下或导入剧本' },
  { id: 'produce', text: '镜头拆解', icon: Scissors, page: 'produce', hint: '把剧本拆成镜头' },
  { id: 'deliver', text: '后期成片', icon: Film, page: 'deliver', hint: '装配并交付成片' },
];

function renderBridge(activeStageId: string) {
  return render(<JourneyBridge stages={stages} activeStageId={activeStageId}
    onNavigate={() => undefined} onHome={() => undefined} />);
}

describe('七阶段页尾衔接：没有下一阶段不等于全部完成', () => {
  it('停在最后阶段时不再宣布「全部阶段已完成」', () => {
    renderBridge('deliver');
    expect(screen.queryByText('全部阶段已完成')).toBeNull();
    expect(screen.getByText('已经是最后一个阶段')).toBeInTheDocument();
  });

  it('最后阶段的「下一阶段」按钮不再暗示交付已完成', () => {
    renderBridge('deliver');
    const nextButton = screen.getByRole('button', { name: '最后阶段' });
    expect(nextButton).toBeDisabled();
    expect(nextButton.getAttribute('title')).toBe('已经是最后一个阶段');
    expect(nextButton.getAttribute('title')).not.toContain('交付完成');
  });

  it('停在中间阶段时仍然指向真正的下一阶段', () => {
    renderBridge('creative');
    expect(screen.getByText('接下来：镜头拆解')).toBeInTheDocument();
    expect(screen.queryByText('全部阶段已完成')).toBeNull();
  });
});
