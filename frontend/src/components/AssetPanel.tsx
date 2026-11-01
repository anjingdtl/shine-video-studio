import { useState } from 'react';
import { Check, Clock3, Film, LockKeyhole, Pencil, Upload, X } from 'lucide-react';
import type { Asset } from '../api';
import { roleLabels, roleOptions } from '../constants';

type Props = {
  assets: Asset[];
  busy: boolean;
  onUpload: (file: File, meta: { kind: string; role: string; entityId: string; tags: string }) => void;
  onConfirm: (assetIds: string[]) => void;
  onPatchMetadata: (assetId: string, patch: { role?: string | null; entity_id?: string; tags?: string[] }) => void;
};

export default function AssetPanel({ assets, busy, onUpload, onConfirm, onPatchMetadata }: Props) {
  const [role, setRole] = useState('character');
  const [entityId, setEntityId] = useState('');
  const [tags, setTags] = useState('');
  const [editingId, setEditingId] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editEntity, setEditEntity] = useState('');
  const [editTags, setEditTags] = useState('');

  function beginEdit(asset: Asset) {
    setEditingId(asset.id);
    setEditRole(asset.role ?? '');
    setEditEntity(asset.entity_id ?? '');
    setEditTags((asset.tags ?? []).join(', '));
  }

  function saveEdit(assetId: string) {
    onPatchMetadata(assetId, {
      role: editRole || null,
      entity_id: editEntity,
      tags: editTags.split(',').map((tag) => tag.trim()).filter(Boolean),
    });
    setEditingId('');
  }

  return (
    <section className="card section-card" id="assets">
      <div className="section-heading">
        <div>
          <div className="section-kicker">01 / ASSET REGISTRY</div>
          <h2>项目素材</h2>
          <p>上传素材，选好用途，再点击「确认使用」。确认后即可用于分镜，素材只保存在本机。</p>
        </div>
        <div className="section-icon"><Film size={17} /></div>
      </div>
      <div className="upload-stack">
        <div className="upload-row">
          <div><label>素材用途</label>
            <select aria-label="素材用途" value={role} onChange={(event) => setRole(event.target.value)}>
              {roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <label className="upload-button"><Upload size={15} /> 上传到本机
            <input
              type="file"
              disabled={busy}
              accept="image/png,image/jpeg,image/webp,audio/*,video/*"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) {
                  const kind = file.type.startsWith('audio/') || /\.(wav|mp3|flac|m4a|ogg)$/i.test(file.name)
                    ? 'audio' : file.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(file.name) ? 'video' : 'image';
                  onUpload(file, { kind, role: kind === 'audio' && role === 'character' ? 'voice' : role,
                    entityId: (entityId.trim() || file.name.replace(/\.[^.]+$/, '')).slice(0, 80), tags });
                }
              }}
            />
          </label>
        </div>
        <details><summary>可选：角色名称与标签</summary>
          <label>角色 / 场景名称<input value={entityId} placeholder="默认使用文件名；同一人物可填相同名称" onChange={e => setEntityId(e.target.value)} /></label>
          <label>标签<input value={tags} placeholder="主角, 夜戏, 定妆" onChange={e => setTags(e.target.value)} /></label>
        </details>
        <span className="upload-hint">支持图片、视频和音频，自动识别类型。仅上传你有权使用的素材。</span>
      </div>
      {assets.some(asset => !asset.locked) && <button className="secondary-button" disabled={busy}
        onClick={() => onConfirm(assets.filter(asset => !asset.locked).map(asset => asset.id))}>确认使用全部待确认素材</button>}
      <div className="asset-list">
        {assets.length === 0 ? <div className="empty-inline">尚无素材。请只上传你提供且有权使用的素材。</div> : assets.map((asset) => (
          <div className="asset-row" key={asset.id}>
            <div className={`asset-type ${asset.kind}`}>{asset.kind === 'image' ? 'IMG' : asset.kind === 'audio' ? 'AUD' : 'VID'}</div>
            <div className="asset-name">
              <b>{asset.filename}</b>
              <span>
                {asset.sha256.slice(0, 12)}… · {(asset.byte_size / 1024).toFixed(1)} KB
                {asset.width && asset.height ? ` · ${asset.width}×${asset.height}` : ''}
                {asset.duration_s ? ` · ${asset.duration_s.toFixed(1)}s` : ''}
              </span>
              <span className="asset-tags">
                {asset.role ? roleLabels[asset.role] ?? asset.role : '未设角色'}{asset.entity_id ? ` · ${asset.entity_id}` : ''}
                {asset.tags?.length ? ` · ${asset.tags.join(' / ')}` : ''}
              </span>
              {editingId === asset.id && <div className="asset-edit">
                <select value={editRole} onChange={(event) => setEditRole(event.target.value)}>
                  <option value="">未设角色</option>
                  {roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <input value={editEntity} placeholder="实体 ID" onChange={(event) => setEditEntity(event.target.value)} />
                <input value={editTags} placeholder="标签, 逗号分隔" onChange={(event) => setEditTags(event.target.value)} />
                <button className="text-button" onClick={() => saveEdit(asset.id)}><Check size={12} /> 保存</button>
                <button className="text-button" onClick={() => setEditingId('')}><X size={12} /> 取消</button>
              </div>}
            </div>
            <div className="asset-state">
              {asset.locked ? <span className="pill approved"><LockKeyhole size={12} />可用于分镜</span>
                : <span className="pill pending"><Clock3 size={12} />待你确认</span>}
            </div>
            {!asset.locked && <button className="text-button" disabled={busy} onClick={() => onConfirm([asset.id])}>确认使用</button>}
            <button className="icon-button tiny" title="编辑元数据" onClick={() => beginEdit(asset)} disabled={busy}><Pencil size={12} /></button>
          </div>
        ))}
      </div>
      <div className="guardrail-note"><LockKeyhole size={13} /><span>确认会固定素材版本；AI 无法替你确认或替换。需要修改用途时，可点击右侧编辑。</span></div>
    </section>
  );
}
