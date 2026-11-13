/**
 * ③ 镜头拆解与 ④ 素材准备里的三个「二级页面」。
 *
 * 编辑的都是**最终提交的内容**：镜头提示词会原样交给 H3，素材提示词会原样
 * 交给 Qwen-Image。MiniMax-H3 与 Qwen-Image-2.1 的官方规范在这里只做软检查，
 * 列出建议而不阻止保存或冻结。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Check, Lock, Plus, RefreshCw, Save, Trash2, Unlock, X,
} from 'lucide-react';
import {
  ApiError, addShotRequestedAsset, fetchMaterialPrompt, fetchShotPrompt,
  saveMaterialPrompt, saveShotPrompt, saveShotSubtitle, unfreezeMaterialPrompt,
  unfreezeShotPrompt, type MaterialPromptView, type RequestedAssetInput,
  type ShotPromptView, type ShotSubtitleLine, type SpecWarning,
} from '../api';

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

/* ============================ 共用外壳 ============================ */

function EditorModal({ title, kicker, onClose, children, footer, badges }: {
  title: string;
  kicker: string;
  onClose: () => void;
  children: React.ReactNode;
  footer: React.ReactNode;
  badges?: React.ReactNode;
}) {
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  useEffect(() => {
    const first = body.current?.querySelector<HTMLElement>('textarea, input, select');
    first?.focus();
  }, []);
  return <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title}
    onClick={onClose}>
    <div className="editor-modal" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}><X size={16} /></button>
      <header className="editor-head">
        <div>
          <div className="section-kicker">{kicker}</div>
          <h2>{title}</h2>
        </div>
        <div className="editor-badges">{badges}</div>
      </header>
      <div className="editor-body" ref={body}>{children}</div>
      <footer className="editor-actions">{footer}</footer>
    </div>
  </div>;
}

/** 冻结版暂不生效的原因；与后端 REASON_LABELS 一一对应。 */
const MANUAL_REASON_TEXT: Record<string, string> = {
  MANUAL_PROMPT_REFERENCES_CHANGED: '参考素材变了，提示词里的角色编号已不对应',
  MANUAL_PROMPT_DIALOGUE_CHANGED: '这一镜的台词变了，提示词不再包含现在的台词',
  MANUAL_PROMPT_DURATION_CHANGED: '这一镜的时长变了，提示词里的时间点不再合适',
  MANUAL_PROMPT_MODE_CHANGED: '这一次的生片方式变了，提示词不是按它写的',
};

function WarningList({ warnings }: { warnings: Array<SpecWarning | string> }) {
  if (!warnings.length) {
    return <p className="spec-ok"><Check size={12} /> 没有发现规范偏差。</p>;
  }
  return <ul className="spec-checklist">
    {warnings.map((row, index) => {
      const text = typeof row === 'string' ? row : row.message;
      return <li key={`${index}-${text.slice(0, 12)}`} className="spec-warning">
        <AlertTriangle size={12} /><span>{text}</span>
      </li>;
    })}
  </ul>;
}

/* ============================ ② 镜头 H3 提示词 ============================ */

