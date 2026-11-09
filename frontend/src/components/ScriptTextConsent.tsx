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
  const path = `/projects/${projectId}/material-preparation/text-consent`;

  useEffect(() => {
    let current = true;
    void api<TextScope>(`${path}/${documentId}`).then((result) => {
      if (current) setScope(result);
    }).catch((error: Error) => {
      if (current) setProblem(error.message);
    });
    return () => { current = false; };
  }, [documentId, path]);

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

  return <details className="material-stage" open={expanded || undefined}>
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
