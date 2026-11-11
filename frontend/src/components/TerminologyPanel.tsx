import { useCallback, useEffect, useState } from 'react';
import { BookText, Captions, Loader2, Plus, Save, X } from 'lucide-react';
import {
  api, json,
  type ProjectTerminology,
  type SubtitleCue,
  type SubtitleTrack,
  type TerminologyTerm,
} from '../api';
import { auditActorLabels, label, subtitleStatusLabels } from '../constants';

const sourceLabels: Record<string, string> = {
  human: '人工填写', ai: 'AI 生成', voice_alignment: '语音对齐生成', asr: '语音识别生成',
};

type Props = {
  projectId: string;
  busy: boolean;
  onMessage: (message: string, kind?: 'success' | 'error') => void;
};

/** One editable line of the terminology table. */
type TermRow = { canonical: string; aliases: string };

const emptyRow = (): TermRow => ({ canonical: '', aliases: '' });

export default function TerminologyPanel({ projectId, busy, onMessage }: Props) {
  const [revisions, setRevisions] = useState<ProjectTerminology[]>([]);
  const [rows, setRows] = useState<TermRow[]>([emptyRow()]);
  const [notes, setNotes] = useState('');
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    const found = await api<ProjectTerminology[]>(`/projects/${projectId}/terminology`);
    setRevisions(found);
    const latest = found[0];
    if (latest) {
      setRows(latest.terms.length ? latest.terms.map((term) => ({
        canonical: term.canonical,
        aliases: term.aliases.join('、'),
      })) : [emptyRow()]);
      setNotes(latest.notes ?? '');
    }
  }, [projectId]);

  useEffect(() => { reload().catch(() => setRevisions([])); }, [reload, projectId]);

  const save = useCallback(async () => {
    const terms: TerminologyTerm[] = rows
      .filter((row) => row.canonical.trim())
      .map((row) => ({
        canonical: row.canonical.trim(),
        aliases: row.aliases.split(/[、,，\s]+/).map((value) => value.trim()).filter(Boolean),
      }));
    setWorking(true);
    try {
      await api(`/projects/${projectId}/terminology`, json('POST', {
        terms, notes, created_by: 'operator', source: 'human',
      }));
      await reload();
      onMessage('人名对照表已保存；下一次真实审片会把它作为提示交给识别模型。', 'success');
    } catch (error) {
      onMessage(`人名对照表保存失败：${(error as Error).message}`, 'error');
    } finally {
      setWorking(false);
    }
  }, [rows, notes, projectId, reload, onMessage]);

  return (
    <div className="card section-card" id="terminology">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">TERM / SCRIPT TERMINOLOGY</div>
          <h2>人名对照表</h2>
        </div>
        <div className="section-icon plum"><BookText size={17} /></div>
      </div>
      <p className="panel-hint">
        识别模型在转写<b>之前</b>会收到这张表，因此专有名词按剧本写法读出，而不是事后纠错。
        实测：把易混写法一并写进提示会让模型重新选错，所以提示里只给标准写法；易混写法仅留作人工参考。
      </p>

      <div className="terminology-builder">
        {rows.map((row, index) => (
          <div className="terminology-row" key={index}>
            <input
              aria-label={`专有名词 ${index + 1}`}
              placeholder="标准写法，例如 周砚"
              value={row.canonical}
              onChange={(event) => setRows(rows.map((item, position) => (
                position === index ? { ...item, canonical: event.target.value } : item)))}
            />
            <input
              aria-label={`易混写法 ${index + 1}`}
              placeholder="易混写法，例如 周烨（用、分隔）"
              value={row.aliases}
              onChange={(event) => setRows(rows.map((item, position) => (
                position === index ? { ...item, aliases: event.target.value } : item)))}
            />
            <button
              type="button"
              className="icon-button tiny"
              aria-label={`删除第 ${index + 1} 行`}
              onClick={() => setRows(rows.length > 1 ? rows.filter((_, position) => position !== index) : [emptyRow()])}
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </div>

      <div className="terminology-actions">
        <button type="button" className="btn-secondary" onClick={() => setRows([...rows, emptyRow()])}>
          <Plus size={13} /> 添加一行
        </button>
        <label className="terminology-notes">
          <span>补充说明（仅存档，不进入提示）</span>
          <input
            aria-label="补充说明"
            placeholder="例如：人名使用剧本写法"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
        <button type="button" className="btn-secondary" disabled={busy || working} onClick={save}>
          {working ? <Loader2 size={14} /> : <Save size={14} />} 保存为新版本
        </button>
      </div>

      {revisions.length > 0 && (
        <div className="shot-list">
          {revisions.map((row) => (
            <div className="shot-block" key={row.id}>
              <div className="shot-row">
                <div className="shot-number">{String(row.revision).padStart(2, '0')}</div>
                <div className="shot-content">
                  <b>版本 {row.revision} · {row.terms.length} 个词条</b>
                  <small>{label(sourceLabels, row.source)} · {label(auditActorLabels, row.created_by)} · 指纹 {row.sha256.slice(0, 12)}…</small>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SubtitlePanel({ projectId, busy, onMessage }: Props) {
  const [revisions, setRevisions] = useState<SubtitleTrack[]>([]);
  const [cues, setCues] = useState<SubtitleCue[]>([]);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async () => {
    const found = await api<SubtitleTrack[]>(`/projects/${projectId}/subtitles`);
    setRevisions(found);
    const latest = found[0];
    setCues(latest ? latest.cues : []);
  }, [projectId]);

  useEffect(() => { reload().catch(() => setRevisions([])); }, [reload, projectId]);

  const save = useCallback(async () => {
    setWorking(true);
    try {
      await api(`/projects/${projectId}/subtitles`, json('POST', {
        cues, created_by: 'operator', source: 'human',
      }));
      await reload();
      onMessage('字幕已保存为新版本；交付时再合成进成片。', 'success');
    } catch (error) {
      onMessage(`字幕保存失败：${(error as Error).message}`, 'error');
    } finally {
      setWorking(false);
    }
  }, [cues, projectId, reload, onMessage]);

  const approve = useCallback(async (revision: number) => {
    setWorking(true);
    try {
      await api(`/projects/${projectId}/subtitles/${revision}/approve`, json('POST', {}));
      await reload();
      onMessage(`字幕版本 ${revision} 已批准；交付时将合成这一版。`, 'success');
    } catch (error) {
      onMessage(`字幕批准失败：${(error as Error).message}`, 'error');
    } finally {
      setWorking(false);
    }
  }, [projectId, reload, onMessage]);

  return (
    <div className="card section-card" id="subtitles">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">CAPTION / EXTERNAL TRACK</div>
          <h2>字幕（外挂可修订）</h2>
        </div>
        <div className="section-icon plum"><Captions size={17} /></div>
      </div>
      <p className="panel-hint">
        字幕独立于镜头保存：可以直接改字或改时间，不必重新渲染；检测完成后再合成进成片。
        只有<b>已批准</b>的版本会被合成。
      </p>

      <div className="terminology-builder">
        {cues.map((cue, index) => (
          <div className="terminology-row subtitle-row" key={index}>
            <input
              aria-label={`字幕开始 ${index + 1}`}
              placeholder="开始秒"
              value={String(cue.start_s)}
              onChange={(event) => setCues(cues.map((item, position) => (
                position === index ? { ...item, start_s: Number(event.target.value) } : item)))}
            />
            <input
              aria-label={`字幕结束 ${index + 1}`}
              placeholder="结束秒"
              value={String(cue.end_s)}
              onChange={(event) => setCues(cues.map((item, position) => (
                position === index ? { ...item, end_s: Number(event.target.value) } : item)))}
            />
            <input
              aria-label={`字幕文本 ${index + 1}`}
              placeholder="字幕文本"
              value={cue.text}
              onChange={(event) => setCues(cues.map((item, position) => (
                position === index ? { ...item, text: event.target.value } : item)))}
            />
          </div>
        ))}
      </div>

      <div className="terminology-actions">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setCues([...cues, { index: cues.length + 1, start_s: 0, end_s: 2, text: '' }])}
        >
          <Plus size={13} /> 添加一行字幕
        </button>
        <button type="button" className="btn-secondary" disabled={busy || working} onClick={save}>
          <Save size={14} /> 保存为新版本
        </button>
      </div>

      {revisions.length > 0 && (
        <div className="shot-list">
          {revisions.map((row) => (
            <div className="shot-block" key={row.id}>
              <div className="shot-row">
                <div className="shot-number">{String(row.revision).padStart(2, '0')}</div>
                <div className="shot-content">
                  <b>版本 {row.revision} · {label(subtitleStatusLabels, row.status)} · {row.cues.length} 行</b>
                  <small>{label(sourceLabels, row.source)} · 指纹 {row.sha256.slice(0, 12)}…</small>
                </div>
                {row.status !== 'APPROVED' && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy || working}
                    onClick={() => approve(row.revision)}
                  >
                    批准此版本
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
