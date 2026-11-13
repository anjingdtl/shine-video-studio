import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { StrategyResourceCard } from '../components/StrategyResourceCard';

const api = vi.fn();
const approve = vi.fn();
vi.mock('../api', () => ({ api: (...args: unknown[]) => api(...args), fetchRuntimeStatus: () => Promise.resolve({ installation_id: 'inst' }) }));
vi.mock('../setupExecution', () => ({
  approveAndExecuteSetupPlan: (...args: unknown[]) => approve(...args),
  isQueuedInstallation: () => false, fetchInstallationAttempt: vi.fn(),
  pauseInstallationAttempt: vi.fn(), resumeInstallationAttempt: vi.fn(),
}));
const strategy = { schema_version: '1' as const, generation_mode: 'Ref2VA' as const,
  model_variant_id: 'ref2va-lowend-candidate', accelerator_id: 'native20',
  second_pass: { enabled: false }, upscale: { enabled: false } };

beforeEach(() => { api.mockReset(); approve.mockReset(); localStorage.clear(); });

it('previews fixed resources before the explicit download confirmation', async () => {
  api.mockImplementation((path: string) => {
    if (path.endsWith('/resolve')) return Promise.resolve({ components: [{ component_id: 'weight', size_bytes: 10,
      state: 'MISSING', license_id: 'license', license_url: 'https://example.com/license', files: [] }],
      missing_download_bytes: 10, snapshot_sha256: 'a'.repeat(64), download_eligible: true, blockers: [] });
    if (path.endsWith('/storage')) return Promise.resolve({ free_bytes: 1000 });
    if (path.endsWith('/install-plan')) return Promise.resolve({ plan_id: 'plan', sha256: 'hash',
      status: 'AWAITING_APPROVAL', download_bytes: 10, disk_required: 20 });
    throw new Error(path);
  });
  approve.mockResolvedValue({ status: 'QUEUED' });
  render(<StrategyResourceCard projectId="p" strategy={strategy} />);
  fireEvent.click(screen.getByText('检测所需资源'));
  await screen.findByText('确认并下载上述资源');
  expect(approve).not.toHaveBeenCalled();
  expect(screen.getByText(/候选模型下载完成后仍不能直接开拍/)).toBeTruthy();
  fireEvent.click(screen.getByText('确认并下载上述资源'));
  await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
});
