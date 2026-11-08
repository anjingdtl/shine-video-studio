import { useEffect, useState } from 'react';
import { Check, Clock3, Film, HelpCircle, LockKeyhole, Pencil, Upload, X } from 'lucide-react';
import { API, api, type Asset } from '../api';
import { ImagePreview } from './ImagePreview';
import { roleLabels, roleOptions } from '../constants';

type Props = {
  assets: Asset[];
  busy: boolean;
  onUpload: (file: File, meta: { kind: string; role: string; entityId: string; tags: string }) => void;
  onConfirm: (assetIds: string[]) => void;
  onPatchMetadata: (assetId: string, patch: { role?: string | null; entity_id?: string; tags?: string[] }) => void;
  onOpenLibrary: () => void;
};

export default function AssetPanel({ assets, busy, onUpload, onConfirm, onPatchMetadata, onOpenLibrary }: Props) {
  const [role, setRole] = useState('character');
  const [entityId, setEntityId] = useState('');
  const [tags, setTags] = useState('');
  const [editingId, setEditingId] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editEntity, setEditEntity] = useState('');
  const [editTags, setEditTags] = useState('');
  const [group, setGroup] = useState('all');
  const [visible, setVisible] = useState(24);
  const [selection, setSelection] = useState<string[]>([]);
  const [saveAssetId, setSaveAssetId] = useState('');
  const [saveTitle, setSaveTitle] = useState('');
  const [saveCategory, setSaveCategory] = useState('other');
  const [saveRightsConfirmed, setSaveRightsConfirmed] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);
  const projectId = assets[0]?.project_id;
  useEffect(() => { setSelection([]); setVisible(24); }, [projectId]);
  const shown = assets.filter((a) => group === 'all' || a.role === group);


  const pending = assets.filter(asset => !asset.locked);

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

  function beginLibrarySave(asset: Asset) {
    const category = ['character', 'scene', 'prop', 'costume', 'voice'].includes(asset.role ?? '')
      ? asset.role!
      : asset.kind === 'audio' ? 'dialogue' : 'other';
    setSaveAssetId(asset.id);
    setSaveTitle(asset.entity_id || asset.filename.replace(/\.[^.]+$/, ''));
    setSaveCategory(category);
    setSaveRightsConfirmed(false);
    setSaveError('');
    setSaved(false);
  }

  async function saveToLibrary(asset: Asset) {
    if (!asset.project_id || !saveRightsConfirmed || !saveTitle.trim()) return;
    setSaveBusy(true);
    setSaveError('');
    try {
      await api(`/projects/${encodeURIComponent(asset.project_id)}/assets/${encodeURIComponent(asset.id)}/save-to-library`, {
        method: 'POST',
        body: JSON.stringify({ title: saveTitle.trim(), category: saveCategory, tags: asset.tags ?? [], confirmed_rights: true }),
      });
      setSaved(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaveBusy(false);
    }
  }

  return (
    <section className="card section-card" id="assets">
      <div className="section-heading">
        <div>
          <div className="section-kicker">① 素材准备</div>
          <h2>项目素材</h2>
          <p>上传素材，选好用途，再点击「确认使用」。确认后即可用于分镜，素材只保存在本机。</p>
        </div>
        <div className="section-icon"><Film size={17} /></div>
      </div>
      <div className="module-guide">
        <HelpCircle size={13} />
        <div>
          <p><b>这一步要做什么：</b>上传你有权使用的素材并选好用途。人物名字可以先不填；在「AI 导演制作」由编剧提出名字和名图对应，再由你二次确认。</p>
          <p><b>为什么必须做：</b>人物对照确认后才调用导演；自动匹配只是建议，需要你按缩略图核对。</p>
        </div>
      </div>
      <div className="upload-stack">
        <details open={role === 'character' || role === 'voice'}>
          <summary>可选：角色名称与标签</summary>
          <label>{role === 'character' ? '人物名称（建议与剧本一致）' : '角色 / 场景名称'}<input value={entityId} placeholder="默认使用文件名；同一人物可填相同名称" onChange={e => setEntityId(e.target.value)} /></label>
          <label>标签<input value={tags} placeholder="主角, 夜戏, 定妆" onChange={e => setTags(e.target.value)} /></label>
        </details>
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
                  // The name belongs to this file; the next upload must not
                  // inherit it and silently bind another person's face.
                  setEntityId('');
                  setTags('');
                }
              }}
            />
          </label>
        </div>
        <span className="upload-hint">支持图片、视频和音频，自动识别类型。仅上传你有权使用的素材。</span>
      </div>
      {pending.length > 0 && <div className="required-action">
        <LockKeyhole size={17} className="required-icon" />
        <div className="required-text">
          <b>有 {pending.length} 项上传素材待你选择</b>
          <small>确认会固定素材版本，AI 不能替你确认或替换。确认后这张素材才能被分镜引用。</small>
        </div>
        <button className="must-button" disabled={busy || !selection.length}
          onClick={() => onConfirm(selection)}>确认使用所选待确认素材</button>
      </div>}
      <label className="material-field"><span>素材分组</span><select value={group} onChange={(e) => { setGroup(e.target.value); setVisible(24); }}>
        <option value="all">全部素材</option><option value="character">角色</option><option value="scene">场景</option><option value="prop">道具</option><option value="voice">声音</option>
      </select></label>
      <div className="asset-list media-grid">
        {assets.length === 0 ? <div className="empty-inline">尚无素材。请只上传你提供且有权使用的素材。</div> : shown.slice(0, visible).map((asset) => (
          <div className="asset-row media-card" key={asset.id}>
            <div className="asset-type-wrap">
              {asset.kind === 'image'
                ? <ImagePreview thumbnail={`${API}/assets/${asset.id}/thumbnail`} original={`${API}/assets/${asset.id}/media`} label={asset.filename} />
                : <div className={`asset-type ${asset.kind}`}>{asset.kind === 'audio' ? 'AUD' : 'VID'}</div>}

            </div>
            <div className="asset-name">
              <b>{asset.filename}</b><small>版本 {asset.sha256.slice(0, 8)}</small>
              <span title={`校验码 ${asset.sha256.slice(0, 16)}…（用于核对文件未被改动）`}>
                {(asset.byte_size / 1024).toFixed(1)} KB
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
                <input value={editEntity} placeholder="人物名称" onChange={(event) => setEditEntity(event.target.value)} />
                <input value={editTags} placeholder="标签, 逗号分隔" onChange={(event) => setEditTags(event.target.value)} />
                <button className="text-button" onClick={() => saveEdit(asset.id)}><Check size={12} /> 保存</button>
                <button className="text-button" onClick={() => setEditingId('')}><X size={12} /> 取消</button>
              </div>}
            </div>
            <div className="asset-state">
              {asset.locked ? <span className="pill approved"><LockKeyhole size={12} />可用于分镜</span>
                : <span className="pill pending"><Clock3 size={12} />待你确认</span>}
            </div>
            {!asset.locked && <label><input type="checkbox" aria-label={`选择上传素材 ${asset.filename}`} checked={selection.includes(asset.id)}
              onChange={(e) => setSelection((old) => e.target.checked ? [...old, asset.id] : old.filter((id) => id !== asset.id))} />选择</label>}
            {!asset.locked && <button className="text-button" disabled={busy} onClick={() => onConfirm([asset.id])}>确认使用</button>}
            {asset.locked && <button className="text-button" disabled={busy || saveBusy} onClick={() => beginLibrarySave(asset)}>收藏到素材中心</button>}
            <button className="icon-button tiny" title="编辑元数据" aria-label={`编辑 ${asset.filename} 元数据`} onClick={() => beginEdit(asset)} disabled={busy}><Pencil size={12} /></button>
            {saveAssetId === asset.id && <div className="asset-library-save-form">
              {saved ? <><p role="status">已保存为素材中心候选，当前授权仍仅限本项目。完成授权复核、技术检查和人工晋升后才能使用；跨项目使用需另行授权。</p><button className="text-button" onClick={onOpenLibrary}>打开全局素材库</button></> : <>
                <label>全局素材名称<input value={saveTitle} maxLength={160} onChange={(event) => setSaveTitle(event.target.value)} /></label>
                <label>素材分类<select value={saveCategory} onChange={(event) => setSaveCategory(event.target.value)}>
                  <option value="character">人物</option><option value="scene">场景</option><option value="prop">道具</option><option value="costume">服装</option><option value="voice">音色</option><option value="dialogue">对白</option><option value="sfx">音效</option><option value="music">音乐</option><option value="other">其他</option>
                </select></label>
                <label className="asset-library-rights-confirm"><input type="checkbox" checked={saveRightsConfirmed} onChange={(event) => setSaveRightsConfirmed(event.target.checked)} />我确认已核对来源许可，允许把此锁定版本加入素材中心。它初始仍仅限当前项目，跨项目使用需另行授权。</label>
                {saveError && <p role="alert" className="required-text">{saveError}</p>}
                <button className="must-button" disabled={busy || saveBusy || !saveRightsConfirmed || !saveTitle.trim()} onClick={() => void saveToLibrary(asset)}>{saveBusy ? '正在保存…' : '保存为待审核候选'}</button>
                <button className="text-button" disabled={saveBusy} onClick={() => setSaveAssetId('')}>取消</button>
              </>}
            </div>}
          </div>
        ))}
      </div>
      {shown.length > visible && <button type="button" onClick={() => setVisible((n) => n + 24)}>加载更多素材</button>}
      <div className="guardrail-note"><LockKeyhole size={13} /><span>确认会固定素材版本；AI 无法替你确认或替换。需要修改用途时，可点击右侧编辑。</span></div>
    </section>
  );
}
