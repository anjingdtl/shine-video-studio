import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import OutputDirectoryPanel from '../components/OutputDirectoryPanel';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const STATE = {
  root: 'F:\\workspace\\.local-data',
  default_root: 'F:\\workspace\\.local-data',
  custom: false,
  layout: { material: '素材', video: '视频', voice: '音色', material_center: '素材中心' },
  projects: [{ project_id: 'p1', title: '雾港夜账', folder: '雾港夜账' }],
  file_count: 3,
  byte_size: 1024,
  free_gb: 88,
  migration: null,
  blockers: [],
};

beforeEach(() => {
  stub = new StudioStub([
    { method: 'GET', pattern: /\/settings\/output-directory$/, respond: () => jsonResponse(STATE) },
    {
      method: 'PUT', pattern: /\/settings\/output-directory$/,
      respond: () => jsonResponse({
        root: 'D:\\ShineOutput', custom: true, moved: 2, copied: 2,
        bytes_copied: 2048, deleted: 2, kept: [], duration_s: 0.2,
      }),
    },
  ]);
});

afterEach(() => {
  stub.restore();
});

describe('作品输出目录', () => {
  it('shows the project/type layout and migrates when the directory is saved', async () => {
    const user = userEvent.setup();
    render(<OutputDirectoryPanel />);

    expect(await screen.findByText(/素材、镜头视频、成片和音色/)).toBeInTheDocument();
    expect(screen.getByText('雾港夜账')).toBeInTheDocument();
    expect(screen.getAllByText('素材中心').length).toBeGreaterThan(0);

    const input = screen.getByLabelText('作品输出目录');
    await user.clear(input);
    await user.type(input, 'D:\\ShineOutput');
    await user.click(screen.getByRole('button', { name: '保存并迁移' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已迁移 2 个文件'));
    const put = stub.callsOf('PUT', /output-directory$/);
    expect(put).toHaveLength(1);
    expect(put[0].body).toEqual({ path: 'D:\\ShineOutput' });
  });

  it('does not offer a save path when production is active', async () => {
    stub.add({
      method: 'GET', pattern: /\/settings\/output-directory$/,
      respond: () => jsonResponse({ ...STATE, blockers: ['1 个镜头任务待执行或正在生成'] }),
    });
    render(<OutputDirectoryPanel />);
    expect(await screen.findByText(/现在还不能迁移/)).toHaveTextContent('1 个镜头任务待执行或正在生成');
  });
});
