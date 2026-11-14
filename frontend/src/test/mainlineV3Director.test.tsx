import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

/**
 * Mainline V3 WP3 (C-06a): one scope per screen.
 *
 * A deep link that names a director package (`#d/package?package=<id>`) is the
 * exact scope every projection on the screen must describe.  The journey used
 * to be fetched without the package id (silently describing the NEWEST
 * package) while the overview described the named one - two projections of the
 * same screen answering about different packages.
 */

const JOURNEY = {
  project_id: 'proj-1',
  stages: [
    { id: 'script', label: '创意与剧本', done: true, summary: '', blockers: [], facts: {} },
    { id: 'plan', label: '剧本统筹', done: true, summary: '', blockers: [], facts: {} },
    { id: 'storyboard', label: '镜头拆解', done: true, summary: '', blockers: [], facts: {} },
    { id: 'assets', label: '拍摄素材准备', done: true, summary: '', blockers: [], facts: {} },
    { id: 'package', label: '导演包', done: true, summary: '', blockers: [], facts: {} },
    { id: 'production', label: '视频生产', done: false, summary: '', blockers: [], facts: {} },
    { id: 'post', label: '后期成片', done: false, summary: '', blockers: [], facts: {} },
  ],
  next_stage: null,
};

const OVERVIEW = {
  project_id: 'proj-1', title: '雾港夜账',
  plan: { version: 1, sha256: 'a'.repeat(64), status: 'CONFIRMED', episode_count: 2 },
  style: { version: 1, sha256: 'b'.repeat(64) },
  counts: {
    episode_count: 2, confirmed_episode_count: 2, shootable_episode_count: 2,
    delivered_episode_count: 0, shot_count: 8, passed_shot_count: 0,
  },
  episodes: [], next_action: null, package: {
    package_id: 'pkg-deep', revision: 1, episode_count: 2, shot_count: 8, startable: true,
  },
};

let stub: StudioStub;

beforeEach(() => {
  window.history.replaceState(null, '', '#');
});
afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function addDirectorRoutes() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey/, respond: () => jsonResponse(JOURNEY) });
  stub.add({
    method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(OVERVIEW),
  });
}

describe('C-06a：深链指名的导演包是整个屏幕的唯一范围', () => {
  it('journey 与 overview 带同一个 package_id，不再一个看最新包、一个看指名包', async () => {
    stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
    addDirectorRoutes();
    window.history.replaceState(null, '', '#d/package?package=pkg-deep');

    render(<App />);

    await waitFor(() => expect(stub.callsOf('GET', /projects\/proj-1\/journey/).length).toBeGreaterThan(0), { timeout: 20000 });
    const journeyCalls = stub.callsOf('GET', /projects\/proj-1\/journey/);
    // 旧实现：journey 不带 package_id（描述最新包）；新实现：与 overview 同范围。
    expect(journeyCalls.some((call) => call.url.includes('package_id=pkg-deep'))).toBe(true);
    await waitFor(() => expect(stub.callsOf('GET', /projects\/proj-1\/director\/overview/).length).toBeGreaterThan(0), { timeout: 20000 });
    const overviewCalls = stub.callsOf('GET', /projects\/proj-1\/director\/overview/);
    expect(overviewCalls.some((call) => call.url.includes('package_id=pkg-deep'))).toBe(true);
  });

  it('没有深链时 journey 仍按默认范围（最新包）读取', async () => {
    stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
    addDirectorRoutes();

    render(<App />);

    await waitFor(() => expect(stub.callsOf('GET', /projects\/proj-1\/journey/).length).toBeGreaterThan(0), { timeout: 20000 });
    const journeyCalls = stub.callsOf('GET', /projects\/proj-1\/journey/);
    expect(journeyCalls.every((call) => !call.url.includes('package_id='))).toBe(true);
  });
});
