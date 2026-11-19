import { useCallback, useEffect, useState } from 'react';
import { BookText, Loader2, Plus, Save, X } from 'lucide-react';
import {
  api, json,
  type ProjectTerminology,
  type TerminologyTerm,
} from '../api';
import { auditActorLabels, label } from '../constants';

const sourceLabels: Record<string, string> = {
  human: '人工填写', ai: 'AI 生成', voice_alignment: '语音对齐生成', asr: '语音识别生成',
  director_shot_config: '人工冻结字幕汇编', shot_dialogue: '镜头对白生成',
};

type Props = {
  projectId: string;
  busy: boolean;
  onMessage: (message: string, kind?: 'success' | 'error') => void;
  refreshEpoch?: number;
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

      {revisions.length === 0 && (
        <div className="empty-inline">还没有人名对照表。在上方填写标准写法并「保存为新版本」后，真实审片时识别模型会先收到这张表。</div>
      )}
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
