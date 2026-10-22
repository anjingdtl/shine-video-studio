import { useState } from 'react';
import { Check, Clock3, Film, LockKeyhole, Pencil, Upload, X } from 'lucide-react';
import type { Asset } from '../api';
import { roleOptions } from '../constants';

type Props = {
  assets: Asset[];
  busy: boolean;
  onUpload: (file: File, meta: { kind: string; role: string; entityId: string; tags: string }) => void;
  onApprove: (assetId: string, token: string, evidence: string) => void;
  onLock: (assetId: string, token: string, evidence: string) => void;
  onPatchMetadata: (assetId: string, patch: { role?: string | null; entity_id?: string; tags?: string[] }) => void;
};

export default function AssetPanel({ assets, busy, onUpload, onApprove, onLock, onPatchMetadata }: Props) {
  const [operatorToken, setOperatorToken] = useState('');
  const [evidence, setEvidence] = useState('');
  const [kind, setKind] = useState('image');
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
          <h2>素材与主资产审批</h2>
          <p>只有人工批准并锁定的素材才能被镜头引用；Agent 没有创建或审批权限。</p>
        </div>
        <div className="section-icon"><Film size={17} /></div>
      </div>
      <div className="form-grid">
        <label>主资产审批凭据<input type="password" autoComplete="off" value={operatorToken}
          onChange={e => setOperatorToken(e.target.value)} /></label>
        <label>主资产核验依据<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
      </div>
      <div className="upload-stack">
        <div className="upload-row">
          <div><label>素材类型</label>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="image">图片</option><option value="video">视频</option><option value="audio">音频</option>
            </select>
          </div>
          <div><label>实体角色</label>
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div><label>实体 ID</label>
            <input value={entityId} placeholder="CHAR_A / SCENE_01" onChange={(event) => setEntityId(event.target.value)} />
          </div>
          <div className="grow"><label>标签（逗号分隔）</label>
            <input value={tags} placeholder="主角, 夜戏, 定妆" onChange={(event) => setTags(event.target.value)} />
          </div>
          <label className="upload-button"><Upload size={15} /> 上传到本机
            <input
              type="file"
              accept={kind === 'image' ? 'image/png,image/jpeg,image/webp' : kind === 'audio' ? 'audio/*' : 'video/*'}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) onUpload(file, { kind, role, entityId, tags });
              }}
            />
          </label>
        </div>
        <span className="upload-hint">按配置限制大小，校验文件签名，内容寻址去重；素材字节不会发送到云端。</span>
      </div>
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
                {asset.role ? `${asset.role}` : '未设角色'}{asset.entity_id ? ` · ${asset.entity_id}` : ''}
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
              {asset.locked ? <span className="pill approved"><LockKeyhole size={12} />已锁定</span>
                : asset.approved ? <span className="pill approved"><Check size={12} />已批准</span>
                  : <span className="pill pending"><Clock3 size={12} />待人工审批</span>}
            </div>
            {!asset.approved && <button className="text-button" onClick={() => onApprove(asset.id, operatorToken, evidence)}>批准</button>}
            {asset.approved && !asset.locked && <button className="text-button" onClick={() => onLock(asset.id, operatorToken, evidence)}>锁定</button>}
            <button className="icon-button tiny" title="编辑元数据" onClick={() => beginEdit(asset)} disabled={busy}><Pencil size={12} /></button>
          </div>
        ))}
      </div>
      <div className="guardrail-note"><LockKeyhole size={13} /><span>Agent 没有主资产创建、替换或审批权限；素材只在本机使用。</span></div>
    </section>
  );
}
