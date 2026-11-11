import { useEffect, useState } from 'react';
import { CloudUpload } from 'lucide-react';
import { api, json } from '../api';

type TextScope = {
  scope_hash: string;
  document_sha256: string;
  transmission: string;
  roles: { role: string; configured: boolean; provider?: string; model?: string; base_url?: string; allowed?: boolean }[];
};

/** 项目级一次同意卡（设计方案 §6.2）：任一流程首次要把剧本文本发往云端文字模型时
 *  （后端返回 REMOTE_AGENT_CONSENT_REQUIRED 的第一现场）页内显示此卡。同意后写入
 *  项目级授权，此后旅程内不再出现任何 consent 勾选；撤回入口在基础配置 ScriptTextConsent。
 *  不触碰「云端传输需明确许可」的产品不变量——只是把每流程重复勾收敛为项目一次同意。 */
export function ConsentOnceCard({ projectId, documentId, onAgreed, onDecline }: {
  projectId: string;
  documentId: string;
  onAgreed?: () => void;
  onDecline?: () => void;
}) {
  const [scope, setScope] = useState<TextScope | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const path = `/projects/${projectId}/material-preparation/text-consent`;

  useEffect(() => {
    let current = true;
    void api<TextScope>(`${path}/${documentId}`)
      .then((result) => { if (current) setScope(result); })
      .catch((error: Error) => { if (current) setProblem(error.message); });
    return () => { current = false; };
  }, [path, documentId]);

  const remoteRoles = (scope?.roles ?? []).filter((row) => row.configured);

  async function agree() {
    if (!scope) return;
    setBusy(true); setProblem('');
    try {
      await api<TextScope>(path, json('POST', {
        document_id: documentId, expected_document_hash: scope.document_sha256,
        expected_scope_hash: scope.scope_hash, granted: true,
      }));
      onAgreed?.();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="consent-card" role="region" aria-label="剧本文本发送一次同意">
      <div className="consent-card-head"><CloudUpload size={16} /> 发送前需要你知道</div>
      <p>剧本文本将发送到你配置的云端文字模型（仅文字，不含图像/音频字节）。同意后本项目全程不再重复询问。</p>
      {remoteRoles.length > 0 && (
        <p className="consent-provider">
          {remoteRoles.map((row) => `${row.role}：${row.provider ?? ''} / ${row.model ?? ''}`).join('；')}
        </p>
      )}
      {problem && <p role="alert" className="form-error">{problem}</p>}
      <div className="consent-card-actions">
        <button type="button" className="btn-primary" disabled={busy || !scope} onClick={() => void agree()}>
          {busy ? '正在保存授权…' : '同意并继续'}
        </button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => onDecline?.()}>暂不发送</button>
      </div>
    </div>
  );
}

export default ConsentOnceCard;