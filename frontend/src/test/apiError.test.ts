import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, json } from '../api';

/** The audit sample saw `[object Object]` where a reason should be. */
describe('接口错误文案', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  function respond(status: number, body: unknown) {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }));
  }

  it('message 是对象时给出可读文案，而不是 [object Object]', async () => {
    respond(409, { detail: { code: 'SETUP_OPERATION_FAILED', message: { nested: ['a', 'b'] } } });
    const error = await api('/setup/anything').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).not.toContain('[object Object]');
    expect((error as ApiError).message).not.toContain('undefined');
  });

  it('详情是数组时逐项说明字段与原因', async () => {
    respond(422, { detail: [{ loc: ['body', 'title'], msg: '不能为空', type: 'missing' }] });
    const error = await api('/projects', json('POST', {})).catch((caught: unknown) => caught);
    expect((error as ApiError).message).toContain('项目名称');
    expect((error as ApiError).message).not.toContain('[object Object]');
  });

  it('服务端内部错误给出中文说明，而不是裸英文 500', async () => {
    respond(500, { detail: 'Internal Server Error' });
    const error = await api('/asset-center/assignments').catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).not.toContain('Internal Server Error');
    expect((error as ApiError).message).toContain('服务端内部错误');
    expect((error as ApiError).message).toContain('没有完成');
  });

  it('安装目录排队状态有面向用户的说明', async () => {
    respond(409, { detail: 'INSTALL_TARGET_CONFLICT' });
    const error = await api('/comfyops/install-plans/x/execute').catch((caught: unknown) => caught);
    expect((error as ApiError).message).toContain('已自动排队等待');
  });

  it('人物重名拒绝带名字后缀时给出中文说明与下一步，而不是裸码串', async () => {
    respond(409, { detail: 'CHARACTER_NAME_ALREADY_IN_USE:林岚' });
    const error = await api('/projects/p/storyboard/character-binding/apply', json('POST', {}))
      .catch((caught: unknown) => caught);
    const message = (error as ApiError).message;
    expect(message).toContain('林岚');
    expect(message).toContain('另一张人物素材');
    expect(message).toContain('改一个不重复的名字');
    expect(message).not.toBe('CHARACTER_NAME_ALREADY_IN_USE:林岚');
  });

  it('人物重名裸码也有兜底说明', async () => {
    respond(409, { detail: 'CHARACTER_NAME_ALREADY_IN_USE' });
    const error = await api('/projects/p/storyboard/character-binding/apply', json('POST', {}))
      .catch((caught: unknown) => caught);
    expect((error as ApiError).message).toContain('一个名字只能对应一张人物素材');
  });
});
