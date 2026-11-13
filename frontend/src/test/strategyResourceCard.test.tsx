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

it('offers hash-checked local reuse before downloading', async () => {
  let imported = false;
  api.mockImplementation((path: string) => {
    if (path.endsWith('/resolve')) return Promise.resolve({ components: [{ component_id: 'weight', size_bytes: 10,
      state: imported ? 'HASH_VERIFIED' : 'MISSING', license_id: 'license',
      license_url: 'https://example.com/license', files: [] }], missing_download_bytes: imported ? 0 : 10,
      snapshot_sha256: 'a'.repeat(64), download_eligible: true, blockers: [] });
    if (path.endsWith('/storage')) return Promise.resolve({ free_bytes: 1000 });
    if (path.endsWith('/inspect-local')) return Promise.resolve({ snapshot_sha256: 'b'.repeat(64),
      candidate_bytes: imported ? 0 : 10, candidates: imported ? [] : [{ component_id: 'weight', size_bytes: 10 }] });
    if (path.endsWith('/import-local')) { imported = true; return Promise.resolve({ reused_bytes: 10, rejected: [] }); }
    if (path.endsWith('/install-plan')) return Promise.resolve({ plan_id: 'plan', sha256: 'hash',
      status: 'AWAITING_APPROVAL', download_bytes: 10, disk_required: 20 });
    throw new Error(path);
  });
  render(<StrategyResourceCard projectId="p" strategy={strategy} />);
  fireEvent.click(screen.getByText('检测所需资源'));
  const reuse = await screen.findByText('校验并复用本机文件');
  expect(api.mock.calls.some(call => String(call[0]).endsWith('/import-local'))).toBe(false);
  fireEvent.click(reuse);
  await screen.findByText(/已校验并复用/);
  expect(api.mock.calls.filter(call => String(call[0]).endsWith('/import-local'))).toHaveLength(1);
  expect(approve).not.toHaveBeenCalled();
});

it('does not start two local imports on repeated clicks', async () => {
  let finish: ((value: unknown) => void) | undefined;
  api.mockImplementation((path: string) => {
    if (path.endsWith('/resolve')) return Promise.resolve({ components: [], missing_download_bytes: 10,
      snapshot_sha256: 'a'.repeat(64), download_eligible: true, blockers: [] });
    if (path.endsWith('/storage')) return Promise.resolve({ free_bytes: 1000 });
    if (path.endsWith('/inspect-local')) return Promise.resolve({ snapshot_sha256: 'b'.repeat(64),
      candidate_bytes: 10, candidates: [{ component_id: 'weight', size_bytes: 10 }] });
    if (path.endsWith('/import-local')) return new Promise(resolve => { finish = resolve; });
    if (path.endsWith('/install-plan')) return Promise.resolve({ plan_id: 'plan', sha256: 'hash',
      status: 'AWAITING_APPROVAL', download_bytes: 10, disk_required: 20 });
    throw new Error(path);
  });
  render(<StrategyResourceCard projectId="p" strategy={strategy} />);
  fireEvent.click(screen.getByText('检测所需资源'));
  const reuse = await screen.findByText('校验并复用本机文件');
  fireEvent.click(reuse);
  fireEvent.click(reuse);
  expect(api.mock.calls.filter(call => String(call[0]).endsWith('/import-local'))).toHaveLength(1);
  finish?.({ reused_bytes: 10, rejected: [] });
  await screen.findByText(/已校验并复用/);
});