export function ShotPromptModal({ projectId, shot, onClose, onChanged }: {
  projectId: string;
  shot: { shot_id: string; name: string; revision: number; duration_target_s: number };
  onClose: () => void;
  onChanged?: (message: string) => void;
}) {
  const [view, setView] = useState<ShotPromptView | null>(null);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState('');
  const [savedWarnings, setSavedWarnings] = useState<SpecWarning[]>([]);

  const load = useCallback(async (compiler = false) => {
    setProblem('');
    try {
      const body = await fetchShotPrompt(projectId, shot.shot_id, compiler);
      setView(body);
      setPrompt(body.frozen?.prompt ?? body.prompt ?? '');
      setSavedWarnings(body.frozen?.spec_report ?? []);
    } catch (error) {
      setProblem(describeError(error));
    }
  }, [projectId, shot.shot_id]);

  useEffect(() => { void load(); }, [load]);

  async function save(freeze: boolean) {
    if (!view?.mode) { setProblem('还没有可用的生成模式，先确认风格与参考素材'); return; }
    setBusy(freeze ? 'freeze' : 'save'); setProblem(''); setResult('');
    try {
      const body = await saveShotPrompt(projectId, shot.shot_id, {
        prompt, mode: view.mode, freeze, expected_revision: shot.revision,
      });
      setSavedWarnings(body.warnings);
      setResult(freeze
        ? `已冻结第 ${body.revision} 版：下次生成会原样提交这段提示词。参考素材变化后需要重新确认。`
        : `第 ${body.revision} 版草稿已保存；冻结后才会用于生成。`);
      onChanged?.(freeze ? '镜头提示词已冻结。' : '镜头提示词草稿已保存。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  async function unfreeze() {
    setBusy('unfreeze'); setProblem(''); setResult('');
    try {
      await unfreezeShotPrompt(projectId, shot.shot_id);
      setResult('已解除冻结；这一镜回到编译器生成的提示词。');
      onChanged?.('镜头提示词已解除冻结。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  const frozen = view?.frozen ?? null;
  const warnings = savedWarnings.length ? savedWarnings : (frozen?.spec_report ?? []);

  return <EditorModal
    title={`镜头提示词 · ${shot.name}`}
    kicker="第 3 阶段 · 人工修订"
    onClose={onClose}
    badges={<>
      {view?.mode && <span className="head-chip">{view.mode}</span>}
      {frozen
        ? <span className="head-chip ok"><Lock size={10} /> 人工冻结 v{frozen.revision}</span>
        : <span className="head-chip">编译器预览</span>}
      <span className="head-chip">{view?.has_bindings ? '已绑定参考' : '暂无参考素材'}</span>
    </>}
    footer={<>
      <button type="button" className="btn-secondary" disabled={busy !== ''}
        onClick={() => void load(true)}><RefreshCw size={12} /> 重新加载编译器版本</button>
      {frozen && <button type="button" className="btn-text" disabled={busy !== ''}
        onClick={() => void unfreeze()}><Unlock size={12} /> 解除冻结</button>}
      <span className="editor-spacer" />
      <button type="button" className="btn-secondary" disabled={busy !== '' || !prompt.trim()}
        onClick={() => void save(false)}><Save size={12} /> 保存草稿</button>
      <button type="button" className="btn-primary" disabled={busy !== '' || !prompt.trim() || !view?.mode}
        onClick={() => void save(true)}><Lock size={12} /> 保存并冻结</button>
    </>}
  >
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}</p>}
    {view?.blocked && <div className="blocked-callout">
      <AlertTriangle size={14} />
      <span>{view.blocked.message ?? view.blocked.code ?? '当前无法编译提示词'}</span>
    </div>}
    {view?.manual_status && <div className="blocked-callout">
      <AlertTriangle size={14} />
      <span>
        冻结版当前不生效：{MANUAL_REASON_TEXT[view.manual_status] ?? view.manual_status}。
        这次生成会用编译器版本；重新确认并冻结后就恢复你的文字。
      </span>
    </div>}
    <p className="run-hint">
      本项目只走 Ref2VA：每一镜都必须先有参考素材（人物/场景/道具至少一项），
      纯文本模式不会被使用。官方规范是建议：画面描述用英文，台词与画面文字保留原文；
      每句台词用一对 &lt;d&gt;&lt;/d&gt; 包住。偏离规范只会在这里列出，不会阻止保存、冻结或生成。
    </p>
    <label className="editor-field">
      <span>提示词（冻结后原样提交给 MiniMax-H3）</span>
      <textarea rows={18} value={prompt} spellCheck={false}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="integration_multimodal_description: ..." />
    </label>
    {view && Object.keys(view.sections).length > 0 && <details className="editor-details">
      <summary>识别到的官方 section（{Object.keys(view.sections).length}）</summary>
      <ul className="spec-sections">
        {Object.entries(view.sections).map(([name, text]) => <li key={name}>
          <b>{name}</b><span>{text.slice(0, 120)}{text.length > 120 ? '…' : ''}</span>
        </li>)}
      </ul>
    </details>}
    <section className="editor-section">
      <h3>规范检查（不阻断）</h3>
      <WarningList warnings={warnings} />
    </section>
    {view?.warnings?.length ? <section className="editor-section">
      <h3>编译期提示</h3>
      <WarningList warnings={view.warnings} />
    </section> : null}
  </EditorModal>;
}

/* ============================ ② 用户指定增加素材 ============================ */

const FAMILIES: Array<{ id: RequestedAssetInput['family']; label: string }> = [
  { id: 'character', label: '人物' },
  { id: 'scene', label: '场景' },
  { id: 'prop', label: '道具' },
];

export function ShotAssetRequestModal({ projectId, shot, onClose, onChanged }: {
  projectId: string;
  shot: { shot_id: string; name: string };
  onClose: () => void;
  onChanged?: (message: string) => void;
}) {
  const [family, setFamily] = useState<RequestedAssetInput['family']>('prop');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [carrier, setCarrier] = useState<'object' | 'paper' | 'screen'>('object');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState('');

  async function submit() {
    if (!name.trim()) { setProblem('先给这个素材起一个名字（分镜与素材清单都用它）'); return; }
    setBusy(true); setProblem(''); setResult('');
    try {
      const body = await addShotRequestedAsset(projectId, shot.shot_id, {
        family, name: name.trim(), description: description.trim(),
        carrier: family === 'prop' ? carrier : null,
      });
      setResult(`已加入本镜素材需求（镜头第 ${body.shot_revision} 版）。后台会为这一镜排队一次 LLM 改写，把它写进画面动作；改写失败也不影响素材需求。`);
      onChanged?.('已为本镜增加指定素材。');
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return <EditorModal
    title={`指定增加素材 · ${shot.name}`}
    kicker="第 3 阶段 · 用户指定"
    onClose={onClose}
    footer={<>
      <span className="editor-spacer" />
      <button type="button" className="btn-secondary" onClick={onClose}>关闭</button>
      <button type="button" className="btn-primary" disabled={busy || !name.trim()}
        onClick={() => void submit()}><Plus size={12} /> 加入本镜并送素材准备</button>
    </>}
  >
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}</p>}
    <p className="run-hint">
      你指定的素材会成为这一镜的必需需求，并出现在「拍摄素材准备」清单里。
      系统还会只为这一镜重跑一次 LLM 分镜，把新素材自然写进画面；模型不可用或失败时需求仍然保留。
    </p>
    <div className="editor-grid">
      <label className="editor-field"><span>类别</span>
        <select value={family} onChange={(event) => setFamily(event.target.value as RequestedAssetInput['family'])}>
          {FAMILIES.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}
        </select>
      </label>
      <label className="editor-field"><span>名称</span>
        <input value={name} onChange={(event) => setName(event.target.value)}
          placeholder={family === 'character' ? '如：老周' : family === 'scene' ? '如：雨夜巷口' : '如：旧怀表'} />
      </label>
    </div>
    {family === 'prop' && <label className="editor-field"><span>道具载体</span>
      <select value={carrier} onChange={(event) => setCarrier(event.target.value as 'object' | 'paper' | 'screen')}>
        <option value="object">实物（静物）</option>
        <option value="paper">纸质内容（信件／告示）</option>
        <option value="screen">屏幕内容</option>
      </select>
    </label>}
    <label className="editor-field"><span>用途／外观描述（可选，给素材准备参考）</span>
      <textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)}
        placeholder="它在这一镜里是什么、长什么样、为什么必须有它。" />
    </label>
  </EditorModal>;
}

/* ============================ ② 每镜字幕 ============================ */

export function ShotSubtitleModal({ projectId, shot, initialLines, onClose, onChanged }: {
  projectId: string;
  shot: { shot_id: string; name: string; duration_target_s: number };
  initialLines: ShotSubtitleLine[];
  onClose: () => void;
  onChanged?: (message: string) => void;
}) {
  const [lines, setLines] = useState<ShotSubtitleLine[]>(
    initialLines.length ? initialLines : [{ start_s: 0, end_s: Math.min(3, shot.duration_target_s), text: '' }]);
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState('');

  function update(index: number, patch: Partial<ShotSubtitleLine>) {
    setLines((current) => current.map((row, position) => (
      position === index ? { ...row, ...patch } : row)));
  }

  async function save(freeze: boolean) {
    setBusy(freeze ? 'freeze' : 'save'); setProblem(''); setResult('');
    try {
      const cleaned = lines
        .filter((row) => row.text.trim())
        .map((row) => ({ start_s: Number(row.start_s) || 0, end_s: Number(row.end_s) || 0, text: row.text.trim() }));
      const body = await saveShotSubtitle(projectId, shot.shot_id, { lines: cleaned, freeze });
      setResult(freeze
        ? `已冻结第 ${body.revision} 版字幕（${cleaned.length} 行）。它属于外挂字幕轨，出片时合成，不烧进画面。`
        : `第 ${body.revision} 版字幕草稿已保存；冻结后才会进入本集字幕轨。`);
      onChanged?.(freeze ? '镜头字幕已冻结。' : '镜头字幕草稿已保存。');
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  return <EditorModal
    title={`镜头字幕 · ${shot.name}`}
    kicker="第 3 阶段 · 外挂字幕"
    onClose={onClose}
    badges={<span className="head-chip">本镜 {shot.duration_target_s.toFixed(1)} 秒</span>}
    footer={<>
      <button type="button" className="btn-text" disabled={busy !== ''}
        onClick={() => setLines((current) => [...current, {
          start_s: 0, end_s: Math.min(3, shot.duration_target_s), text: '' }])}>
        <Plus size={12} /> 添加一行
      </button>
      <span className="editor-spacer" />
      <button type="button" className="btn-secondary" disabled={busy !== ''}
        onClick={() => void save(false)}><Save size={12} /> 保存草稿</button>
      <button type="button" className="btn-primary" disabled={busy !== ''}
        onClick={() => void save(true)}><Lock size={12} /> 保存并冻结</button>
    </>}
  >
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}</p>}
    <p className="run-hint">
      时间按这一镜内部计算（0 到本镜时长）。冻结后可在这一集底部生成字幕轨，
      再在后期选择并批准；字幕不会改变画面，也不进入 H3 提示词。
    </p>
    <div className="subtitle-editor">
      {lines.map((line, index) => <div key={index} className="subtitle-editor-row">
        <input type="number" min={0} max={shot.duration_target_s} step={0.1}
          value={line.start_s} onChange={(event) => update(index, { start_s: Number(event.target.value) })}
          aria-label={`第 ${index + 1} 行开始秒`} />
        <input type="number" min={0} max={shot.duration_target_s} step={0.1}
          value={line.end_s} onChange={(event) => update(index, { end_s: Number(event.target.value) })}
          aria-label={`第 ${index + 1} 行结束秒`} />
        <input value={line.text} onChange={(event) => update(index, { text: event.target.value })}
          placeholder="这一行字幕内容" aria-label={`第 ${index + 1} 行字幕`} />
        <button type="button" className="btn-text" aria-label={`删除第 ${index + 1} 行`}
          disabled={lines.length === 1}
          onClick={() => setLines((current) => current.filter((_, position) => position !== index))}>
          <Trash2 size={12} />
        </button>
      </div>)}
    </div>
  </EditorModal>;
}

/* ============================ ④ 素材提示词 ============================ */

export function MaterialPromptModal({ projectId, requirement, onClose, onChanged }: {
  projectId: string;
  requirement: { requirement_id: string; name: string; family: string; description: string };
  onClose: () => void;
  onChanged?: (message: string) => void;
}) {
  const [view, setView] = useState<MaterialPromptView | null>(null);
  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const body = await fetchMaterialPrompt(projectId, requirement.requirement_id);
      setView(body);
      setPrompt(body.prompt);
      setNegative(body.negative_prompt);
      setWarnings(body.warnings);
    } catch (error) {
      setProblem(describeError(error));
    }
  }, [projectId, requirement.requirement_id]);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setBusy('save'); setProblem(''); setResult('');
    try {
      const body = await saveMaterialPrompt(projectId, requirement.requirement_id, {
        prompt: prompt.trim(), negative_prompt: negative.trim(),
      });
      setWarnings(body.warnings);
      setResult('提示词已冻结：这一项下一次出图会直接使用它，不再调用生成外观描述的模型。');
      onChanged?.('素材提示词已冻结。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  async function unfreeze() {
    setBusy('unfreeze'); setProblem(''); setResult('');
    try {
      await unfreezeMaterialPrompt(projectId, requirement.requirement_id);
      setResult('已解除冻结；这一项回到「由模型生成外观描述」的流程。');
      onChanged?.('素材提示词已解除冻结。');
      await load();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  }

  return <EditorModal
    title={`素材提示词 · ${requirement.name}`}
    kicker="第 4 阶段 · 人工修订"
    onClose={onClose}
    badges={<>
      <span className="head-chip">{requirement.family === 'character' ? '人物'
        : requirement.family === 'scene' ? '场景' : '道具'}</span>
      {view?.frozen
        ? <span className="head-chip ok"><Lock size={10} /> 已冻结 {view.frozen.chars} 字</span>
        : <span className="head-chip">{view?.source === 'composed' ? '模型提示词' : '尚未生成'}</span>}
    </>}
    footer={<>
      {view?.frozen && <button type="button" className="btn-text" disabled={busy !== ''}
        onClick={() => void unfreeze()}><Unlock size={12} /> 解除冻结</button>}
      <span className="editor-spacer" />
      <button type="button" className="btn-secondary" disabled={busy !== ''}
        onClick={() => void load()}><RefreshCw size={12} /> 重新读取</button>
      <button type="button" className="btn-primary" disabled={busy !== '' || !prompt.trim()}
        onClick={() => void save()}><Lock size={12} /> 保存并冻结</button>
    </>}
  >
    {problem && <p role="alert" className="form-error">{problem}</p>}
    {result && <p className="run-hint ok-hint"><Check size={12} /> {result}</p>}
    {requirement.description && <p className="run-hint">需求描述：{requirement.description.slice(0, 160)}</p>}
    {view?.blocked === 'MATERIAL_BRIEF_NOT_GENERATED' && <div className="blocked-callout">
      <AlertTriangle size={14} />
      <span>这一项还没有模型生成的外观描述。你可以直接写一条英文自然句提示词并冻结，下一次出图就会使用它。</span>
    </div>}
    <label className="editor-field">
      <span>正向提示词（Qwen-Image-2.1；建议英文自然句，按主体→环境→光线→材质→构图→风格）</span>
      <textarea rows={12} value={prompt} spellCheck={false}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="A character reference photograph for a cinematic narrative production: ..." />
    </label>
    <label className="editor-field">
      <span>负向提示词（可选）</span>
      <textarea rows={4} value={negative} spellCheck={false}
        onChange={(event) => setNegative(event.target.value)}
        placeholder="no text, no lettering, no collage" />
    </label>
    <section className="editor-section">
      <h3>Qwen-Image-2.1 规范建议（不阻断）</h3>
      <WarningList warnings={warnings} />
      {view?.standard && <p className="run-hint">
        建议层次：{view.standard.layer_order.join(' → ')}；不要使用质量口号（
        {view.standard.quality_slogans.slice(0, 6).join('、')}…）。
      </p>}
    </section>
  </EditorModal>;
}
