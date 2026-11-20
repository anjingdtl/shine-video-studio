import { useCallback, useEffect, useState } from 'react';
import {
  confirmM4Storyboard,
  fetchM4StoryboardDraft,
  importM4Storyboard,
  saveM4StoryboardDraft,
  type M4DraftEpisode,
  type M4DraftShot,
  type M4Overview,
  type M4RequestedAsset,
  type M4StageSummary,
  type M4StoryboardPayload,
} from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onRefresh: () => Promise<void>;
  onNextStage: () => void;
  onMessage?: (text: string, kind?: 'success' | 'error') => void;
};

function formatAssetsText(items: M4RequestedAsset[]): string {
  return items
    .map((a) => `${a.family === 'character' ? '人物' : a.family === 'scene' ? '场景' : '道具'}:${a.name}${a.variant ? `(${a.variant})` : ''}`)
    .join(', ');
}

function parseAssetsText(raw: string): M4RequestedAsset[] {
  const out: M4RequestedAsset[] = [];
  for (const token of raw.split(/[,，、;；]+/)) {
    const t = token.trim();
    if (!t) continue;
    let family: 'character' | 'scene' | 'prop' = 'character';
    let rest = t;
    const colon = t.match(/^(人物|场景|道具|character|scene|prop)\s*[:：]\s*(.+)$/i);
    if (colon) {
      const f = colon[1].toLowerCase();
      family = f === '场景' || f === 'scene' ? 'scene' : f === '道具' || f === 'prop' ? 'prop' : 'character';
      rest = colon[2].trim();
    }
    const vm = rest.match(/^(.+?)[（(]([^）)]+)[）)]$/);
    if (vm) out.push({ family, name: vm[1].trim(), variant: vm[2].trim() });
    else out.push({ family, name: rest, variant: '' });
  }
  return out;
}

