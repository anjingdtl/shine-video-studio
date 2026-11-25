import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, RefreshCw, Sparkles, Trash2, Upload } from 'lucide-react';

export type StyleLoraConfig = {
  lora_id: string;
  lora_name: string;
  sha256: string;
  strength_model: number;
  trigger_word: string;
};

export type StyleLoraAssetItem = {
  id: string;
  display_name: string;
  filename: string;
  storage_path: string;
  sha256: string;
  file_size_bytes: number;
  default_strength: number;
  trigger_word: string;
  origin: 'discovered' | 'imported' | string;
  comfy_lora_name: string | null;
  status: 'READY' | 'COMFY_UNREACHABLE' | 'NEEDS_COMFY_REFRESH' | 'MISSING_FILE' | 'HASH_MISMATCH' | string;
  created_at: string;
  updated_at: string;
};

type Props = {
  value: StyleLoraConfig[];
  onChange: (next: StyleLoraConfig[]) => void;
  loraSupported: boolean;
  disabled?: boolean;
  onMessage?: (msg: string) => void;
};

const MAX_STYLE_LORAS = 3;
const TRIGGER_MAX_LEN = 160;
const TRIGGER_ASCII_RE = /^[\x20-\x7e]{0,160}$/;
const TRIGGER_RESERVED_RE = /<(?:Subject|Audio|Video|Picture)\b|At\s+\d{1,2}:\d{2}(?:\.\d{1,3})?/i;

function validateTriggerClient(raw: string): string | null {
  const cleaned = raw.trim();
  if (!cleaned) return null;
  if (cleaned.length > TRIGGER_MAX_LEN) {
    return `触发词长度不能超过 ${TRIGGER_MAX_LEN} 个字符。`;
  }
  if (!TRIGGER_ASCII_RE.test(cleaned)) {
    return '触发词仅允许 ASCII 可打印字符（禁止中文或控制字符）。';
  }
  if (TRIGGER_RESERVED_RE.test(cleaned)) {
    return '触发词禁止包含 H3 保留标签（Subject/Audio/Video/Picture）或时间轴标记。';
  }
  return null;
}

const STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  READY: { label: 'ComfyUI 已可见', tone: 'ok' },
  COMFY_UNREACHABLE: { label: '已登记（离线未探测）', tone: 'neutral' },
  NEEDS_COMFY_REFRESH: { label: 'ComfyUI 暂未发现（需刷新/重启）', tone: 'warn' },
  MISSING_FILE: { label: '磁盘文件缺失', tone: 'bad' },
  HASH_MISMATCH: { label: '文件哈希不匹配', tone: 'bad' },
};

function parseApiError(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback;
  const obj = payload as Record<string, unknown>;
  const detail = obj.detail;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    const msg = typeof d.message === 'string' ? d.message : '';
    const code = typeof d.code === 'string' ? d.code : '';
    if (msg && code) return `${msg}（${code}）`;
    if (msg) return msg;
    if (code) return code;
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (typeof obj.message === 'string' && obj.message.trim()) return obj.message;
  return fallback;
}

function clampStrength(raw: number): number {
  if (!Number.isFinite(raw)) return 0.65;
  const clamped = Math.max(0, Math.min(1.5, raw));
  return Math.round(clamped * 100) / 100;
}

