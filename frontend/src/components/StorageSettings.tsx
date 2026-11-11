import { useEffect, useState } from 'react';
import { api } from '../api';

export type DeploymentStorage = {
  root: string; configured: boolean; system_drive: boolean; requires_selection: boolean;
  free_bytes: number; free_gb: number; paths: Record<string, string>;
  drives: Array<{ path: string; system_drive: boolean; free_gb: number }>;
};

function storageError(error: unknown): string {
  const text = (error as Error).message || String(error);
  const messages: Record<string, string> = {
    EXISTING_DEPLOYMENT_REQUIRES_RECONCILIATION_NO_FILES_MOVED: '已有下载或实例，请先完成迁移对账后再更换目录。现有文件已保留。',
    CHOOSE_EMPTY_DEDICATED_DEPLOYMENT_DIRECTORY: '请选择专用空文件夹，避免覆盖已有文件。',
    ABSOLUTE_LOCAL_DEPLOYMENT_DIRECTORY_REQUIRED: '请输入完整的本机目录，例如 D:\\ShineVideoStudio\\Runtime。',
    DEDICATED_DEPLOYMENT_DIRECTORY_REQUIRED: '请选择专用子文件夹，不要直接选择盘符根目录或项目数据目录。',
    SYSTEM_DRIVE_REQUIRES_EXPLICIT_SELECTION: '所选目录位于系统盘。请改选其他磁盘，或明确勾选使用系统盘。',
    DEPLOYMENT_DIRECTORY_NOT_WRITABLE: '该目录无法写入，请选择有写入权限的文件夹。',
  };
  return Object.entries(messages).find(([code]) => text.includes(code))?.[1] ?? text;
}

export default function StorageSettings() {
  const [storage, setStorage] = useState<DeploymentStorage | null>(null);
  const [path, setPath] = useState('');
  const [allowSystem, setAllowSystem] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    void api<DeploymentStorage>('/setup/storage').then(value => {
      setStorage(value); setPath(value.requires_selection ? '' : value.root);
    }).catch(error => setMessage(String(error)));
  }, []);
  async function save() {
    setBusy(true); setMessage('');
    try {
      const value = await api<DeploymentStorage & { cancelled_plans: string[] }>('/setup/storage', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, allow_system_drive: allowSystem }),
      });
      setStorage(value); setPath(value.root);
      setMessage(`目录已保存，可用 ${value.free_gb} GB${value.cancelled_plans.length ? '；原待执行计划已取消，请重新生成' : ''}。`);
      window.dispatchEvent(new Event('shine-storage-changed'));
    } catch (error) { setMessage(storageError(error)); }
    finally { setBusy(false); }
  }
  return <div className="settings-block" id="deployment-storage">
    <h3>下载与安装目录</h3>
    <p>安装包缓存、模型、ComfyUI 环境和快照统一保存在所选目录。建议选择非系统盘的专用空文件夹。</p>
    {storage && <>
      <div className="guardrail-note">当前目录：{storage.root} · 可用 {storage.free_gb} GB
        {storage.requires_selection && ' · 请先选择其他磁盘，自动安装已阻止。'}</div>
      {storage.drives.length > 0 && <label className="field-line">选择磁盘
        <select aria-label="下载安装磁盘" value="" onChange={event => {
          if (event.target.value) { setPath(`${event.target.value}ShineVideoStudio\\Runtime`); setAllowSystem(false); }
        }}>
          <option value="">选择磁盘后可编辑完整目录</option>
          {storage.drives.map(drive => <option key={drive.path} value={drive.path}>
            {drive.path} · {drive.free_gb} GB 可用{drive.system_drive ? '（系统盘）' : '（推荐）'}
          </option>)}
        </select>
      </label>}
    </>}
    <label className="field-line">完整安装目录
      <input aria-label="下载安装目录" value={path} placeholder="例如 D:\ShineVideoStudio\Runtime"
        onChange={event => setPath(event.target.value)} />
    </label>
    <label className="switch-row"><span>明确选择使用系统盘（默认关闭）</span>
      <input type="checkbox" checked={allowSystem} onChange={event => setAllowSystem(event.target.checked)} />
    </label>
    <button className="btn-secondary" disabled={busy || !path} onClick={() => void save()}>保存下载安装目录</button>
    <p className="guardrail-note">更换目录会取消未执行的安装计划。已有下载或实例时需先对账，系统不会自动搬动或删除文件。</p>
    {message && <div role="status">{message}</div>}
  </div>;
}
