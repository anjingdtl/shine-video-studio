import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { AgentProgress } from '../components/AgentProgress';

it('按完成角色计数，并保留失败返回码和导演裁定状态', () => {
  render(<AgentProgress runs={[
    { id: 'a', role_id: 'director', status: 'RUNNING', created_at: '', trace: { tool: 'continuity_arbitration' } },
    { id: 'b', role_id: 'director', status: 'SUCCEEDED', created_at: '', trace: { http_status: 200, provider_code: 0 } },
    { id: 'c', role_id: 'director', status: 'REJECTED', created_at: '', error_code: 'LLM_ERROR',
      trace: { http_status: 200, provider_code: 1008 } },
    { id: 'd', role_id: 'scriptwriter', status: 'SUCCEEDED', created_at: '', trace: { http_status: 200 } },
  ]} />);
  expect(screen.getByRole('progressbar')).toHaveValue(2);
  expect(screen.getByText(/服务商代码 1008/)).toBeInTheDocument();
  expect(screen.getByText(/导演连续性裁定：进行中 · 尚无返回码/)).toBeInTheDocument();
});
