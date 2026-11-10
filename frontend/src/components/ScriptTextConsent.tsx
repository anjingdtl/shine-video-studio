import { useEffect, useState } from 'react';
import { api, json } from '../api';

type TextScope = {
  scope_hash: string;
  document_sha256: string;
  transmission: string;
  roles: {
    role: string;
    configured: boolean;
    provider?: string;
    base_url?: string;
    model?: string;
    allowed?: boolean;
  }[];
};

const ROLE_LABELS: Record<string, string> = {
  producer: '制片统筹', scriptwriter: '编剧', director: '导演', cinematographer: '摄影指导',
  continuity_supervisor: '场记与连续性', editor: '剪辑师',
  four_stage_bible_run: '全剧蓝图', four_stage_scene_script_run: '分场剧本',
};

/**
 * One consent panel per project document, even when the workbench mounts the
 * same panel more than once in a single tree. Mounting sixteen copies stacked
 * sixteen identical "文字模型发送授权" folds above the real material list, so the
 * operator could not reach the analysis button at all. The first instance owns
 * the panel; every other instance of the same document renders nothing.
 *
 * The count is ref-counted on mount, so switching projects and coming back
 * re-claims the slot instead of leaving the panel permanently invisible.
 */
const ACTIVE_PANELS = new Map<string, number>();

export function ScriptTextConsent({ projectId, documentId, expanded = false, onScriptwriterAccess }: {
  projectId: string;
  documentId: string;
  expanded?: boolean;
  onScriptwriterAccess?: (allowed: boolean) => void;
}) {
  const [scope, setScope] = useState<TextScope | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [problem, setProblem] = useState('');
  const [primary, setPrimary] = useState(true);
  // One consent fold per PROJECT, not per document: the same panel was
  // mounted with several document ids, so a per-document key still let
  // several folds stack up and push the material controls off screen.
  const panelKey = projectId;
  const path = `/projects/${projectId}/material-preparation/text-consent`;

  useEffect(() => {
    const next = (ACTIVE_PANELS.get(panelKey) ?? 0) + 1;
    ACTIVE_PANELS.set(panelKey, next);
    setPrimary(next === 1);
    return () => {
      const left = (ACTIVE_PANELS.get(panelKey) ?? 1) - 1;
      if (left <= 0) ACTIVE_PANELS.delete(panelKey);
      else ACTIVE_PANELS.set(panelKey, left);
    };
  }, [panelKey]);

  useEffect(() => {
    if (!primary) return;
    let current = true;
    void api<TextScope>(`${path}/${documentId}`).then((result) => {
      if (current) setScope(result);
    }).catch((error: Error) => {
      if (current) setProblem(error.message);
    });
    return () => { current = false; };
  }, [documentId, path, primary]);

  const scriptwriterAllowed = scope?.roles.some((row) => row.role === 'scriptwriter' && row.allowed) ?? false;
  useEffect(() => { onScriptwriterAccess?.(scriptwriterAllowed); }, [onScriptwriterAccess, scriptwriterAllowed]);

  async function decide(granted: boolean) {
    if (!scope) return;
    setBusy(true); setNotice(''); setProblem('');
    try {
      setScope(await api<TextScope>(path, json('POST', {
        document_id: documentId, expected_document_hash: scope.document_sha256,
        expected_scope_hash: scope.scope_hash, granted,
      })));
      setNotice(granted ? '已保存文字发送授权。' : '已撤回文字发送授权；已经发送的请求无法撤回。');
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
      setScope(await api<TextScope>(`${path}/${documentId}`).catch(() => null));
    } finally {
      setBusy(false);
    }
  }

  if (!primary) return null;
  return <details className="material-stage" data-consent="script-text" open={expanded || undefined}>
    <summary>文字模型发送授权</summary>
    {scope ? <>
      <p>{scope.transmission}</p>
      {scope.roles.map((row) => <p key={row.role}>{ROLE_LABELS[row.role] ?? row.role} · {
        row.configured ? `${row.provider} / ${row.model} / ${row.base_url} · ${row.allowed ? '当前允许' : '需要授权'}` : '未配置'
      }</p>)}
      <button type="button" disabled={busy} onClick={() => void decide(true)}>允许向上述模型发送本剧本必要文字</button>
      <button type="button" disabled={busy} onClick={() => void decide(false)}>撤回文字发送授权</button>
      <small>更换剧本修订、角色路由或模型配置后需要重新确认。授权范围为文字。</small>
    </> : !problem && <p>正在读取文字发送范围…</p>}
    {notice && <p role="status">{notice}</p>}
    {problem && <p role="alert" className="form-error">{problem}</p>}
  </details>;
}
