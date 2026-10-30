import { useState } from 'react';
import { ArrowUpRight, Clapperboard, Eye, Link2, Plus, Save, X } from 'lucide-react';
import type { Asset, PromptPreview, Shot, StyleVersion } from '../api';
import { useAsOptions } from '../constants';

type DraftBinding = { asset_id: string; use_as: string; role: string; frame_role: string };
type SaveBinding = { asset_id: string; use_as: string; role: string | null; frame_role: string | null; ordinal: number };

type Props = {
  assets: Asset[];
  shots: Shot[];
  currentStyle?: StyleVersion;
  busy: boolean;
  preview: PromptPreview | null;
  onPreview: (shotId: string) => void;
  onQueue: (shot: Shot) => void;
  onCreate: (payload: Record<string, unknown>) => void;
  onSaveBindings: (shotId: string, bindings: SaveBinding[]) => void;
  setPreview: (preview: PromptPreview | null) => void;
};

const emptyDraft = (): DraftBinding => ({ asset_id: '', use_as: 'picture', role: '', frame_role: '' });

export default function ShotPanel({ assets, shots, currentStyle, busy, preview, onPreview, onQueue, onCreate, onSaveBindings, setPreview }: Props) {
  const [shotName, setShotName] = useState('第一镜');
  const [storyBeat, setStoryBeat] = useState('');
  const [duration, setDuration] = useState(6);
  const [characters, setCharacters] = useState('');
  const [environment, setEnvironment] = useState('');
  const [speaker, setSpeaker] = useState('');
  const [line, setLine] = useState('');
  const [startS, setStartS] = useState(0.4);
  const [endS, setEndS] = useState(2.4);
  const [draft, setDraft] = useState<DraftBinding[]>([emptyDraft()]);
  const [editingShot, setEditingShot] = useState('');
  const [editBindings, setEditBindings] = useState<Record<string, { use_as: string; role: string; frame_role: string; checked: boolean }>>({});
  const [continuityMode, setContinuityMode] = useState<'cut' | 'last_frame' | 'hard_frame_anchor'>('cut');
  const [continuitySource, setContinuitySource] = useState('');
  const approved = assets.filter((asset) => asset.approved && asset.locked);

  function addBinding() {
    setDraft((rows) => [...rows, emptyDraft()]);
  }

  function updateDraft(index: number, patch: Partial<DraftBinding>) {
    setDraft((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  }

  function submitShot() {
    if (!currentStyle || !storyBeat.trim()) return;
    const bindings = draft
      .filter((row) => row.asset_id)
      .map((row, index) => ({
        asset_id: row.asset_id,
        use_as: row.use_as,
        role: row.role || null,
        frame_role: row.use_as === 'picture' && row.frame_role ? row.frame_role : null,
        ordinal: index,
      }));
    const characterList = characters.split(',').map((value) => value.trim()).filter(Boolean);
    if (speaker.trim() && !characterList.includes(speaker.trim())) characterList.push(speaker.trim());
    const dialogue = speaker.trim() && line.trim()
      ? [{ speaker_id: speaker.trim(), text: line.trim(), start_s: Number(startS), end_s: Number(endS) }]
      : [];
    const continuity = continuityMode === 'cut'
      ? { mode: 'cut' }
      : { mode: continuityMode, previous_shot_id: continuitySource || null };
    onCreate({
      name: shotName.trim() || `镜头 ${shots.length + 1}`,
      spec: {
        schema_version: '1.0',
        story_beat: storyBeat.trim(),
        duration_target_s: Number(duration),
        shot_priority: 'B',
        style_version_id: currentStyle.id,
        camera: { shot_size: 'medium', movement: 'static' },
        characters: characterList,
        required_asset_ids: [...new Set(bindings.map((binding) => binding.asset_id))],
        dialogue,
        sound: { environment: environment.trim(), music_policy: 'low_or_none' },
        continuity,
        locked_story_facts: [],
      },
      bindings,
    });
    setStoryBeat('');
    setDraft([emptyDraft()]);
  }

  function beginBindings(shot: Shot) {
    setEditingShot(shot.id);
    const map: Record<string, { use_as: string; role: string; frame_role: string; checked: boolean }> = {};
    for (const asset of approved) {
      const existing = shot.bindings.find((binding) => binding.asset_id === asset.id);
      map[asset.id] = {
        use_as: existing?.use_as ?? (asset.kind === 'image' ? 'picture' : asset.kind),
        role: existing?.role ?? '',
        frame_role: existing?.frame_role ?? '',
        checked: Boolean(existing),
      };
    }
    setEditBindings(map);
  }

  function saveBindings(shotId: string) {
    const bindings = Object.entries(editBindings)
      .filter(([, value]) => value.checked)
      .map(([assetId, value], index) => ({
        asset_id: assetId,
        use_as: value.use_as,
        role: value.role || null,
        frame_role: value.use_as === 'picture' && value.frame_role ? value.frame_role : null,
        ordinal: index,
      }));
    onSaveBindings(shotId, bindings);
    setEditingShot('');
  }

  return (
    <div className="card section-card storyboard-card" id="shots">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">03 / SHOT WORKSPACE</div>
          <h2>分镜与独立素材绑定</h2>
          <p>每个镜头独立保存引用；编号由任务级确定性程序重新映射。</p>
        </div>
        <div className="section-icon plum"><Clapperboard size={17} /></div>
      </div>
      {!currentStyle && <div className="blocked-callout"><Clapperboard size={15} /><span>Style Bible 尚未冻结，分镜和生产入口已锁定。</span></div>}
      <div className={`shot-form ${!currentStyle ? 'is-disabled' : ''}`}>
        <label>镜头名称<input value={shotName} onChange={(event) => setShotName(event.target.value)} disabled={!currentStyle} /></label>
        <label>时长（4–15 秒）<input type="number" min={4} max={15} value={duration} onChange={(event) => setDuration(Number(event.target.value))} disabled={!currentStyle} /></label>
        <label className="wide-field">镜头意图<textarea value={storyBeat} onChange={(event) => setStoryBeat(event.target.value)} placeholder="描述画面、动作和关键剧情事实" disabled={!currentStyle} /></label>
        <label>人物（逗号分隔）<input value={characters} onChange={(event) => setCharacters(event.target.value)} placeholder="CHAR_A, CHAR_B" disabled={!currentStyle} /></label>
        <label>环境声 / 地点<input value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder="雨夜码头" disabled={!currentStyle} /></label>
        <label>对白角色<input value={speaker} onChange={(event) => setSpeaker(event.target.value)} placeholder="CHAR_A" disabled={!currentStyle} /></label>
        <label>对白内容<input value={line} onChange={(event) => setLine(event.target.value)} placeholder="账册在哪儿？" disabled={!currentStyle} /></label>
        <label>开始秒<input type="number" min={0} step={0.1} value={startS} onChange={(event) => setStartS(Number(event.target.value))} disabled={!currentStyle} /></label>
        <label>结束秒<input type="number" min={0} step={0.1} value={endS} onChange={(event) => setEndS(Number(event.target.value))} disabled={!currentStyle} /></label>
        <label>与上一镜的关系<select value={continuityMode} onChange={(event) => setContinuityMode(event.target.value as 'cut' | 'last_frame' | 'hard_frame_anchor')} disabled={!currentStyle}>
          <option value="cut">独立镜头（CUT）</option>
          <option value="last_frame">软连续：引用上一镜末帧（last_frame）</option>
          <option value="hard_frame_anchor">硬帧锚定（hard_frame_anchor，需已验证的工作流能力）</option>
        </select></label>
        {continuityMode !== 'cut' && <label>上一镜<select value={continuitySource} onChange={(event) => setContinuitySource(event.target.value)} disabled={!currentStyle}>
          <option value="">请选择来源镜头</option>
          {shots.map((shot) => <option key={shot.id} value={shot.id}>{shot.name}</option>)}
        </select></label>}
        <div className="wide-field binding-builder">
          <span className="builder-label">镜头引用素材</span>
          {draft.map((row, index) => (
            <div className="binding-row" key={index}>
              <select value={row.asset_id} onChange={(event) => updateDraft(index, { asset_id: event.target.value })}>
                <option value="">选择已批准素材</option>
                {approved.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename} · {asset.kind}</option>)}
              </select>
              <select value={row.use_as} onChange={(event) => updateDraft(index, { use_as: event.target.value })}>
                {useAsOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <input value={row.role} placeholder="角色 / 实体（可选）" onChange={(event) => updateDraft(index, { role: event.target.value })} />
              {row.use_as === 'picture' && <select value={row.frame_role} onChange={(event) => updateDraft(index, { frame_role: event.target.value })}>
                <option value="">无帧位</option><option value="first_frame">首帧</option><option value="last_frame">末帧</option><option value="keyframe">关键帧</option>
              </select>}
              <button className="icon-button tiny" onClick={() => setDraft((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><X size={12} /></button>
            </div>
          ))}
          <button className="subtle-button builder-add" onClick={addBinding} disabled={!currentStyle}><Plus size={13} /> 添加引用</button>
        </div>
        <button className="primary-button wide-button" disabled={busy || !currentStyle || !storyBeat.trim()} onClick={submitShot}><Plus size={14} /> 保存分镜</button>
      </div>
      <div className="shot-list">
        {shots.map((shot) => (
          <div className="shot-block" key={shot.id}>
            <div className="shot-row">
              <div className="shot-number">{String(shot.revision).padStart(2, '0')}</div>
              <div className="shot-content">
                <b>{shot.name}</b>
                <span>{String(shot.spec.story_beat ?? '')}</span>
                <small>连续性：{({ last_frame: 'SOFT_REFERENCE_CONTINUITY', hard_frame_anchor: 'HARD_FRAME_ANCHOR_CONTINUITY · 待测试', video_reference: 'VIDEO_REFERENCE_CONTINUITY · 传输未验收' } as Record<string, string>)[String(shot.spec.continuity?.mode)] ?? String(shot.spec.continuity?.mode ?? 'cut')}</small>
                <div className="binding-chips">
                  {shot.bindings.length === 0 ? <span className="empty-chip">无引用</span> : shot.bindings.map((binding) => (
                    <span className="binding-chip" key={binding.id}>{binding.use_as} · {binding.filename || binding.asset_id.slice(0, 6)}</span>
                  ))}
                </div>
              </div>
              <button className="small-action" onClick={() => onPreview(shot.id)}><Eye size={12} /> 预览 Prompt</button>
              <button className="small-action" onClick={() => beginBindings(shot)}><Link2 size={12} /> 绑定</button>
              <button className="small-action" disabled={!currentStyle || busy} onClick={() => onQueue(shot)}><ArrowUpRight size={12} /> 加入队列</button>
            </div>
            {editingShot === shot.id && <div className="binding-editor">
              {approved.length === 0 && <span className="empty-inline">没有已批准并锁定的素材。</span>}
              {approved.map((asset) => (
                <div className="binding-edit-row" key={asset.id}>
                  <label className="check-label">
                    <input
                      type="checkbox"
                      checked={editBindings[asset.id]?.checked ?? false}
                      onChange={(event) => setEditBindings((rows) => ({ ...rows, [asset.id]: { ...rows[asset.id], checked: event.target.checked } }))}
                    />
                    {asset.filename}
                  </label>
                  <select
                    value={editBindings[asset.id]?.use_as ?? 'picture'}
                    onChange={(event) => setEditBindings((rows) => ({ ...rows, [asset.id]: { ...rows[asset.id], use_as: event.target.value } }))}
                  >
                    {useAsOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  <input
                    value={editBindings[asset.id]?.role ?? ''}
                    placeholder="角色 / 实体"
                    onChange={(event) => setEditBindings((rows) => ({ ...rows, [asset.id]: { ...rows[asset.id], role: event.target.value } }))}
                  />
                  {editBindings[asset.id]?.use_as === 'picture' && <select
                    value={editBindings[asset.id]?.frame_role ?? ''}
                    onChange={(event) => setEditBindings((rows) => ({ ...rows, [asset.id]: { ...rows[asset.id], frame_role: event.target.value } }))}
                  >
                    <option value="">无帧位</option><option value="first_frame">首帧</option><option value="last_frame">末帧</option><option value="keyframe">关键帧</option>
                  </select>}
                </div>
              ))}
              <div className="editor-actions">
                <button className="primary-button" onClick={() => saveBindings(shot.id)}><Save size={13} /> 保存绑定</button>
                <button className="secondary-button" onClick={() => setEditingShot('')}>取消</button>
              </div>
            </div>}
          </div>
        ))}
        {shots.length === 0 && <div className="empty-inline">冻结风格后创建第一条分镜。</div>}
      </div>
      {preview && <div className="prompt-preview">
        <div className="prompt-head">
          <div><b>H3 编译预览</b><span>{preview.mode} · {preview.compiler_version} · SHA {preview.prompt_sha256.slice(0, 10)}…</span></div>
          <button className="icon-button tiny" onClick={() => setPreview(null)}><X size={13} /></button>
        </div>
        {preview.warnings.length > 0 && <div className="prompt-warnings">{preview.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
        <div className="label-row">{Object.entries(preview.labels).map(([label, assetId]) => <code key={label}>{label} → {assetId.slice(0, 8)}</code>)}</div>
        <pre>{preview.compiled_prompt}</pre>
      </div>}
    </div>
  );
}
