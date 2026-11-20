import { useCallback, useEffect, useState } from 'react';
import {
  acceptM4Delivery,
  assembleM4EpisodeDelivery,
  fetchM4DeliveryState,
  type M4DeliveryState,
  type M4Overview,
  type M4StageSummary,
} from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onMessage?: (msg: string, kind?: 'success' | 'error') => void;
};

export function DeliveryStagePage({ overview, stage, onRefresh, onMessage }: Props) {
  const projectId = overview.project.id;
  const [state, setState] = useState<M4DeliveryState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [jobOverridesByEp, setJobOverridesByEp] = useState<Record<string, Record<string, string>>>({});
  const [includeSubtitles, setIncludeSubtitles] = useState(true);
  const [burnSubtitles, setBurnSubtitles] = useState(false);
  const [watchedByDeliv, setWatchedByDeliv] = useState<Record<string, boolean>>({});
  const [evidenceByDeliv, setEvidenceByDeliv] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchM4DeliveryState(projectId);
      setState(data);
    } catch (err) {
      onMessage?.(`读取四阶成片状态失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId, onMessage]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyState = async (next: M4DeliveryState) => {
    setState(next);
    await onRefresh();
  };

  const handleAssemble = async (epId: string) => {
    setBusyKey(`asm:${epId}`);
    try {
      const overrides = jobOverridesByEp[epId] ?? {};
      const res = await assembleM4EpisodeDelivery(projectId, epId, {
        job_overrides: overrides,
        include_subtitles: includeSubtitles,
        burn_subtitles: burnSubtitles,
      });
      await applyState(res.state);
      onMessage?.(`已合成成片版本 v${res.delivery.version}（SHA ${res.delivery.sha256.slice(0, 10)}）。`, 'success');
    } catch (err) {
      onMessage?.(`合成失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleBatchAssemble = async () => {
    const eps = (state?.episodes ?? []).filter((e) => e.ready_to_assemble);
    if (eps.length === 0) return;
    setBusyKey('asm:all');
    try {
      for (const ep of eps) {
        const res = await assembleM4EpisodeDelivery(projectId, ep.id, {
          job_overrides: jobOverridesByEp[ep.id] ?? {},
          include_subtitles: includeSubtitles,
          burn_subtitles: burnSubtitles,
        });
        setState(res.state);
      }
      await onRefresh();
      onMessage?.(`已完成 ${eps.length} 集成片装配。`, 'success');
    } catch (err) {
      onMessage?.(`批量合成失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const handleAcceptance = async (deliveryId: string, sha256: string, decision: 'accepted' | 'rejected') => {
    setBusyKey(`acc:${deliveryId}`);
    try {
      const res = await acceptM4Delivery(projectId, deliveryId, {
        decision,
        watched: Boolean(watchedByDeliv[deliveryId]),
        evidence: evidenceByDeliv[deliveryId] ?? '',
        expected_sha256: sha256,
      });
      await applyState(res.state);
      onMessage?.(decision === 'accepted' ? '已记录正式人工验收通过。' : '已记录驳回意见。', 'success');
    } catch (err) {
      onMessage?.(`验收提交失败：${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const facts = stage?.facts ?? {};

  return (
    <section className="m4-panel" aria-label="产出成片">
      <div className="m4-panel-head">
        <div>
          <h2>④ 产出成片</h2>
          <p className="m4-panel-desc">
            按①冻结顺序从本次四阶已通过的真实视频产物生成 EDL、FFmpeg 装配、校验哈希并逐集观看验收。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          已验收 {String(facts.accepted_deliveries ?? 0)} / {String(facts.episodes ?? 0)} 集
        </span>
      </div>

      {stage && stage.blockers.length > 0 && (
        <div className="m4-blocker-box" role="status">
          {stage.blockers.map((b, idx) => (
            <div key={`${b.code}-${idx}`}>• {b.message}</div>
          ))}
        </div>
      )}

      <div className="m4-ep-card">
        <div className="m4-ep-head">
          <strong>成片装配设置</strong>
          <div className="m4-toolbar">
            <label className="m4-toolbar">
              <input
                type="checkbox"
                checked={includeSubtitles}
                onChange={(e) => setIncludeSubtitles(e.target.checked)}
              />
              生成外挂 SRT 字幕
            </label>
            <label className="m4-toolbar">
              <input
                type="checkbox"
                checked={burnSubtitles}
                disabled={!includeSubtitles}
                onChange={(e) => setBurnSubtitles(e.target.checked)}
              />
              烧录字幕进画面
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={busyKey !== null || !(state?.episodes ?? []).some((e) => e.ready_to_assemble)}
              onClick={() => void handleBatchAssemble()}
            >
              一键合成全部已就绪分集
            </button>
          </div>
        </div>
      </div>

      {loading && !state ? (
        <div className="m4-empty-box">正在加载四阶成片工作台...</div>
      ) : (
        (state?.episodes ?? []).map((ep) => (
          <div key={ep.id} className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>
                {ep.episode_code}：{ep.title || '未命名分集'}
              </strong>
              <button
                type="button"
                className="btn-primary"
                disabled={busyKey !== null || !ep.ready_to_assemble}
                onClick={() => void handleAssemble(ep.id)}
              >
                {ep.deliveries.length > 0 ? '合成新版本成片' : '合成当前集成片'}
              </button>
            </div>

            <div className="m4-table-wrap">
              <table className="m4-table">
                <thead>
                  <tr>
                    <th>镜号（按①顺序）</th>
                    <th>选用通过产物（支持单镜重拍替换）</th>
                    <th>可装配</th>
                  </tr>
                </thead>
                <tbody>
                  {ep.shots.map((sh) => {
                    const selectedJob = jobOverridesByEp[ep.id]?.[sh.shot_id] ?? sh.passed_jobs[0]?.id ?? '';
                    return (
                      <tr key={sh.shot_id}>
                        <td>{sh.shot_code}</td>
                        <td>
                          {sh.passed_jobs.length === 0 ? (
                            <span className="m4-badge warn">尚无通过产物</span>
                          ) : (
                            <select
                              className="m4-input"
                              value={selectedJob}
                              onChange={(e) =>
                                setJobOverridesByEp((prev) => ({
                                  ...prev,
                                  [ep.id]: { ...(prev[ep.id] ?? {}), [sh.shot_id]: e.target.value },
                                }))
                              }
                            >
                              {sh.passed_jobs.map((j, idx) => (
                                <option key={j.id} value={j.id}>
                                  {idx === 0 ? '[最新通过] ' : '[历史保留] '}
                                  {j.measured_duration_s ?? sh.target_seconds}s · SHA {j.output_sha256.slice(0, 10)} (
                                  {j.provider})
                                </option>
                              ))}
                            </select>
                          )}
                        </td>
                        <td>
                          <span className={`m4-badge ${sh.passed_jobs.length > 0 ? 'ok' : 'warn'}`}>
                            {sh.passed_jobs.length > 0 ? '就绪' : '缺产物'}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {ep.deliveries.map((deliv) => (
              <div key={deliv.id} className="m4-mat-card">
                <div className="m4-ep-head">
                  <strong>
                    成片版本 v{deliv.version} · {deliv.duration_s ?? '?'}s · {deliv.width ?? '?'}×{deliv.height ?? '?'} ·
                    SHA {deliv.sha256.slice(0, 12)}
                    {deliv.is_mock ? '（Mock 演示件，不可验收）' : ''}
                  </strong>
                  <span
                    className={`m4-badge ${
                      deliv.acceptance?.decision === 'accepted'
                        ? 'ok'
                        : deliv.acceptance?.decision === 'rejected'
                          ? 'warn'
                          : ''
                    }`}
                  >
                    {deliv.acceptance ? `验收：${deliv.acceptance.decision}` : '待观看验收'}
                  </span>
                </div>

                {deliv.media_url && <video src={deliv.media_url} controls width={480} preload="metadata" />}

                <div className="m4-toolbar">
                  {deliv.media_url && (
                    <a className="btn-secondary" href={deliv.media_url} download={`${ep.episode_code}_v${deliv.version}.mp4`}>
                      下载成片 MP4
                    </a>
                  )}
                  {deliv.subtitle_url && (
                    <a className="btn-secondary" href={deliv.subtitle_url} download={`${ep.episode_code}_v${deliv.version}.srt`}>
                      下载 SRT 字幕
                    </a>
                  )}
                </div>

                <div className="m4-toolbar">
                  <label className="m4-toolbar">
                    <input
                      type="checkbox"
                      checked={Boolean(watchedByDeliv[deliv.id])}
                      onChange={(e) => setWatchedByDeliv((prev) => ({ ...prev, [deliv.id]: e.target.checked }))}
                    />
                    已完整观看并核对画音与字幕
                  </label>
                  <input
                    className="m4-input"
                    style={{ maxWidth: 320 }}
                    placeholder="验收备注或驳回原因"
                    value={evidenceByDeliv[deliv.id] ?? ''}
                    onChange={(e) => setEvidenceByDeliv((prev) => ({ ...prev, [deliv.id]: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busyKey !== null || deliv.is_mock}
                    onClick={() => void handleAcceptance(deliv.id, deliv.sha256, 'accepted')}
                  >
                    验收通过
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busyKey !== null || deliv.is_mock}
                    onClick={() => void handleAcceptance(deliv.id, deliv.sha256, 'rejected')}
                  >
                    驳回返修
                  </button>
                </div>
              </div>
            ))}
          </div>
        ))
      )}

      <div className="m4-footer-bar">
        <span className="m4-footer-note">仅接受来自四阶本轮已通过真实产物的 FFmpeg 装配结果，每次重装不覆盖历史版本。</span>
        <button
          type="button"
          className="btn-primary"
          disabled={!stage?.done}
          onClick={() => {
            void onRefresh();
            onMessage?.('已核对并确认本集四阶成片验收完成。', 'success');
          }}
        >
          成片验收完成
        </button>
      </div>
    </section>
  );
}