export function StyleLoraAssemblyPanel({
  value,
  onChange,
  loraSupported,
  disabled = false,
  onMessage,
}: Props) {
  const [items, setItems] = useState<StyleLoraAssetItem[]>([]);
  const [comfyReachable, setComfyReachable] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');

  const [showImport, setShowImport] = useState<boolean>(false);
  const [localPath, setLocalPath] = useState<string>('');
  const [displayName, setDisplayName] = useState<string>('');
  const [defaultStrength, setDefaultStrength] = useState<string>('0.65');
  const [triggerWord, setTriggerWord] = useState<string>('');
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const reportMessage = useCallback(
    (msg: string) => {
      setNotice(msg);
      if (onMessage) onMessage(msg);
    },
    [onMessage],
  );

  const loadRegistry = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/loras', {
        headers: { 'x-shine-local-action': '1' },
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(parseApiError(payload, '读取 LoRA 台账失败'));
        return;
      }
      setItems(Array.isArray(payload.items) ? payload.items : []);
      setComfyReachable(Boolean(payload.comfy_reachable));
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取 LoRA 台账失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRegistry();
  }, [loadRegistry]);

  const byId = useMemo(() => {
    const map = new Map<string, StyleLoraAssetItem>();
    for (const item of items) map.set(item.id, item);
    return map;
  }, [items]);

  const mountedIds = useMemo(() => new Set(value.map((entry) => entry.lora_id)), [value]);
  const controlsDisabled = disabled || !loraSupported;

  function mountItem(item: StyleLoraAssetItem) {
    if (controlsDisabled) return;
    if (mountedIds.has(item.id) || value.length >= MAX_STYLE_LORAS) return;
    const loraName = item.comfy_lora_name || item.filename;
    onChange([
      ...value,
      {
        lora_id: item.id,
        lora_name: loraName,
        sha256: item.sha256,
        strength_model: clampStrength(item.default_strength),
        trigger_word: item.trigger_word || '',
      },
    ]);
  }

  function updateEntry(index: number, patch: Partial<StyleLoraConfig>) {
    if (controlsDisabled) return;
    onChange(
      value.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)),
    );
  }

  function removeEntry(index: number) {
    if (disabled) return;
    onChange(value.filter((_, i) => i !== index));
  }

  function moveEntry(index: number, delta: -1 | 1) {
    if (controlsDisabled) return;
    const target = index + delta;
    if (target < 0 || target >= value.length) return;
    const next = [...value];
    const [picked] = next.splice(index, 1);
    next.splice(target, 0, picked);
    onChange(next);
  }

  async function handleImportLocalPath() {
    const trimmedPath = localPath.trim();
    if (!trimmedPath) {
      setError('请先填写本机 .safetensors 文件路径或选择文件上传。');
      return;
    }
    const triggerErr = validateTriggerClient(triggerWord);
    if (triggerErr) {
      setError(triggerErr);
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const res = await fetch('/api/v1/loras/import', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-shine-local-action': '1',
        },
        body: JSON.stringify({
          local_path: trimmedPath,
          display_name: displayName.trim() || undefined,
          default_strength: clampStrength(Number(defaultStrength)),
          trigger_word: triggerWord.trim(),
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(parseApiError(payload, '导入 LoRA 失败'));
        return;
      }
      setLocalPath('');
      setDisplayName('');
      setTriggerWord('');
      setDefaultStrength('0.65');
      setShowImport(false);
      reportMessage(payload.deduplicated ? '已命中同哈希 LoRA，直接复用现有资产。' : '风格 LoRA 已导入受管目录。');
      await loadRegistry();
    } catch (e) {
      setError(e instanceof Error ? e.message : '导入 LoRA 失败');
    } finally {
      setBusy(false);
    }
  }

  async function handleUploadFile(file: File) {
    const triggerErr = validateTriggerClient(triggerWord);
    if (triggerErr) {
      setError(triggerErr);
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const form = new FormData();
      form.append('file', file);
      if (displayName.trim()) form.append('display_name', displayName.trim());
      form.append('default_strength', String(clampStrength(Number(defaultStrength))));
      if (triggerWord.trim()) form.append('trigger_word', triggerWord.trim());
      const res = await fetch('/api/v1/loras/import', {
        method: 'POST',
        headers: { 'x-shine-local-action': '1' },
        body: form,
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(parseApiError(payload, '上传 LoRA 失败'));
        return;
      }
      setDisplayName('');
      setTriggerWord('');
      setDefaultStrength('0.65');
      setShowImport(false);
      reportMessage(payload.deduplicated ? '已命中同哈希 LoRA，直接复用现有资产。' : '风格 LoRA 已上传并纳入台账。');
      await loadRegistry();
    } catch (e) {
      setError(e instanceof Error ? e.message : '上传 LoRA 失败');
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleDelete(item: StyleLoraAssetItem) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const res = await fetch(`/api/v1/loras/${encodeURIComponent(item.id)}`, {
        method: 'DELETE',
        headers: { 'x-shine-local-action': '1' },
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(parseApiError(payload, '删除 LoRA 失败'));
        return;
      }
      if (mountedIds.has(item.id)) {
        onChange(value.filter((entry) => entry.lora_id !== item.id));
      }
      reportMessage(`已删除 LoRA「${item.display_name}」。`);
      await loadRegistry();
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除 LoRA 失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="style-lora-panel" data-testid="style-lora-panel">
      <div className="style-lora-head">
        <div className="style-lora-title">
          <Sparkles size={14} aria-hidden="true" />
          <strong>风格 LoRA 装配</strong>
          <span className="style-lora-count">
            {value.length} / {MAX_STYLE_LORAS}（可选）
          </span>
        </div>
        <div className="style-lora-actions">
          <button
            type="button"
            className="strategy-option"
            disabled={loading || busy}
            onClick={() => void loadRegistry()}
          >
            <RefreshCw size={12} className={loading ? 'spin' : undefined} aria-hidden="true" />
            检测可见性
          </button>
          <button
            type="button"
            className="strategy-option"
            disabled={disabled || busy}
            onClick={() => setShowImport((prev) => !prev)}
          >
            <Upload size={12} aria-hidden="true" />
            {showImport ? '收起导入' : '导入 LoRA'}
          </button>
          {value.length > 0 && (
            <button
              type="button"
              className="strategy-option"
              disabled={disabled}
              onClick={() => onChange([])}
            >
              清空已选
            </button>
          )}
        </div>
      </div>

      <p className="strategy-note">
        可选挂载 0–{MAX_STYLE_LORAS} 个风格 LoRA；未选择时不修改工作流且不引入额外检查。
        {comfyReachable ? ' ComfyUI 实时列表已连接。' : ' 当前按本地受管路径登记。'}
      </p>

      {!loraSupported && (
        <div className="style-lora-warn" role="status">
          当前所选量化变体暂不支持风格 LoRA（V1 仅 INT8 主线可挂载）。
          {value.length > 0 && ' 请切换到 INT8 变体，或点击「清空已选」。'}
        </div>
      )}

      {error && (
        <div className="style-lora-error" role="alert">
          {error}
        </div>
      )}
      {notice && !error && (
        <div className="style-lora-notice" role="status">
          {notice}
        </div>
      )}

      {value.length > 0 && (
        <div className="style-lora-mounted">
          {value.map((entry, idx) => {
            const live = byId.get(entry.lora_id);
            const statusKey = live?.status ?? 'COMFY_UNREACHABLE';
            const statusMeta = STATUS_LABELS[statusKey] ?? { label: statusKey, tone: 'neutral' };
            const triggerErr = validateTriggerClient(entry.trigger_word);
            return (
              <div key={`${entry.lora_id}-${idx}`} className="style-lora-slot">
                <div className="style-lora-slot-head">
                  <span className="style-lora-order">#{idx + 1}</span>
                  <strong>{live?.display_name ?? entry.lora_name}</strong>
                  <span className={`style-lora-badge tone-${statusMeta.tone}`}>
                    {statusMeta.label}
                  </span>
                  <code className="style-lora-file">{entry.lora_name}</code>
                  <div className="style-lora-slot-btns">
                    <button
                      type="button"
                      className="strategy-option"
                      aria-label={`上移 ${live?.display_name ?? entry.lora_name}`}
                      disabled={controlsDisabled || idx === 0}
                      onClick={() => moveEntry(idx, -1)}
                    >
                      <ArrowUp size={12} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="strategy-option"
                      aria-label={`下移 ${live?.display_name ?? entry.lora_name}`}
                      disabled={controlsDisabled || idx === value.length - 1}
                      onClick={() => moveEntry(idx, 1)}
                    >
                      <ArrowDown size={12} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="strategy-option"
                      aria-label={`移除 ${live?.display_name ?? entry.lora_name}`}
                      disabled={disabled}
                      onClick={() => removeEntry(idx)}
                    >
                      移除
                    </button>
                  </div>
                </div>
                <div className="style-lora-slot-controls">
                  <label className="style-lora-field">
                    <span>强度 ({entry.strength_model.toFixed(2)})</span>
                    <input
                      type="range"
                      min={0}
                      max={1.5}
                      step={0.05}
                      disabled={controlsDisabled}
                      value={entry.strength_model}
                      onChange={(e) =>
                        updateEntry(idx, { strength_model: clampStrength(Number(e.target.value)) })
                      }
                    />
                    <input
                      type="number"
                      min={0}
                      max={1.5}
                      step={0.05}
                      className="style-lora-num"
                      aria-label={`权重 ${live?.display_name ?? entry.lora_name}`}
                      disabled={controlsDisabled}
                      value={entry.strength_model}
                      onChange={(e) =>
                        updateEntry(idx, { strength_model: clampStrength(Number(e.target.value)) })
                      }
                    />
                  </label>
                  <label className="style-lora-field style-lora-trigger-field">
                    <span>触发词（可选 ASCII）</span>
                    <input
                      type="text"
                      maxLength={TRIGGER_MAX_LEN}
                      placeholder="例如 cyberpunk_neon"
                      disabled={controlsDisabled}
                      value={entry.trigger_word}
                      onChange={(e) => updateEntry(idx, { trigger_word: e.target.value })}
                    />
                  </label>
                </div>
                {triggerErr && (
                  <p className="style-lora-error">
                    {triggerErr}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {showImport && (
        <div className="style-lora-import">
          <div className="style-lora-import-row">
            <input
              type="text"
              placeholder="输入本机 .safetensors 绝对路径（复制并哈希去重入库）"
              disabled={busy}
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
            />
            <button
              type="button"
              className="strategy-option"
              disabled={busy || !localPath.trim()}
              onClick={() => void handleImportLocalPath()}
            >
              路径导入
            </button>
            <label className={`strategy-option style-lora-upload ${busy ? 'disabled' : ''}`}>
              <Upload size={12} aria-hidden="true" />
              上传 .safetensors
              <input
                ref={fileInputRef}
                type="file"
                accept=".safetensors"
                hidden
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void handleUploadFile(file);
                }}
              />
            </label>
          </div>
          <div className="style-lora-import-row">
            <input
              type="text"
              placeholder="显示名称（留空则用文件名）"
              disabled={busy}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
            <input
              type="number"
              min={0}
              max={1.5}
              step={0.05}
              placeholder="默认权重"
              disabled={busy}
              value={defaultStrength}
              onChange={(e) => setDefaultStrength(e.target.value)}
            />
            <input
              type="text"
              maxLength={TRIGGER_MAX_LEN}
              placeholder="默认触发词（可选 ASCII）"
              disabled={busy}
              value={triggerWord}
              onChange={(e) => setTriggerWord(e.target.value)}
            />
          </div>
        </div>
      )}

      <div className="style-lora-library">
        {items.length === 0 ? (
          <p className="strategy-note">
            {loading ? '正在扫描已登记风格 LoRA…' : '暂无已登记的风格 LoRA，可通过上方「导入 LoRA」添加。'}
          </p>
        ) : (
          items.map((item) => {
            const isMounted = mountedIds.has(item.id);
            const statusMeta = STATUS_LABELS[item.status] ?? { label: item.status, tone: 'neutral' };
            const unmountable =
              controlsDisabled ||
              isMounted ||
              value.length >= MAX_STYLE_LORAS ||
              item.status === 'MISSING_FILE' ||
              item.status === 'HASH_MISMATCH';
            return (
              <div key={item.id} className="style-lora-lib-row">
                <div className="style-lora-lib-info">
                  <strong>{item.display_name}</strong>
                  <span className="style-lora-origin">
                    {item.origin === 'discovered' ? '内置发现' : '本地导入'}
                  </span>
                  <span className={`style-lora-badge tone-${statusMeta.tone}`}>
                    {statusMeta.label}
                  </span>
                  <span className="style-lora-meta">
                    默认 {item.default_strength.toFixed(2)}
                    {item.trigger_word ? ` · 触发词 ${item.trigger_word}` : ''}
                    {` · ${item.sha256.slice(0, 8)}`}
                  </span>
                </div>
                <div className="style-lora-lib-actions">
                  <button
                    type="button"
                    className="strategy-option"
                    disabled={unmountable}
                    onClick={() => mountItem(item)}
                  >
                    <Plus size={12} aria-hidden="true" />
                    {isMounted ? '已挂载' : '挂载'}
                  </button>
                  {item.origin !== 'discovered' && (
                    <button
                      type="button"
                      className="strategy-option"
                      aria-label={`删除 ${item.display_name}`}
                      disabled={busy || disabled}
                      onClick={() => void handleDelete(item)}
                    >
                      <Trash2 size={12} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
