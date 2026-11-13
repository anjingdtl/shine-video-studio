import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Cpu, RefreshCw } from 'lucide-react';
import { ApiError, api } from '../api';

/**
 * H3 方案清单：只回答「这台电脑能用哪一套」。
 *
 * 每套方案只有一行：名称 + 当前状态。以前那一长串证据缺口、第三方声明和
 * 运行要求不再铺在页面上——它们仍然在变体清单里，需要核对时到「检测明细」
 * 查看；日常只需要看懂这一行。候选方案按产品红线处理：可以看见，但不会
 * 进入自动下载。
 *
 * 接口：GET /api/v1/setup/variant-recommendations
 */
export type VariantEvidenceGap = { code: string; text: string; requirement?: string };
export type VariantThirdPartyClaim = { claim: string; source: string; verified_on?: string; is_project_measurement?: boolean; note?: string };
export type VariantRuntimeRequirement = { requirement: string; source: string; is_project_measurement?: boolean; note?: string };

export type VariantOption = {
  variant_id: string;
  title: string;
  summary?: string;
  quantization?: string;
  precision?: string;
  source?: string;
  tier?: string;
  acceptance_status: 'SUPPORTED' | 'EXPERIMENTAL' | 'CANDIDATE' | 'BLOCKED';
  status: 'SUPPORTED' | 'BLOCKED' | 'UNKNOWN';
  usable_in_production: boolean;
  requires_explicit_user_ack: boolean;
  auto_download_allowed?: boolean;
  total_bytes?: number;
  workflow_profile_ids?: string[];
  evidence_gaps?: VariantEvidenceGap[];
  third_party_claims?: VariantThirdPartyClaim[];
  runtime_requirements?: VariantRuntimeRequirement[];
  blockers?: { code: string; message: string }[];
  unknowns?: { code: string; message: string }[];
};

export type VariantRecommendation = {
  tier: string;
  host: { gpu?: string | null; vram_mb?: number | null; ram_mb?: number | null; free_gb?: number | null };
  recommended: VariantOption[];
  candidates: VariantOption[];
  blocked: VariantOption[];
  options: VariantOption[];
  notes: string[];
};

const STATUS_TEXT: Record<string, string> = {
  SUPPORTED: '可在生产使用',
  EXPERIMENTAL: '实验性',
  CANDIDATE: '候选 · 本机未准备',
  BLOCKED: '不可用',
};

const TIER_LABELS: Record<string, string> = {
  standard: '标准档（15 GiB 以上显存）',
  lowend_12gb: '12GB 级档位',
  below_lowend: '低于 12GB 级档位',
  unknown: '显卡档位未知',
};

function mbAsGib(mb?: number | null): string {
  if (mb == null) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

export function VariantRecommendationCard() {
  const [data, setData] = useState<VariantRecommendation | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [problem, setProblem] = useState('');

  const detect = useCallback(async () => {
    setState('loading');
    setProblem('');
    try {
      setData(await api<VariantRecommendation>('/setup/variant-recommendations'));
      setState('ready');
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : String(e));
      setState('error');
    }
  }, []);

  useEffect(() => { void detect(); }, [detect]);

  const rows = data ? [...data.recommended, ...data.candidates, ...data.blocked] : [];

  return <section className="card section-card variant-recommendation" id="variant-recommendation">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">H3 变体</div>
        <h2>这台电脑能用哪套 H3 变体</h2>
        <p>只读检测，不下载、不加载模型。</p>
      </div>
      <button className="btn-secondary" disabled={state === 'loading'} onClick={() => { void detect(); }}>
        {state === 'loading' ? <RefreshCw size={13} className="spin" /> : <Cpu size={13} />}
        重新检测
      </button>
    </div>

    {state === 'error' && <p className="form-error">变体检测暂时不可用：{problem}</p>}
    {state === 'idle' && <p className="empty-inline">正在读取本机硬件事实…</p>}

    {data && state !== 'error' && <>
      <div className="recommend-host">
        <span>显卡：{data.host.gpu || '未识别'}</span>
        <span>显存：{mbAsGib(data.host.vram_mb)}</span>
        <span>内存：{mbAsGib(data.host.ram_mb)}</span>
        <span>档位：{TIER_LABELS[data.tier] ?? data.tier}</span>
      </div>
      <ul className="variant-line-list">
        {rows.map((option) => {
          const production = option.usable_in_production;
          return <li key={option.variant_id} className={production ? 'ok' : 'blocked'}>
            <b>{option.title || option.variant_id}</b>
            <span className={`chip ${production ? 'ok' : 'warn'}`}>
              {production ? <Check size={11} /> : <AlertTriangle size={11} />}
              {STATUS_TEXT[option.acceptance_status] ?? option.acceptance_status}
            </span>
          </li>;
        })}
      </ul>
    </>}
  </section>;
}

export default VariantRecommendationCard;
