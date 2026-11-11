/** P2 反馈体系契约（设计方案 §5）：
 *  - armed 二次确认：第一次点击不执行，第二次才执行；超时自动复原。
 *  - toast 队列：重复错误合并计数；error 常驻、success 自动消失。 */
import { render, screen, act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { ArmedButton } from '../components/ArmedButton';
import { useStudio } from '../useStudio';
import { StudioStub, baseRoutes } from './studioStub';

describe('armed 二次确认', () => {
  it('第一次点击不执行，第二次才执行', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<ArmedButton label="取消整部生产" onConfirm={onConfirm} />);
    const button = screen.getByRole('button', { name: '取消整部生产' });
    await user.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '再点一次确认' })).toHaveClass('is-armed');
    await user.click(screen.getByRole('button', { name: '再点一次确认' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('超时未确认自动复原，不执行', async () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    render(<ArmedButton label="放弃任务" onConfirm={onConfirm} />);
    act(() => { screen.getByRole('button', { name: '放弃任务' }).click(); });
    expect(screen.getByRole('button', { name: '再点一次确认' })).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(3100); });
    expect(screen.getByRole('button', { name: '放弃任务' })).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});

describe('toast 队列', () => {
  let stub: StudioStub;
  beforeEach(() => { stub = new StudioStub(baseRoutes()); vi.useFakeTimers(); });
  afterEach(() => { stub.restore(); vi.useRealTimers(); });

  it('重复错误合并计数，error 常驻；success 4 秒后消失', () => {
    const { result } = renderHook(() => useStudio());
    act(() => { result.current.report('同一个错误', 'error'); });
    act(() => { result.current.report('同一个错误', 'error'); });
    expect(result.current.notices).toHaveLength(1);
    expect(result.current.notices[0].count).toBe(2);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.notices).toHaveLength(1); // error 常驻
    act(() => { result.current.report('操作成功', 'success'); });
    expect(result.current.notices).toHaveLength(2);
    act(() => { vi.advanceTimersByTime(4100); });
    expect(result.current.notices.some((t) => t.kind === 'success')).toBe(false);
    expect(result.current.notices.some((t) => t.kind === 'error')).toBe(true);
  });
});