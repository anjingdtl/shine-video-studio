import { useState } from 'react';
import { ArrowUpRight, Clapperboard, Eye, Link2, Plus, Save, X } from 'lucide-react';
import type { Asset, PromptPreview, Shot, StyleVersion } from '../api';
import { continuityLabels, label, modeLabels, useAsLabels, useAsOptions } from '../constants';

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
  onCreate: (payload: Record<string, unknown>) => Promise<void>;
  onSaveBindings: (shotId: string, bindings: SaveBinding[]) => Promise<void>;
  setPreview: (preview: PromptPreview | null) => void;
};

const emptyDraft = (): DraftBinding => ({ asset_id: '', use_as: 'picture', role: '', frame_role: '' });

export default function ShotPanel({ assets, shots, currentStyle, busy, preview, onPreview, onQueue, onCreate, onSaveBindings, setPreview }: Props) {
  const [shotName, setShotName] = useState('第一镜');
  const [storyBeat, setStoryBeat] = useState('');
  const [duration, setDuration] = useState(6);
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
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([]);
  const [newCharacter, setNewCharacter] = useState('');
  const [shotSize, setShotSize] = useState('medium');
  const [movement, setMovement] = useState('static');
  const [submitting, setSubmitting] = useState(false);
  const [bindingSaving, setBindingSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const approved = assets.filter((asset) => asset.approved && asset.locked);
  // 人物选择器数据源：本项目已批准人物主资产的 entity_id + 本项目镜头已用过的人物名。
  // AI 文本不会变成已批准角色；新增人物必须走显式的新增输入。
  const characterOptions = [...new Set([
    ...approved.filter((asset) => asset.kind === 'image' && asset.role === 'character' && asset.entity_id).map((asset) => asset.entity_id as string),
    ...shots.flatMap((shot) => shot.spec.characters ?? []),
  ])];

  function addBinding() {
    setDraft((rows) => [...rows, emptyDraft()]);
  }

  function updateDraft(index: number, patch: Partial<DraftBinding>) {
    setDraft((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  }

  async function submitShot() {
    if (busy || submitting || !currentStyle || !storyBeat.trim()) return;
    const durationValue = Number(duration);
    if (!Number.isFinite(durationValue) || durationValue < 4 || durationValue > 15) {
      setFormError(`时长需在 4–15 秒之间（当前：${duration}），调整后再保存。`);
      return;
    }
    const bindings = draft
      .filter((row) => row.asset_id)
      .map((row, index) => ({
        asset_id: row.asset_id,
        use_as: row.use_as,
        role: row.role || null,
        frame_role: row.use_as === 'picture' && row.frame_role ? row.frame_role : null,
        ordinal: index,
      }));
    const characterList = selectedCharacters;
    if (speaker.trim() && !characterList.includes(speaker.trim())) characterList.push(speaker.trim());
    const dialogue = speaker.trim() && line.trim()
      ? [{ speaker_id: speaker.trim(), text: line.trim(), start_s: Number(startS), end_s: Number(endS) }]
      : [];
    const continuity = continuityMode === 'cut'
      ? { mode: 'cut' }
      : { mode: continuityMode, previous_shot_id: continuitySource || null };
    setSubmitting(true);
    setFormError('');
    try {
      // Await the server result: only a confirmed create clears the draft, so
      // a 4xx or network failure keeps every field the user typed.
      await onCreate({
        name: shotName.trim() || `镜头 ${shots.length + 1}`,
        spec: {
          schema_version: '1.0',
          story_beat: storyBeat.trim(),
          duration_target_s: durationValue,
          shot_priority: 'B',
          style_version_id: currentStyle.id,
          camera: { shot_size: shotSize, movement },
          characters: characterList,
          required_asset_ids: [...new Set(bindings.map((binding) => binding.asset_id))],
          dialogue,
          sound: { environment: environment.trim(), music_policy: 'low_or_none' },
          continuity,
          locked_story_facts: [],
        },
        bindings,
      });
    } catch {
      // The parent already surfaced the error; keep the form as-is.
      return;
    } finally {
      setSubmitting(false);
    }
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

  async function saveBindings(shotId: string) {
    const bindings = Object.entries(editBindings)
      .filter(([, value]) => value.checked)
      .map(([assetId, value], index) => ({
        asset_id: assetId,
        use_as: value.use_as,
        role: value.role || null,
        frame_role: value.use_as === 'picture' && value.frame_role ? value.frame_role : null,
        ordinal: index,
      }));
    setBindingSaving(true);
    try {
      // Only close the editor after the server confirmed the replacement.
      await onSaveBindings(shotId, bindings);
      setEditingShot('');
    } catch {
      // The parent already surfaced the error; keep the editor open for edits.
    } finally {
      setBindingSaving(false);
    }
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
      {!currentStyle && <div className="blocked-callout"><Clapperboard size={15} /><span>风格基准还没冻结，先去「素材与风格」完成冻结，才能建分镜。</span></div>}
      <div className={`shot-form ${!currentStyle ? 'is-disabled' : ''}`}>
        <label>镜头名称<input value={shotName} onChange={(event) => setShotName(event.target.value)} disabled={!currentStyle} /></label>
        <label>时长（4–15 秒）<input type="number" min={4} max={15} value={duration} onChange={(event) => setDuration(Number(event.target.value))} disabled={!currentStyle} /></label>
        <label className="wide-field">镜头意图<textarea value={storyBeat} onChange={(event) => setStoryBeat(event.target.value)} placeholder="描述画面、动作和关键剧情事实" disabled={!currentStyle} /></label>
        <div className="wide-field character-picker">
          <span className="builder-label">人物（从本项目选择；新增需显式输入）</span>
          {characterOptions.length === 0 && <span className="empty-chip">暂无可选人物：先上传并批准人物主资产，或先建过含人物的镜头。</span>}
          <div className="character-chips">
            {characterOptions.map((name) => (
              <label key={name} className={`character-chip ${selectedCharacters.includes(name) ? 'is-selected' : ''}`}>
                <input
                  type="checkbox"
                  checked={selectedCharacters.includes(name)}
                  disabled={!currentStyle}
                  onChange={() => setSelectedCharacters((current) => current.includes(name)
                    ? current.filter((value) => value !== name)
                    : [...current, name])}
                />
                {name}
              </label>
            ))}
          </div>
          <div className="character-add-row">
            <input
              value={newCharacter}
              placeholder="新增人物名（如 CHAR_C）"
              aria-label="新增人物名"
              onChange={(event) => setNewCharacter(event.target.value)}
              disabled={!currentStyle}
            />
            <button
              className="subtle-button"
              disabled={!currentStyle || !newCharacter.trim() || selectedCharacters.includes(newCharacter.trim())}
              onClick={() => { setSelectedCharacters((current) => [...current, newCharacter.trim()]); setNewCharacter(''); }}
            >
              <Plus size={12} /> 添加人物
              <span className="character-add-note">仅加入本镜人物名单，不创建主资产</span>
            </button>
          </div>
        </div>
        <label>环境声 / 地点<input value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder="雨夜码头" disabled={!currentStyle} /></label>
        <label>对白角色<select value={speaker} onChange={(event) => setSpeaker(event.target.value)} disabled={!currentStyle}>
          <option value="">（无对白）</option>
          {selectedCharacters.map((name) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <label>景别<select value={shotSize} onChange={(event) => setShotSize(event.target.value)} disabled={!currentStyle}>
          <option value="wide">远景</option><option value="full">全景</option><option value="medium">中景</option>
          <option value="medium_close">中近景</option><option value="close_up">近景</option><option value="big_close_up">特写</option>
        </select></label>
        <label>运镜<select value={movement} onChange={(event) => setMovement(event.target.value)} disabled={!currentStyle}>
          <option value="static">固定</option><option value="slow_push">缓慢推进</option><option value="pan">横移</option>
          <option value="follow">跟随</option><option value="handheld">手持</option>
        </select></label>
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
        {formError && <p className="shot-form-error" role="alert">{formError}</p>}
        <button className="primary-button wide-button" disabled={busy || submitting || !currentStyle || !storyBeat.trim()} onClick={submitShot}><Plus size={14} /> {submitting ? '正在保存…' : '保存分镜'}</button>
      </div>
      <div className="shot-list">
        {shots.map((shot) => (
          <div className="shot-block" key={shot.id}>
            <div className="shot-row">
              <div className="shot-number">{String(shot.revision).padStart(2, '0')}</div>
              <div className="shot-content">
                <b>{shot.name}</b>
                <span>{String(shot.spec.story_beat ?? '')}</span>
                <small>连续性：{label(continuityLabels, String(shot.spec.continuity?.mode ?? 'cut'))}</small>
                <div className="binding-chips">
                  {shot.bindings.length === 0 ? <span className="empty-chip">无引用</span> : shot.bindings.map((binding) => (
                    <span className="binding-chip" key={binding.id}>{label(useAsLabels, binding.use_as)} · {binding.filename || binding.asset_id.slice(0, 6)}</span>
                  ))}
                </div>
              </div>
              <button className="small-action" onClick={() => onPreview(shot.id)}><Eye size={12} /> 预览提示词</button>
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
                <button className="primary-button" disabled={bindingSaving} onClick={() => saveBindings(shot.id)}><Save size={13} /> {bindingSaving ? '正在保存…' : '保存绑定'}</button>
                <button className="secondary-button" disabled={bindingSaving} onClick={() => setEditingShot('')}>取消</button>
              </div>
            </div>}
          </div>
        ))}
        {shots.length === 0 && <div className="empty-inline">冻结风格后创建第一条分镜。</div>}
      </div>
      {preview && <div className="prompt-preview">
        <div className="prompt-head">
          <div><b>H3 提示词预览</b><span>生成方式：{label(modeLabels, preview.mode)} · 编译器 {preview.compiler_version} · 指纹 {preview.prompt_sha256.slice(0, 10)}…</span></div>
          <button className="icon-button tiny" onClick={() => setPreview(null)}><X size={13} /></button>
        </div>
        {preview.warnings.length > 0 && <div className="prompt-warnings">{preview.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
        <div className="label-row">{Object.entries(preview.labels).map(([slot, assetId]) => <code key={slot}>{slot} → {assetId.slice(0, 8)}</code>)}</div>
        <pre>{preview.compiled_prompt}</pre>
      </div>}
    </div>
  );
}
