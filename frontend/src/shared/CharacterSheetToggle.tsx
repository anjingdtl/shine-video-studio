import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';

export type CharacterSheetProjection = {
  lora_id: string;
  lora_sha256: string;
  lora_status: string;
  lora_ready: boolean;
  comfy_reachable?: boolean;
  display_name?: string;
  setup_hint?: string;
};

const DEFAULT_SETUP_HINT = '请前往「设置 → 制作环境 → 角色五视图定妆照」勾选下载并完成纳管；角色定妆照不会静默降级为无 LoRA 生图。';

type Props = {
  checked: boolean;
  onChange: (next: boolean) => void;
  sceneChecked?: boolean;
  onSceneChange?: (next: boolean) => void;
  disabled?: boolean;
  onGoSetup?: () => void;
};

/**
 * 人物 16:9 五视图定妆照 & 场景 16:9 四视图母版一步勾选开关。
 *
 * 三原则：
 * - 一套事实：仅读取服务端 `/api/v1/loras?base_model=qwen_image_21` 返回的
 *   `character_sheet` / `scene_sheet` 统一实时投影（二者复用同一 Qwen 多视角镜头 LoRA），
 *   前端绝不自行拼第二套就绪判断。
 * - 未下载整备时默认关闭且禁用，给出中文引导与「设置 → 制作环境」直达入口；
 *   下载整备完成后立即可一步勾选。
 */
export function CharacterSheetToggle({
  checked,
  onChange,
  sceneChecked = false,
  onSceneChange,
  disabled = false,
  onGoSetup,
}: Props) {
  const [projection, setProjection] = useState<CharacterSheetProjection | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch('/api/v1/loras?base_model=qwen_image_21', {
      headers: { 'x-shine-local-action': '1' },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        if (!alive) return;
        const cs = payload && typeof payload === 'object' ? payload.character_sheet : null;
        if (cs && typeof cs === 'object' && typeof cs.lora_ready === 'boolean') {
          setProjection(cs as CharacterSheetProjection);
        } else {
          setProjection(null);
        }
      })
      .catch(() => {
        if (alive) setProjection(null);
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const ready = Boolean(projection?.lora_ready);

  useEffect(() => {
    if (loaded && !ready && checked) {
      onChange(false);
    }
  }, [loaded, ready, checked, onChange]);

  useEffect(() => {
    if (loaded && !ready && sceneChecked && onSceneChange) {
      onSceneChange(false);
    }
  }, [loaded, ready, sceneChecked, onSceneChange]);

  const hint = projection?.setup_hint || DEFAULT_SETUP_HINT;

  function goSetup() {
    if (onGoSetup) {
      onGoSetup();
      return;
    }
    window.location.hash = '#d/setup';
  }

  return (
    <div className="character-sheet-toggle" data-ready={ready ? 'true' : 'false'}>
      <label className="character-sheet-toggle-label">
        <input
          type="checkbox"
          checked={ready ? checked : false}
          disabled={disabled || !ready}
          onChange={(event) => onChange(event.target.checked)}
        />
        <Sparkles size={14} />
        <span><b>人物生成 16:9 五视图定妆照</b></span>
      </label>
      {onSceneChange && (
        <label className="character-sheet-toggle-label">
          <input
            type="checkbox"
            checked={ready ? sceneChecked : false}
            disabled={disabled || !ready}
            onChange={(event) => onSceneChange(event.target.checked)}
          />
          <Sparkles size={14} />
          <span><b>场景生成 16:9 四视图母版</b></span>
        </label>
      )}
      {ready ? (
        <span className="material-hint">
          {onSceneChange
            ? '已就绪：勾选后在单次显卡租约内串行产出多视角母版（人物 16:9 五视图 / 场景四视角 1344×768 原图 + 4 合 1 母版，出片时按镜头朝向自动路由单/双角原图）；未勾选时走单步直出。'
            : '已就绪：勾选后人物素材在单次显卡租约内串行产出 16:9 五视图定妆照；未勾选时走单步直出，场景与道具保持单步出图。'}
        </span>
      ) : (
        <span className="material-hint">
          {loaded ? hint : '正在读取多视角镜头 LoRA 权重状态…'}
          {loaded && (
            <>
              {' '}
              <button type="button" className="btn-text" onClick={goSetup}>
                前往「设置 → 制作环境」下载
              </button>
            </>
          )}
        </span>
      )}
    </div>
  );
}
