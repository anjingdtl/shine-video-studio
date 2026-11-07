import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Cpu, RefreshCw } from 'lucide-react';
import { ApiError, api } from '../api';

/**
 * H3 变体推荐：按这台电脑的实测硬件给出可用变体与候选变体。
 *
 * 推荐只读、只算、不承诺：候选变体（CANDIDATE / BLOCKED）在真机验收之前
 * 永远不会被写成“生产可用”，页面必须把这一点说清楚。
 *
 * 接口：GET /api/v1/setup/variant-recommendations
 *   tier: 'standard' | 'lowend_12gb' | 'below_lowend' | 'unknown'
 *   host: { gpu, vram_mb, vram_free_mb, ram_mb, commit_mb, free_gb }
 *   recommended / candidates / blocked / options: VariantOption[]
 *   notes: string[]
 *
 * VariantOption:
 *   variant_id, title, summary, quantization, precision, source, tier
 *   acceptance_status: 'SUPPORTED' | 'EXPERIMENTAL' | 'CANDIDATE' | 'BLOCKED'
 *   status: 'SUPPORTED' | 'BLOCKED' | 'UNKNOWN'
 *   usable_in_production: boolean
 *   requires_explicit_user_ack: boolean
 *   evidence_gaps: { code, text, requirement? }[]
 *   blockers / unknowns / warnings: { code, message }[]
 *   workflow_profile_ids: string[]
 *   total_bytes: number
 *   requirements: { min_vram_mb?, min_ram_mb?, min_commit_mb?, min_disk_gb? }
 */
export type VariantEvidenceGap = { code: string; text: string; requirement?: string };

export type VariantThirdPartyClaim = {
  claim: string;
  source: string;
  verified_on?: string;
  is_project_measurement?: boolean;
  note?: string;
};

export type VariantRuntimeRequirement = {
  requirement: string;
  source: string;
  is_project_measurement?: boolean;
  note?: string;
};

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
  evidence_gaps?: VariantEvidenceGap[];
  third_party_claims?: VariantThirdPartyClaim[];
  runtime_requirements?: VariantRuntimeRequirement[];
  blockers?: { code: string; message: string }[];
  unknowns?: { code: string; message: string }[];
  workflow_profile_ids?: string[];
  total_bytes?: number;
  requirements?: Record<string, unknown>;
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

const ACCEPTANCE_LABELS: Record<string, string> = {
  SUPPORTED: '已验收（可在生产使用）',
  EXPERIMENTAL: '实验性（已验收但未开放推荐）',
  CANDIDATE: '候选（未完成真机验收）',
  BLOCKED: '阻断（缺少可信来源或钉点）',
};

const STATUS_LABELS: Record<string, string> = {
  SUPPORTED: '本机满足门槛',
  BLOCKED: '本机不满足门槛',
  UNKNOWN: '无法判断（关键事实读不到或门槛未核实）',
};

const TIER_LABELS: Record<string, string> = {
  standard: '标准档（15 GiB 以上显存）',
  lowend_12gb: '12GB 级档位',
  below_lowend: '低于 12GB 级档位',
  unknown: '显卡档位未知',
};

const GAP_LABELS: Record<string, string> = {
  NO_REAL_ACCEPTANCE: '未完成真机最小镜头验收',
  SOURCE_UNVERIFIED: '来源未经核实',
  REQUIRED_INPUT_MISSING: '还缺这些材料',
  OFFICIAL_THRESHOLD_UNKNOWN: '官方未公布门槛',
  COMPATIBILITY_UNVERIFIED: '兼容性未逐项验证',
  WEIGHT_EXCEEDS_VRAM: '权重本体超过本机显存',
  PUBLISHER_TABLE_ADA_NOT_COVERED: '发布方分档表未覆盖 Ada / 12GB',
  NO_FULL_GENERATION_RUN: '没有做过完整生成运行',
  CHECKPOINT_SIZE_IS_NOT_PEAK_VRAM: '文件大小不等于峰值显存',
  THIRD_PARTY_DERIVATIVE_NOT_OFFICIAL: '第三方衍生件，非官方发布',
  QUANTIZATION_NOT_AWQ: '不是 AWQ',
  NODE_CONTRACT_NOT_VERIFIED_HERE: '节点契约未在本机验证',
};

function mbAsGib(mb?: number | null): string {
  if (mb == null) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

function gib(bytes?: number | null): string {
  if (bytes == null) return '—';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
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
        <p>只读检测，不下载、不加载模型。候选变体在真机验收之前只是候选，不会被当成生产可用。</p>
      </div>
      <button className="secondary-button" disabled={state === 'loading'} onClick={() => { void detect(); }}>
        {state === 'loading' ? <RefreshCw size={13} className="spin" /> : <Cpu size={13} />}
        {state === 'ready' ? '重新检测' : '重新检测'}
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
      <ul className="recommend-options">
        {rows.map((option) => {
          const production = option.usable_in_production;
          return <li key={option.variant_id} className={production ? 'ok' : 'blocked'}>
            <div className="recommend-option-head">
              <b>{option.title || option.variant_id}</b>
              <span className={`chip ${production ? 'ok' : 'warn'}`}>
                {production ? <><Check size={11} /> {ACCEPTANCE_LABELS[option.acceptance_status] ?? option.acceptance_status}</>
                  : <><AlertTriangle size={11} /> {ACCEPTANCE_LABELS[option.acceptance_status] ?? option.acceptance_status}</>}
              </span>
              <span className="chip">{STATUS_LABELS[option.status] ?? option.status}</span>
            </div>
            <div className="recommend-option-body">
              {option.summary && <span>{option.summary}</span>}
              {option.total_bytes != null && option.total_bytes > 0 && <span>权重总体积 {gib(option.total_bytes)}</span>}
              {(option.workflow_profile_ids ?? []).length > 0 && (
                <span>工作流：{(option.workflow_profile_ids ?? []).join('、')}</span>
              )}
              {!production && <em>
                未完成真机验收，不能当作生产可用；启用前需要你明确确认。
              </em>}
              {(option.evidence_gaps ?? []).map((gap, index) => <em key={`${gap.code}-${index}`}>
                {GAP_LABELS[gap.code] ?? gap.code}：{gap.text}
              </em>)}
              {(option.third_party_claims ?? []).map((claim, index) => <em key={`claim-${index}`}>
                第三方声明（非本项目实测）：{claim.claim}——来源 {claim.source}
                {claim.note ? `；${claim.note}` : ''}
              </em>)}
              {(option.runtime_requirements ?? []).map((item, index) => <em key={`runtime-${index}`}>
                运行要求（非本项目实测）：{item.requirement}——来源 {item.source}
                {item.note ? `；${item.note}` : ''}
              </em>)}
              {(option.blockers ?? []).map((blocker) => <em key={blocker.code}>{blocker.message}</em>)}
              {(option.unknowns ?? []).map((item) => <em key={item.code}>{item.message}</em>)}
            </div>
          </li>;
        })}
      </ul>
      <p className="run-hint">
        候选变体不会进入默认安装路径，也不会自动下载；官方没有公布显存门槛时，
        能不能跑通是待核，不是结论。
        {data.notes.length ? ` ${data.notes.join(' ')}` : ''}
      </p>
    </>}
  </section>;
}

export default VariantRecommendationCard;