export function StoryboardStagePage({ overview, stage, onRefresh, onNextStage, onMessage }: Props) {
  const [pasteText, setPasteText] = useState('');
  const [payload, setPayload] = useState<M4StoryboardPayload>({ episodes: [], ambiguities: [], extractor: 'manual' });
  const [revision, setRevision] = useState(0);
  const [draftStatus, setDraftStatus] = useState('EMPTY');
  const [sourceSha, setSourceSha] = useState('');
  const [ackDeviation, setAckDeviation] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState('');

  const loadDraft = useCallback(async () => {
    try {
      const state = await fetchM4StoryboardDraft(overview.project.id);
      setPayload(state.draft.payload);
      setRevision(state.draft.revision);
      setDraftStatus(state.draft.status);
      setSourceSha(state.draft.source_sha256);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : '读取分镜草稿失败');
    }
  }, [overview.project.id]);

  useEffect(() => {
    void loadDraft();
  }, [loadDraft]);

  const handleImportText = async () => {
    if (!pasteText.trim()) return;
    setBusy(true);
    setErrorText('');
    try {
      const res = await importM4Storyboard(overview.project.id, { text: pasteText, filename: 'manual_paste.txt' });
      setPayload(res.draft.payload);
      setRevision(res.draft.revision);
      setDraftStatus(res.draft.status);
      setSourceSha(res.draft.source_sha256);
      onMessage?.('已按人工原文解析集镜表，请核对后冻结。', 'success');
    } catch (error) {
      const msg = error instanceof Error ? error.message : '解析失败';
      setErrorText(msg);
      onMessage?.(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setErrorText('');
    try {
      const res = await importM4Storyboard(overview.project.id, { file });
      setPayload(res.draft.payload);
      setRevision(res.draft.revision);
      setDraftStatus(res.draft.status);
      setSourceSha(res.draft.source_sha256);
      onMessage?.(`已导入「${file.name}」，请核对并确认。`, 'success');
    } catch (error) {
      const msg = error instanceof Error ? error.message : '导入失败';
      setErrorText(msg);
      onMessage?.(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  const updateEpisode = (epIdx: number, patch: Partial<M4DraftEpisode>) => {
    setPayload((prev) => ({
      ...prev,
      episodes: prev.episodes.map((ep, idx) => (idx === epIdx ? { ...ep, ...patch } : ep)),
    }));
  };

  const updateShot = (epIdx: number, shIdx: number, patch: Partial<M4DraftShot>) => {
    setPayload((prev) => ({
      ...prev,
      episodes: prev.episodes.map((ep, i) =>
        i === epIdx
          ? { ...ep, shots: ep.shots.map((sh, j) => (j === shIdx ? { ...sh, ...patch } : sh)) }
          : ep,
      ),
    }));
  };

  const addEpisode = () => {
    const nextNo = payload.episodes.length + 1;
    const epCode = `ep${String(nextNo).padStart(2, '0')}`;
    setPayload((prev) => ({
      ...prev,
      episodes: [
        ...prev.episodes,
        {
          episode_no: nextNo,
          episode_code: epCode,
          title: `第 ${nextNo} 集`,
          target_total_seconds: null,
          shots: [
            {
              episode_code: epCode,
              shot_no: 1,
              shot_code: `${epCode}-s01`,
              target_seconds: 5,
              summary: '',
              dialogue: [],
              requested_assets: [],
            },
          ],
        },
      ],
    }));
  };

  const addShot = (epIdx: number) => {
    const ep = payload.episodes[epIdx];
    const nextNo = ep.shots.length + 1;
    updateEpisode(epIdx, {
      shots: [
        ...ep.shots,
        {
          episode_code: ep.episode_code,
          shot_no: nextNo,
          shot_code: `${ep.episode_code}-s${String(nextNo).padStart(2, '0')}`,
          target_seconds: 5,
          summary: '',
          dialogue: [],
          requested_assets: [],
        },
      ],
    });
  };

  const handleSaveDraft = async () => {
    setBusy(true);
    setErrorText('');
    try {
      const saved = await saveM4StoryboardDraft(overview.project.id, payload, revision || undefined);
      setPayload(saved.draft.payload);
      setRevision(saved.draft.revision);
      setDraftStatus(saved.draft.status);
      onMessage?.(`草稿已保存（第 ${saved.draft.revision} 版）。`, 'success');
      return saved.draft.revision;
    } catch (error) {
      const msg = error instanceof Error ? error.message : '保存草稿失败';
      setErrorText(msg);
      onMessage?.(msg, 'error');
      return null;
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    setBusy(true);
    setErrorText('');
    try {
      const saved = await saveM4StoryboardDraft(overview.project.id, payload, revision || undefined);
      const res = await confirmM4Storyboard(overview.project.id, {
        expectedRevision: saved.draft.revision,
        acknowledgeDurationMismatch: ackDeviation,
      });
      setRevision(res.confirmed.revision);
      setDraftStatus(res.confirmed.status);
      await onRefresh();
      onMessage?.(`分镜计划第 ${res.confirmed.revision} 版已确认并冻结。`, 'success');
    } catch (error) {
      const msg = error instanceof Error ? error.message : '确认冻结失败';
      setErrorText(msg);
      onMessage?.(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="m4-panel" aria-label="人工分镜书">
      <div className="m4-panel-head">
        <div>
          <h2>① 人工分镜书</h2>
          <p className="m4-panel-desc">
            粘贴或导入你的人工分镜文本，或在集镜表中直接编辑分集、镜序、目标秒数（4–{overview.project.max_shot_seconds}s）与素材需求后冻结。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          {draftStatus === 'CONFIRMED' ? `已冻结第 ${revision} 版` : revision > 0 ? `草稿第 ${revision} 版` : '待建立'}
        </span>
      </div>

      {sourceSha && (
        <div className="m4-footer-note">原文 SHA-256：{sourceSha.slice(0, 16)}…</div>
      )}

      {(errorText || (payload.ambiguities && payload.ambiguities.length > 0)) && (
        <div className="m4-blocker-box" role="status">
          {errorText && <div>• {errorText}</div>}
          {payload.ambiguities?.map((amb, idx) => (
            <div key={`${amb.code}-${idx}`}>• [{amb.shot_code ?? amb.code}] {amb.message}</div>
          ))}
        </div>
      )}

      <div className="m4-import-grid">
        <textarea
          className="m4-textarea"
          aria-label="粘贴分镜文本"
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder={'例：\n第1集：雨夜重逢\n第1镜（6s）：雨夜窄巷中林晚撑伞回头。\n人物：林晚（雨衣版）\n场景：雨夜窄巷'}
        />
        <div className="m4-toolbar" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <button type="button" className="btn-secondary" disabled={busy || !pasteText.trim()} onClick={() => void handleImportText()}>
            解析粘贴文本
          </button>
          <label className="btn-secondary" style={{ textAlign: 'center', cursor: 'pointer' }}>
            导入文件 (.txt/.md/.docx/.pdf)
            <input
              type="file"
              accept=".txt,.md,.markdown,.json,.docx,.pdf"
              style={{ display: 'none' }}
              onChange={(e) => void handleFileUpload(e.target.files?.[0])}
            />
          </label>
          <button type="button" className="btn-secondary" disabled={busy} onClick={addEpisode}>
            + 手工新增一集
          </button>
        </div>
      </div>

      {payload.episodes.map((ep, epIdx) => {
        const plannedSum = ep.shots.reduce((s, sh) => s + (Number(sh.target_seconds) || 0), 0);
        return (
          <div key={ep.episode_code} className="m4-ep-card">
            <div className="m4-ep-head">
              <strong>{ep.title}（{ep.episode_code}）· 合计 {plannedSum.toFixed(1)}s</strong>
              <button type="button" className="btn-secondary" onClick={() => addShot(epIdx)}>
                + 新增镜头
              </button>
            </div>
            <div className="m4-table-wrap">
              <table className="m4-table">
                <thead>
                  <tr>
                    <th>镜号</th>
                    <th>秒数</th>
                    <th>画面摘要（人工原文）</th>
                    <th>素材需求（人物/场景/道具）</th>
                  </tr>
                </thead>
                <tbody>
                  {ep.shots.map((sh, shIdx) => (
                    <tr key={sh.shot_code}>
                      <td style={{ width: 110 }}>
                        <input
                          className="m4-input"
                          aria-label={`镜号 ${sh.shot_code}`}
                          value={sh.shot_code}
                          onChange={(e) => updateShot(epIdx, shIdx, { shot_code: e.target.value })}
                        />
                      </td>
                      <td style={{ width: 90 }}>
                        <input
                          className="m4-input"
                          type="number"
                          min={4}
                          max={overview.project.max_shot_seconds}
                          step={0.5}
                          aria-label={`时长 ${sh.shot_code}`}
                          value={sh.target_seconds ?? ''}
                          onChange={(e) => updateShot(epIdx, shIdx, {
                            target_seconds: e.target.value === '' ? null : Number(e.target.value),
                          })}
                        />
                      </td>
                      <td>
                        <input
                          className="m4-input"
                          aria-label={`摘要 ${sh.shot_code}`}
                          value={sh.summary}
                          onChange={(e) => updateShot(epIdx, shIdx, { summary: e.target.value })}
                        />
                      </td>
                      <td style={{ width: 240 }}>
                        <input
                          className="m4-input"
                          aria-label={`素材 ${sh.shot_code}`}
                          value={formatAssetsText(sh.requested_assets)}
                          onChange={(e) => updateShot(epIdx, shIdx, { requested_assets: parseAssetsText(e.target.value) })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      <div className="m4-footer-bar">
        <label className="m4-footer-note">
          <input type="checkbox" checked={ackDeviation} onChange={(e) => setAckDeviation(e.target.checked)} /> 如有分集总时长偏差，按各镜实际时长之和冻结
        </label>
        <div className="m4-toolbar">
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => void handleSaveDraft()}>
            保存草稿
          </button>
          <button type="button" className="btn-primary" disabled={busy || payload.episodes.length === 0} onClick={() => void handleConfirm()}>
            确认并冻结分镜计划
          </button>
          {stage?.done && (
            <button type="button" className="btn-secondary" onClick={onNextStage}>
              下一步：素材生成
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
