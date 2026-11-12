/** 反例（2026-11-12 穿测）：分镜草案链在后台跑，但四阶段面板没有任何进度显示。
 *  草案是一条同步长请求，单元台账整卷结束才写；面板必须直接读 crew 任务
 *  （film-runs）展示逐工种进度，刷新后同样可见，且草案按钮防重入。 */
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FourStagePanel } from '../components/FourStagePanel';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;
const PROJECT = 'proj-1';

function overview() {
  return {
    master: { document_id: 'doc-1', sha256: 'a'.repeat(64) },
    volume_plan_confirmed: true, bible_confirmed: true,
    volume_plan: { artifact_id: 'plan-1', sha256: 'b'.repeat(64), volumes: [] },
    bible_status: { artifact_id: 'bible-1', sha256: 'c'.repeat(64), blockers: [] },
    pending_bible: null, pending_scripts: [],
    volumes: [{
      volume_id: 'v1', order: 1, title: '第1卷', char_count: 100, outline_confirmed: true,
      scripts: [{ section_id: 's1', scene_key: 'S1', title: '出海', confirmed: true }],
      scripts_confirmed: 1, scripts_total: 1,
      storyboard: { status: null, updated_at: null, units: [] },
      episode_contracts: [], handoff: null,
    }],
  };
}

beforeEach(() => { stub = new StudioStub([]); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

describe('四阶段面板：分镜草案链的可见进度', () => {
  it('crew 任务在跑时显示逐工种进度，草案按钮防重入', async () => {
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/documents$/,
      respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/pipeline$/,
      respond: () => jsonResponse(overview()) });
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/film-runs$/,
      respond: () => jsonResponse([
        { id: 't3', role_id: 'cinematographer', status: 'RUNNING',
          created_at: '2026-11-12T04:33:05', trace: { phase: 'WAITING_PROVIDER' } },
        { id: 't2', role_id: 'director', status: 'SUCCEEDED',
          created_at: '2026-11-12T04:32:39', trace: {} },
        { id: 't1', role_id: 'scriptwriter', status: 'SUCCEEDED',
          created_at: '2026-11-12T04:31:10', trace: {} },
      ]) });
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    const strip = await screen.findByRole('status', { name: '分镜草案后台进度' });
    expect(strip).toHaveTextContent('分镜草案后台生成中');
    expect(strip).toHaveTextContent('摄影');
    expect(strip).toHaveTextContent('等待服务商返回完整结果');
    expect(screen.getByRole('button', { name: '生成分镜草案' })).toBeDisabled();
  });

  it('crew 全部完成且无活跃任务时不显示进度条', async () => {
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/documents$/,
      respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/pipeline$/,
      respond: () => jsonResponse(overview()) });
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/film-runs$/,
      respond: () => jsonResponse([
        { id: 't1', role_id: 'editor', status: 'SUCCEEDED',
          created_at: '2026-11-12T04:30:58', trace: {} },
      ]) });
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByRole('button', { name: '生成分镜草案' });
    expect(screen.queryByRole('status', { name: '分镜草案后台进度' })).not.toBeInTheDocument();
  });
});