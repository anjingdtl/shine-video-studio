import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Cpu, RefreshCw } from 'lucide-react';
import { ApiError, getDeploymentRecommendation, type DeploymentOption, type DeploymentRecommendation } from '../api';

const EVIDENCE_LABELS: Record<string, string> = {
  measured_on_this_machine: '本机实测',
  measured_on_similar_hardware: '相近硬件实测',
  estimated: '估算（尚未实测）',
};

const BLOCKER_LABELS: Record<string, string> = {
  OS_UNSUPPORTED: '操作系统或 CPU 架构不在这套组合的支持范围内',
  VRAM_INSUFFICIENT: '显存不足',
  RAM_INSUFFICIENT: '内存不足',
  DISK_INSUFFICIENT: '目标盘可用空间不足',
  CATALOG_UNAVAILABLE: '这套组合的固定目录暂时读不到',
};

const WARNING_LABELS: Record<string, string> = {
  GPU_NOT_IDENTIFIED: '没有识别到 NVIDIA 显卡：这不代表机器没有显卡，但当前组合只按 NVIDIA 显卡核对显存。',
};

function gib(bytes?: number | null): string {
  if (bytes == null) return '—';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function mbAsGib(mb?: number | null): string {
  if (mb == null) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

function blockerText(blocker: DeploymentOption['blockers'][number]): string {
  const label = BLOCKER_LABELS[blocker.code] ?? blocker.code;
  if (blocker.required != null && blocker.actual != null) {
    const unit = blocker.code === 'DISK_INSUFFICIENT' ? ' GB' : ' MB';
    return `${label}（需要 ${blocker.required}${unit}，本机 ${blocker.actual}${unit}）`;
  }
  return label;
}

export type DeploymentChoice = { h3_video: boolean; image_prep: boolean };

/**
 * 新机部署组合推荐：检测本机与已有安装 → 推荐 H3 / 生图完整组合（附证据等级、
 * 下载与复用量、具名阻塞）→ 由你选择 → 交给下方既有的安装计划逐项确认执行。
 *
 * 推荐只读、只算、不承诺：它不下载、不加载权重；估算与实测分开标注，
 * 多张显卡的显存不相加。全部自行上传素材时可以不装生图。
 */
export function DeploymentRecommendationCard({ onChoice }: { onChoice?: (choice: DeploymentChoice) => void }) {
  const [data, setData] = useState<DeploymentRecommendation | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [problem, setProblem] = useState('');
  const [choice, setChoice] = useState<DeploymentChoice>({ h3_video: true, image_prep: true });

  async function detect() {
    setState('loading');
    setProblem('');
    try {
      const result = await getDeploymentRecommendation(true);
      setData(result);
      const image = result.options.find((row) => row.purpose === 'image_prep');
      // A combination that cannot run here is never pre-selected.
      setChoice({ h3_video: true, image_prep: image?.status === 'SUPPORTED' });
      setState('ready');
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : String(e));
      setState('error');
    }
  }

  // Report a changed choice only; an inline parent callback must not loop.
  const onChoiceRef = useRef(onChoice);
  onChoiceRef.current = onChoice;
  useEffect(() => { onChoiceRef.current?.(choice); }, [choice]);

  return <section className="card section-card deployment-recommendation" id="deployment-recommendation">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">推荐部署组合</div>
        <h2>按这台电脑的能力推荐完整组合</h2>
        <p>只读检测，不下载、不加载模型。估算与实测分开标注；多张显卡的显存不会相加。</p>
      </div>
      <button className="btn-secondary" disabled={state === 'loading'} onClick={() => { void detect(); }}>
        {state === 'loading' ? <RefreshCw size={13} className="spin" /> : <Cpu size={13} />}
        {state === 'ready' ? '重新检测' : '检测并推荐'}
      </button>
    </div>

    {state === 'error' && <p className="form-error">推荐暂时不可用：{problem}</p>}
    {state === 'idle' && <p className="empty-inline">点「检测并推荐」查看这台电脑适合的 H3 视频与生图组合。</p>}

    {data && state !== 'error' && <>
      <div className="recommend-host">
        <span>显卡：{data.host.gpu || '未识别'}</span>
        <span>显存：{mbAsGib(data.host.vram_mb)}</span>
        <span>内存：{mbAsGib(data.host.ram_mb)}</span>
        <span>可用空间：{data.host.free_gb == null ? '未识别' : `${data.host.free_gb.toFixed(1)} GB`}</span>
      </div>
      <ul className="recommend-options">
        {data.options.map((option) => {
          const supported = option.status === 'SUPPORTED';
          const key = option.purpose === 'image_prep' ? 'image_prep' : 'h3_video';
          const checked = choice[key as keyof DeploymentChoice];
          return <li key={option.purpose} className={supported ? 'ok' : 'blocked'}>
            <div className="recommend-option-head">
              <label>
                <input type="checkbox" checked={checked} disabled={!option.skippable}
                  onChange={(event) => setChoice((current) => ({ ...current, [key]: event.target.checked }))} />
                <b>{option.label}</b>
              </label>
              <span className={`chip ${supported ? 'ok' : 'warn'}`}>
                {supported ? <><Check size={11} /> 本机可满足</> : <><AlertTriangle size={11} /> 不满足</>}
              </span>
              {option.evidence && <span className="chip">{EVIDENCE_LABELS[option.evidence.level] ?? option.evidence.level}</span>}
            </div>
            <div className="recommend-option-body">
              {option.catalog_id && <span>组合：{option.catalog_id}（{option.catalog_version}）</span>}
              {option.requirements && <span>
                要求：显存 ≥ {mbAsGib(option.requirements.min_vram_mb)} · 内存 ≥ {mbAsGib(option.requirements.min_ram_mb)}
                {' '}· 磁盘 ≥ {option.requirements.min_disk_gb ?? '—'} GB
              </span>}
              {option.total_bytes != null && <span>
                总体积 {gib(option.total_bytes)} · 可复用已有 {gib(option.reuse_bytes)} · 需下载 {gib(option.download_bytes)}
              </span>}
              {option.evidence?.verified && option.evidence.peak_vram_mb != null && <span>
                实测峰值显存 {mbAsGib(option.evidence.peak_vram_mb)}
                {option.evidence.duration_s != null ? ` · 用时 ${Math.round(option.evidence.duration_s)} 秒` : ''}
              </span>}
              {option.blockers.map((blocker) => <em key={blocker.code} className="recommend-blocker">{blockerText(blocker)}</em>)}
              {(option.warnings ?? []).map((warning) => <em key={warning.code}>{WARNING_LABELS[warning.code] ?? warning.code}</em>)}
              {option.skippable && !checked && <em>你选择全部自行上传素材：可以不装生图，正式视频生产不受影响。</em>}
            </div>
          </li>;
        })}
      </ul>
      <p className="run-hint">
        选择只决定下面要执行哪些安装计划；每个计划仍会先展示来源、体积、目录与许可证，由你逐项确认后才下载。
        {data.notes.length ? ` ${data.notes.join(' ')}` : ''}
      </p>
    </>}
  </section>;
}
