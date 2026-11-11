import { useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { api } from '../api';

export function OpenVideoFolderButton({ artifactId }: { artifactId: string }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  async function openFolder() {
    setBusy(true);
    setProblem('');
    try {
      await api<void>(`/video-artifacts/${encodeURIComponent(artifactId)}/open-folder`, { method: 'POST' });
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return <>
    <button type="button" className="btn-secondary" disabled={busy} onClick={() => void openFolder()}>
      <FolderOpen size={14} aria-hidden="true" /> 打开所在文件夹
    </button>
    {problem && <span className="failure" role="alert">{problem}</span>}
  </>;
}
