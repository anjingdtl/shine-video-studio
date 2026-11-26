import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ChevronRight, Download, Image as ImageIcon, Loader2, Mic,
  MonitorCheck, Sparkles, Video,
} from 'lucide-react';
import { api, json, type SetupVerdict } from '../api';
import type { VariantAvailability } from '../api';
import { GuideTip } from './GuideTip';
import { ResourceScanCard } from './ResourceScanCard';
import { CAPABILITY_ORDER, SetupEnvironmentStatus } from './SetupEnvironmentStatus';
import type { CapabilityKey, SetupSummary } from './SetupEnvironmentStatus';

/**
 * 「制作环境」的唯一入口：一次检测，一处确认，一条自动链。
 *
 * 检测把以前分散的四块（H3 变体、本机已有模型、出图能力、审片能力）合成
 * 一条结论；确认只有一次，后面的下载/复用、工作流登记、受管配置、服务核验
 * 与激活由同一条链自动完成。候选变体仍按产品红线处理：可看、可对比，
 * 但没有真机证据之前不进入自动下载。
 */

type EnvFinding = {
  code: string;
  repairability: 'AUTO_REPAIRABLE' | 'USER_ACTION_REQUIRED' | 'HARDWARE_INCOMPATIBLE'
    | 'DETECTION_UNKNOWN' | 'ALREADY_AVAILABLE';
  scopes?: string[]; scope?: string[];
  message?: string; package_id?: string; tools?: string[];
};

type ToolRepairPlan = {
  plan_id: string; status: string; download_bytes: number;
  packages: Array<{ package_id: string; label: string }>;
};

type EnvReport = {
  platform: Record<string, string>;
  tools: Record<string, { present: boolean; version: string; at_floor: boolean }>;
  gpu: Record<string, unknown>;
  ram: { status: string; ram_mb?: number; installed_mb?: number | null; note?: string };
  disk: { status: string; free_gb: number };
  port: { status: string; port: number; selected?: number; identity?: string };
  data_dir: { path: string; writable: boolean };
  deployment_dir?: { path: string; writable: boolean };
  blockers: Array<{ code: string; tool?: string }>;
  findings?: EnvFinding[];
  repair_plan?: ToolRepairPlan;
};

type Storage = { root: string; free_bytes: number; free_gb: number; requires_selection: boolean };

type ExistingScan = {
  root: string;
  snapshot: string;
  candidate_bytes: number;
  scanned_files: number;
  matches: Array<{ component_id: string; relative_path: string; size: number }>;
  missing: Array<{ component_id: string; relative_path: string; size: number }>;
  software?: { installed: boolean; version_status: string; runtime?: { python_executable?: string | null } };
};

type VariantOption = {
  variant_id: string; title: string; summary?: string; quantization?: string;
  acceptance_status: 'SUPPORTED' | 'EXPERIMENTAL' | 'CANDIDATE' | 'BLOCKED';
  status: 'SUPPORTED' | 'BLOCKED' | 'UNKNOWN';
  usable_in_production: boolean;
  auto_download_allowed: boolean;
  catalog_id?: string;
  total_bytes?: number;
  required_component_ids?: string[];
  install_component_ids?: string[];
  workflow_profile_ids?: string[];
  blockers?: Array<{ code: string; message: string }>;
  unknowns?: Array<{ code: string; message: string }>;
};

type VariantRecommendation = {
  tier: string;
  preferred_variant_id?: string | null;
  host: { gpu?: string | null; vram_mb?: number | null; ram_mb?: number | null; free_gb?: number | null };
  recommended: VariantOption[];
  candidates: VariantOption[];
  blocked: VariantOption[];
};

type Readiness = { layers: Array<{ layer: string; status: string; blockers?: unknown[] }> };

type RuntimeState = {
  status: string;
  candidate: { project_id: string; recipe_sha256: string } | null;
  services_verified?: boolean;
};

type ImageReadiness = {
  configured: boolean;
  layer: string;
  renderer: { state: string };
  blockers: Array<{ code: string }>;
  upstream: { required_bytes: number };
  families: Record<string, { can_generate: boolean; layer: string }>;
};

const IMAGE_BYTES = 17.3 * 1024 ** 3;
const REVIEW_BYTES = { vlm: 10.6 * 1024 ** 3, asr: 1.9 * 1024 ** 3 };
const RELAYVSR_BYTES = 5291054712;

const TIER_LABELS: Record<string, string> = {
  standard: '标准档', lowend_12gb: '12GB 级档位', below_lowend: '低于 12GB 级档位', unknown: '显卡档位未知',
};

const CAP_LABELS: Record<CapabilityKey, string> = {
  h3: 'H3 视频生成', image: '素材出图', vlm: '画面审片', asr: '语音核对', relayvsr: 'RelayVSR 2X 精修',
  image_loras: '生图风格 LoRA 包', character_sheet: '角色五视图定妆照',
};

/**
 * What one capability ended up as.
 *
 * A capability that could not be prepared says why and stops there: it never
 * takes the other - legal - capabilities down with it.  The vocabulary keeps
 * two different facts apart: "FILES_READY" states the pinned bytes are on
 * disk and hash-verified (the installer's honest terminal stage
 * FILES_VERIFIED_PENDING_RUNTIME), while "READY" claims the runtime contract
 * was verified too.  Neither is a claim of a real generation.
 */
type CapabilityOutcome = {
  key: CapabilityKey;
  label: string;
  status: 'READY' | 'FILES_READY' | 'REUSED' | 'QUEUED' | 'BLOCKED' | 'FAILED';
  note: string;
};

function gib(bytes?: number | null): string {
  if (bytes == null) return '—';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function mbAsGib(mb?: number | null): string {
  if (mb == null) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

const SNAPSHOT_KEY = 'shine-setup-center-snapshot-v1';

type DetectionSnapshot = {
  detectedAt: string;
  env: EnvReport; storage: Storage; runtime: RuntimeState; readiness: Readiness;
  scan: ExistingScan | null;
  variants: VariantRecommendation; image: ImageReadiness | null;
  adoptedRoot: string | null;
  variantId: string; caps: Record<CapabilityKey, boolean>;
  variantChosenByUser?: boolean;
};

const EMPTY_CAPS: Record<CapabilityKey, boolean> = {
  h3: false, image: false, vlm: false, asr: false, relayvsr: false, image_loras: false, character_sheet: false,
};

function readSnapshot(): DetectionSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(SNAPSHOT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DetectionSnapshot;
    if (!parsed?.variants) return null;
    // 「要准备哪些能力」始终默认全空，由用户按需自行勾选；快照只保留只读检测结果避免白屏，
    // 不替用户预勾已下载或未下载的模块。
    return {
      ...parsed,
      caps: { ...EMPTY_CAPS },
    };
  } catch { return null; }
}

function writeSnapshot(patch: Partial<DetectionSnapshot>) {
  try {
    const current = readSnapshot() ?? ({} as DetectionSnapshot);
    window.sessionStorage.setItem(SNAPSHOT_KEY, JSON.stringify({ ...current, ...patch }));
  } catch { /* 快照只是便利，不是门禁 */ }
}

/** 一句人话结论：只写这台电脑能用什么，不写验收术语。 */
function verdictLine(variants: VariantRecommendation | null): string {
  if (!variants) return '正在读取这台电脑的硬件…';
  const gpu = variants.host.gpu || '未识别显卡';
  const vram = mbAsGib(variants.host.vram_mb);
  const main = variants.recommended[0];
  if (main) return `${gpu} · ${vram} 显存，可以运行 ${main.title}。`;
  if (variants.candidates.length) {
    return `${gpu} · ${vram} 显存，只能用候选方案；本机还没有可确认的生产方案。`;
  }
  return `${gpu} · ${vram} 显存，本机没有匹配的 H3 方案。`;
}

type Props = {
  projectId: string;
  refreshEpoch?: number;
  onNeedProject?: () => void;
  /** 整备提交成功的通知：页面级进度条据此立即跟读同一份 summary。 */
  onPrepSubmitted?: () => void;
};

export function SetupCenter({ projectId, refreshEpoch = 0, onPrepSubmitted }: Props) {
  // 打开页面就自动检测：不再要求先点「检测本机」才看到环境。
  // 结果存快照只是为了不空屏；资格判定永远来自服务端。
  const [restored] = useState<DetectionSnapshot | null>(() => readSnapshot());
  const [phase, setPhase] = useState<'idle' | 'detecting' | 'ready' | 'error'>('detecting');
  const [problem, setProblem] = useState('');
  const [env, setEnv] = useState<EnvReport | null>(restored?.env ?? null);
  const [storage, setStorage] = useState<Storage | null>(restored?.storage ?? null);
  const [readiness, setReadiness] = useState<Readiness | null>(restored?.readiness ?? null);
  /** The one verdict the badge and both configuration pages read. */
  const [setupVerdict, setSetupVerdict] = useState<SetupVerdict | null>(null);
  const [scan, setScan] = useState<ExistingScan | null>(restored?.scan ?? null);
  const [variants, setVariants] = useState<VariantRecommendation | null>(restored?.variants ?? null);
  const [image, setImage] = useState<ImageReadiness | null>(restored?.image ?? null);
  /** 已采用的 ComfyUI 根目录：存在时不再创建受管实例，直接复用。 */
  const [adoptedRoot, setAdoptedRoot] = useState<string | null>(restored?.adoptedRoot ?? null);
  /**
   * 后端权威的「这套方案在本机处于哪一步」。界面不再用扫描文件数
   * 推断生产事实：扫描只看目录，核验/绑定/登记/节点核对各有自己的证据。
   */
  const [availabilityRows, setAvailabilityRows] = useState<VariantAvailability[] | null>(null);
  const [variantId, setVariantId] = useState(restored?.variantChosenByUser ? restored.variantId : '');
  const [caps, setCaps] = useState(EMPTY_CAPS);
  const [detectedAt, setDetectedAt] = useState(restored?.detectedAt ?? '');
  /** 用户确认「重复下载」的能力：已有权重也重新下载。 */
  const [force, setForce] = useState(EMPTY_CAPS);
  const [redownloadPrompt, setRedownloadPrompt] = useState<null | CapabilityKey>(null);
  const [running, setRunning] = useState<{ label: string; note: string } | null>(null);
  /** 提交整备后只刷新整备投影，不重跑整页检测。 */
  const [prepEpoch, setPrepEpoch] = useState(0);
  const [result, setResult] = useState('');
  /** 每项能力各自的结论：一项没准备好，不影响其它项。 */
  const [outcomes, setOutcomes] = useState<CapabilityOutcome[]>([]);
  /** 服务端唯一事实视图；界面的资格判定只读它。 */
  const [summary, setSummary] = useState<SetupSummary | null>(null);
  /** 二次确认就在卡片里完成，不弹系统对话框：看到体积与目录，再点一次开始。 */
  const [confirming, setConfirming] = useState(false);
  const mounted = useRef(true);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => { writeSnapshot({ variantId, caps }); }, [variantId, caps]);

  // 裁决在 summary 里算一次（与 /setup/verdict 同一函数）；这里只读，不再单独请求。
  useEffect(() => { if (summary?.verdict) setSetupVerdict(summary.verdict); }, [summary]);

  const loadImage = useCallback(async () => {
    try { setImage(await api<ImageReadiness>('/setup/image-readiness')); } catch { /* 保持上次结果 */ }
  }, []);

  const detect = useCallback(async () => {
    setPhase('detecting');
    setProblem('');
    setResult('');
    try {
      const [environment, disk, state, ready, existing, imageState, variantRows] = await Promise.all([
        api<EnvReport>('/setup/environment'),
        api<Storage>('/setup/storage'),
        api<RuntimeState>('/setup/runtime'),
        api<Readiness>('/setup/readiness'),
        api<{ installations: string[] }>('/setup/existing-installations'),
        api<ImageReadiness>('/setup/image-readiness').catch(() => null),
        api<VariantRecommendation>('/setup/variant-recommendations'),
      ]);
      // 作品 id 在首屏是异步到达的：没有作品时不去问「已采用的安装」。
      const availability = projectId
        ? await api<{ adopted_root?: string | null; variants?: VariantAvailability[] }>(
          `/setup/variant-availability?project_id=${encodeURIComponent(projectId)}`,
        ).catch(() => null)
        : null;
      if (!mounted.current) return;
      setEnv(environment); setStorage(disk); setReadiness(ready);
      setImage(imageState); setVariants(variantRows);
      setAdoptedRoot(availability?.adopted_root ?? null);
      setAvailabilityRows(availability?.variants ?? null);
      const preferred = variantRows.preferred_variant_id
        ?? [...variantRows.recommended, ...variantRows.candidates, ...variantRows.blocked]
          .find((row) => row.acceptance_status === 'SUPPORTED' && row.auto_download_allowed)?.variant_id;
      setVariantId((current) => current || preferred || '');
      const stamp = new Date().toISOString();
      setDetectedAt(stamp);
      writeSnapshot({
        detectedAt: stamp, env: environment, storage: disk, runtime: state, readiness: ready,
        variants: variantRows, image: imageState,
        adoptedRoot: availability?.adopted_root ?? null,
      });
      setPhase('ready');
      // 只读展示明细异步补：inspect 与方案可用性不得把进度条、回执和报错
      // 锁在「检测中」分支里。深搜的唯一入口是检索卡的「重新检索」。
      const root = existing.installations?.[0];
      if (root) {
        void api<ExistingScan>('/setup/existing-installations/inspect', json('POST', { path: root }))
          .then((row) => {
            if (!mounted.current) return;
            setScan(row);
            writeSnapshot({ scan: row });
          })
          .catch(() => {});
      }
    } catch (error) {
      if (!mounted.current) return;
      setProblem(error instanceof Error ? error.message : String(error));
      setPhase('error');
    }
  }, [projectId]);

  // 挂载即检测一次；作品 id 变化时再检测一次（生成方案是项目级事实）。
  // 「刷新全部状态」（refreshEpoch 变化）是用户动作，也重新检测。
  const detectKey = useRef<string | null>(null);
  useEffect(() => {
    if (detectKey.current === projectId) return;
    detectKey.current = projectId;
    void detect();
  }, [projectId, detect]);

  const epochRef = useRef(refreshEpoch);
  useEffect(() => {
    if (epochRef.current === refreshEpoch) return;
    epochRef.current = refreshEpoch;
    // 「刷新全部状态」是用户主动动作：才重新检索磁盘；平时切页只读持久化事实。
    void detect();
  }, [refreshEpoch, detect]);

  const allVariants = useMemo(() => {
    if (!variants) return [];
    const rows = [...variants.recommended, ...variants.candidates, ...variants.blocked];
    const seen = new Set<string>();
    return rows.filter((row) => (seen.has(row.variant_id) ? false : (seen.add(row.variant_id), true)));
  }, [variants]);

  const selectedVariant = allVariants.find((row) => row.variant_id === variantId) ?? null;
  const availabilityOf = (id: string | undefined) =>
    id ? availabilityRows?.find((row) => row.variant_id === id) ?? null : null;
  const comfyInstalled = !!scan?.software?.installed;
  // 缺件与下载量以后端同一份能力结论为准（summary.capabilities 就是后台整备
  // 任务读的 capability_report）。浏览器不再按自查的第一套 ComfyUI 另算一套
  // 缺件判定；扫描只作为「检测明细」的只读展示。
  const capFacts = (key: CapabilityKey) => summary?.capabilities?.[key]?.facts;
  const h3Complete = !!capFacts('h3')?.FILES_FOUND;
  const vlmReady = !!capFacts('vlm')?.FILES_FOUND;
  const asrReady = !!capFacts('asr')?.FILES_FOUND;

  // Only conditions that really block resource preparation count here.  A
  // repairable missing tool, a low-end GPU or an occupied port must not stop
  // the automatic preparation entry.
  const blockingEnv = (env?.findings ?? []).filter((finding) =>
    finding.repairability !== 'AUTO_REPAIRABLE'
    && finding.repairability !== 'ALREADY_AVAILABLE'
    && (finding.scopes ?? finding.scope ?? []).includes('RESOURCE_PREPARATION'));
  const envBlocked = env?.findings ? blockingEnv.length > 0 : (env?.blockers.length ?? 0) > 0;
  const pendingRepair = env?.repair_plan?.status === 'COMPLETE' && (env.repair_plan.packages.length ?? 0) > 0;
  const servicePort = summary?.installation.selected?.port ?? env?.port?.selected ?? env?.port?.port ?? null;
  const portAdjusted = !!env?.port?.identity && env.port.identity !== 'FREE'
    && servicePort !== summary?.installation.selected?.port;
  const imageBytes = image?.upstream?.required_bytes || IMAGE_BYTES;
  // 出图权重是否已有，读后端能力结论与受管运行时 readiness，不再自查第二套扫描。
  const imagePresent = !!capFacts('image')?.FILES_FOUND;
  // 勾选且（缺权重 或 用户确认重复下载）才计入下载体积。
  // H3 是一个和素材出图、审片一样独立的能力：勾不勾由人决定，综合就绪状态
  // 不再替人做决定。
  const h3Want = caps.h3 && (!h3Complete || force.h3);
  const imageWant = caps.image && (!imagePresent || force.image);
  const vlmWant = caps.vlm && (!vlmReady || force.vlm);
  const asrWant = caps.asr && (!asrReady || force.asr);
  const relayvsrPresent = !!summary?.capabilities?.relayvsr?.facts?.FILES_FOUND;
  const relayvsrWant = caps.relayvsr && (!relayvsrPresent || force.relayvsr);
  const imageLoraPack = summary?.capabilities?.image_loras;
  const imageLorasWant = caps.image_loras && !imageLoraPack?.facts?.RESOURCE_REGISTERED;
  const imageLoraBytes = imageLoraPack?.missing_bytes ?? null;
  const characterSheetPack = summary?.capabilities?.character_sheet;
  const characterSheetWant = caps.character_sheet && !characterSheetPack?.facts?.RESOURCE_REGISTERED;
  const characterSheetBytes = characterSheetPack?.missing_bytes ?? null;

  const h3Bytes = selectedVariant?.total_bytes ?? 0;
  // 下载量读后端同一结论里的「实际缺项」字节；只有用户明确重复下载时才按整档
  // 体积计。R5 B6/INV-14：summary 读不到时是「未知」，不是「缺整档」——
  // 不得把超时/未到达显示成「需要下载几十 GiB」。
  const capDownloadBytes = (key: CapabilityKey, fullBytes: number): number | null => {
    if (force[key]) return fullBytes;
    if (!summary) return null;
    return summary.capabilities?.[key]?.missing_bytes ?? fullBytes;
  };
  const downloadBytes = (h3Want ? capDownloadBytes('h3', h3Bytes) ?? 0 : 0)
    + (imageWant ? capDownloadBytes('image', imageBytes) ?? 0 : 0)
    + (vlmWant ? capDownloadBytes('vlm', REVIEW_BYTES.vlm) ?? 0 : 0)
    + (asrWant ? capDownloadBytes('asr', REVIEW_BYTES.asr) ?? 0 : 0)
    + (relayvsrWant ? capDownloadBytes('relayvsr', RELAYVSR_BYTES) ?? 0 : 0)
    + (imageLorasWant ? imageLoraBytes ?? 0 : 0)
    + (characterSheetWant ? characterSheetBytes ?? 0 : 0);

  /** 这次真正要准备的能力，按各自的分项体积列出（未知体积为 null，显示 —）。 */
  const capabilityRows = useMemo(() => {
    const rows: Array<{ key: CapabilityKey; label: string; bytes: number | null }> = [];
    if (h3Want) rows.push({ key: 'h3', label: selectedVariant && !selectedVariant.auto_download_allowed ? 'H3 候选模型' : 'H3 视频权重', bytes: capDownloadBytes('h3', h3Bytes) });
    if (imageWant) rows.push({ key: 'image', label: '素材出图权重', bytes: capDownloadBytes('image', imageBytes) });
    if (vlmWant) rows.push({ key: 'vlm', label: '画面审片权重', bytes: capDownloadBytes('vlm', REVIEW_BYTES.vlm) });
    if (asrWant) rows.push({ key: 'asr', label: '语音核对权重', bytes: capDownloadBytes('asr', REVIEW_BYTES.asr) });
    if (relayvsrWant) rows.push({ key: 'relayvsr', label: 'RelayVSR 2X 精修权重', bytes: capDownloadBytes('relayvsr', RELAYVSR_BYTES) });
    if (imageLorasWant) rows.push({ key: 'image_loras', label: CAP_LABELS.image_loras, bytes: imageLoraBytes });
    if (characterSheetWant) rows.push({ key: 'character_sheet', label: CAP_LABELS.character_sheet, bytes: characterSheetBytes });
    return rows;
  }, [h3Want, imageWant, vlmWant, asrWant, relayvsrWant, imageLorasWant, imageLoraBytes, characterSheetWant, characterSheetBytes, h3Bytes, imageBytes, selectedVariant, summary, force]);

  /**
   * The button says what will actually happen.
   *
   * "确认并下载安装（约 16.1GiB）" used to hide three different plans behind
   * one sentence, and the number on it included a video download the person
   * had never asked for.  The label now names the capabilities that are
   * really selected.
   */
  const selectedCaps = CAPABILITY_ORDER.filter((key) => caps[key]);
  const anyCapSelected = selectedCaps.length > 0;
  const onlyLoraPacks = selectedCaps.length > 0
    && selectedCaps.every((key) => key === 'image_loras' || key === 'character_sheet');

  const actionLabel = useMemo(() => {
    if (!anyCapSelected) {
      return pendingRepair ? '自动补装缺失基础工具' : '勾选上方能力后开始准备';
    }
    if (!capabilityRows.length) {
      if (caps.relayvsr || caps.image_loras || caps.character_sheet) return '确认整备所选能力';
      return pendingRepair ? '自动补装缺失基础工具' : '确认并完成配置（不下载）';
    }
    if (capabilityRows.length > 1) return '确认整备所选能力';
    const row = capabilityRows[0];
    if (row.key === 'image') return `确认下载素材出图权重（约 ${gib(row.bytes)}）`;
    if (row.key === 'vlm') return `确认下载画面审片权重（约 ${gib(row.bytes)}）`;
    if (row.key === 'asr') return `确认下载语音核对权重（约 ${gib(row.bytes)}）`;
    if (row.key === 'relayvsr') return `确认准备 RelayVSR 精修权重（约 ${gib(row.bytes)}）`;
    if (row.key === 'image_loras' || row.key === 'character_sheet') return '确认整备所选能力';
    if (selectedVariant && !selectedVariant.auto_download_allowed) return '确认准备 H3 候选模型';
    return `确认准备 H3 权重（约 ${gib(row.bytes)}）`;
  }, [anyCapSelected, capabilityRows, caps.relayvsr, caps.image_loras, caps.character_sheet, pendingRepair, selectedVariant]);

  // The H3 scheme is a condition of the H3 capability alone: preparing only
  // image or review weights must not wait for an H3 variant that was never
  // asked for (a machine with no installable H3 scheme stays able to prepare
  // the other - legal - capabilities).
  // R5 B6：summary 未到达时不做任何「缺件/下载」断言，也不开放提交——
  // 资格与缺件事实都来自服务端同一份投影，读不到就是未知；能力默认全空，
  // 至少由用户勾选一项（或存在可自动补装的基础工具）才开放提交。
  const canPrepare = (!envBlocked || onlyLoraPacks) && !running && !!storage && !storage.requires_selection
    && !!summary && (anyCapSelected || pendingRepair)
    && (!caps.h3 || !!selectedVariant) && (onlyLoraPacks || !summary?.installation?.choice_required);

  /**
   * 一次授权 → 后台持续推进。
   *
   * 这里只把整备意图提交给持久任务:检索复用、核验登记、下载缺项、实例、
   * 配置、绑定、核验与小样全部由同一个后台任务按步骤完成。浏览器不再
   * 串行驱动任何一步,所以关掉页面、切走、Worker 重启都不会中断;重复
   * 点击同一意图返回同一个任务。
   */
  async function prepare(requested?: CapabilityKey[]) {
    const wants = (key: CapabilityKey) => (requested ? requested.includes(key) : caps[key]);
    const capabilities = CAPABILITY_ORDER.filter(wants);
    if (capabilities.length === 0) {
      setProblem('请至少勾选一项要准备的能力。');
      return;
    }
    setConfirming(false);
    setRunning({ label: '交给后台整备…', note: '一次确认，后台连续完成' });
    setResult('');
    setProblem('');
    setOutcomes([]);
    try {
      const installation = summary?.installation?.selected;
      const task = await api<{ status: string; task_id: string; steps: string[]; message?: string }>(
        '/setup/prepare/execute', json('POST', {
          ...(projectId ? { project_id: projectId } : {}),
          capabilities,
          installation_path: installation?.root ?? undefined,
          port: installation?.port ?? env?.port?.selected ?? env?.port?.port ?? undefined,
          variant_id: capabilities.includes('h3') ? selectedVariant?.variant_id : undefined,
          verify_runtime: true,
          deep_scan: !capabilities.every((key) => key === 'image_loras' || key === 'character_sheet')
            && (capabilities.length !== 1 || capabilities[0] !== 'relayvsr' || !relayvsrPresent),
        }));
      setResult(capabilities.length === 1 && capabilities[0] === 'relayvsr'
        ? '已交给后台整备 RelayVSR。系统会复用已下载的权重、补齐缺少的核心组件并核对节点。'
        : `已交给后台准备（${task.steps.length} 步）。可以离开这个页面，回到这里会看到最新进度。`);
      // 只刷新整备投影（能力卡读同一份 summary）；不触发整页重检测，
      // 否则回执与进度会被「检测中」分支吞掉。
      setPrepEpoch((n) => n + 1);
      onPrepSubmitted?.();
      if (capabilities.includes('image')) await loadImage();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      if (mounted.current) setRunning(null);
    }
  }

  /** 已有权重时勾选能力：先二次确认「重复下载 / 仅复用」，不静默重下。 */
  function toggleCap(key: CapabilityKey, present: boolean, checked: boolean) {
    if (checked && present) { setRedownloadPrompt(key); return; }
    setCaps((current) => ({ ...current, [key]: checked }));
    if (!checked) setForce((current) => ({ ...current, [key]: false }));
  }

  const verdict = verdictLine(variants);

  return <section className="card section-card setup-center" id="setup">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">00 / 制作环境</div>
        <h2>制作环境</h2>
        <p>打开这一页就开始检查；确认一次，剩下的交给后台。</p>
      </div>
      {detectedAt && <span className="run-hint">上次检测 {new Date(detectedAt).toLocaleTimeString()}</span>}
    </div>

    <SetupEnvironmentStatus
      projectId={projectId}
      refreshEpoch={refreshEpoch + prepEpoch}
      variantId={selectedVariant?.variant_id ?? null}
      onSummary={setSummary}
      onGoSettings={() => window.setTimeout(() => document.getElementById('setup')?.scrollIntoView({ block: 'start' }), 40)}
      onPrepare={(capability) => prepare([capability])}
    />

    <ResourceScanCard />

    {/* 回执、提交中提示与报错不挂在检测分支下：检测再慢，用户点了什么、
        后台接没接住，都必须立刻可见。整备进度由能力卡的同一份 summary 投影。 */}
    {running && <p className="run-hint" role="status">{running.label}{running.note ? ` · ${running.note}` : ''}</p>}
    {result && <p className="ok-line" role="status">{result}</p>}
    {problem && phase !== 'error' && <p className="form-error" role="alert">{problem}</p>}

    {phase === 'error' && <p className="form-error" role="alert">检测没有完成：{problem}</p>}
    {phase === 'detecting' && <p className="run-hint">正在读取显卡、显存、磁盘和已有安装…</p>}

    {phase === 'ready' && <>
      <p className="setup-verdict"><b>{verdict}</b></p>


      <div className="setup-facts">
        <span>显卡 {variants?.host.gpu ?? '未识别'}</span>
        <span>显存 {mbAsGib(variants?.host.vram_mb)}</span>
        <span>内存 {mbAsGib(variants?.host.ram_mb)}
          {env?.ram?.note === 'HARDWARE_RESERVED_MEMORY' ? `（实装 ${mbAsGib(env.ram.installed_mb)}）` : ''}</span>
        <span>档位 {TIER_LABELS[variants?.tier ?? 'unknown'] ?? variants?.tier}</span>
        <span>服务端口 {servicePort ?? '未识别'}{portAdjusted ? `（${env?.port.port} 被占用，已避开）` : ''}</span>
        <span>安装盘 {storage ? `${storage.root}（可用 ${storage.free_gb} GB）` : '读取中'}</span>
      </div>

      {pendingRepair && <div className="setup-repair" role="group" aria-label="基础工具补装">
        <div>
          <b>缺少 {(env?.repair_plan?.packages ?? []).map((pkg) => pkg.label).join('、')}</b>
          <span>已固定下载源与哈希，约 {gib(env?.repair_plan?.download_bytes ?? 0)}；补装是「一键准备」的第一步，由后台任务用 Windows 自带 PowerShell 5.1 完成并自动复查。</span>
        </div>
        <button type="button" className="btn-secondary" disabled={!!running} onClick={() => void prepare()}>
          交给一键准备补装
        </button>
      </div>}


      <div className="setup-presets">
        <h3>选一套 H3 生成方案 <GuideTip tight>只有「可直接安装」的方案会真的下载；候选方案要先在本机跑通</GuideTip></h3>
        <div className="setup-preset-list">
          {allVariants.map((row) => {
            // R5 B6/INV-07：presence 只读后端 availability 同一份台账结论；
            // 读不到就是「读取中」，不再用浏览器自查扫描另算一套「本机已有」。
            const fact = availabilityOf(row.variant_id);
            const local = !!fact?.installed;
            const blocked = row.acceptance_status === 'BLOCKED';
            // 已有一套权重不锁死其他方案：候选也可选、可显式确认后下载；
            // 只有没有可核对权重来源的 BLOCKED 槽位才禁用。
            const selectable = !blocked;
            // 两份服务端事实的合成：availability 说字节在不在，recommendation
            // 说硬件够不够。硬件不足时「文件已有」不得读成「可以生产」。
            const reason = fact
              ? (row.status === 'BLOCKED' && fact.installed
                ? '本机已有这套权重，但当前硬件不具备这一方案的正式生产准入条件'
                : fact.reason)
              : '正在读取本机模型状态…';
            return <label key={row.variant_id} className={`setup-preset${variantId === row.variant_id ? ' active' : ''}${selectable ? '' : ' disabled'}`}>
              <input type="radio" name="h3-preset" checked={variantId === row.variant_id} disabled={!selectable || !!running}
                onChange={() => {
                  writeSnapshot({ variantId: row.variant_id, variantChosenByUser: true });
                  setVariantId(row.variant_id);
                }} />
              <span className="setup-preset-text">
                <b>{row.title}</b>
                <em>{reason}</em>
              </span>
              <span className={`chip ${local ? 'ok' : row.auto_download_allowed ? '' : 'warn'}`}>
                {fact?.verified && fact.bound ? '本机已核验 · 已绑定'
                  : fact?.verified ? '本机已核验'
                    : local ? '本机已有' : !fact ? '读取中' : row.auto_download_allowed ? '可安装' : blocked ? '候选' : '候选 · 可下载'}
              </span>
            </label>;
          })}
        </div>
      </div>

      <div className="setup-capabilities">
        <h3>要准备哪些能力 <GuideTip tight>勾选才会下载；不勾的以后可以随时回来补</GuideTip></h3>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.h3} disabled={!!running}
            onChange={(event) => toggleCap('h3', h3Complete, event.target.checked)} />
          <Video size={14} /><span><b>H3 视频生成</b>
            <em className={h3Complete ? 'cap-found-warn' : undefined}>
              {capFacts('h3')?.FILES_VERIFIED
                ? (capFacts('h3')?.INSTANCE_BOUND
                  ? '权重已逐字节核验，并已绑定到当前 ComfyUI 实例'
                  : '权重已逐字节核验，等待实例绑定')
                : h3Complete
                  ? '本机已找到权重'
                  : summary?.capabilities?.h3?.state === 'UNKNOWN'
                    ? '扫描未完成，待核验'
                    : summary ? `需要下载 ${gib(h3Bytes)}` : '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.image} disabled={!!running}
            onChange={(event) => toggleCap('image', imagePresent, event.target.checked)} />
          <ImageIcon size={14} /><span><b>素材出图（人物 / 场景 / 道具）</b>
            <em className={imagePresent ? 'cap-found-warn' : undefined}>
              {capFacts('image')?.FILES_VERIFIED
                ? (capFacts('image')?.INSTANCE_BOUND
                  ? '权重已逐字节核验，并已绑定到当前 ComfyUI 实例'
                  : '权重已逐字节核验，等待实例绑定')
                : imagePresent
                  ? '本机已找到权重'
                  : summary?.capabilities?.image?.state === 'UNKNOWN'
                    ? '扫描未完成，待核验'
                    : summary ? `需要下载 ${gib(imageBytes)}` : '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.vlm} disabled={!!running}
            onChange={(event) => toggleCap('vlm', vlmReady, event.target.checked)} />
          <MonitorCheck size={14} /><span><b>画面审片（只给建议，不卡出片）</b>
            <em className={vlmReady ? 'cap-found-warn' : undefined}>{summary?.capabilities.vlm?.summary ?? '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.asr} disabled={!!running}
            onChange={(event) => toggleCap('asr', asrReady, event.target.checked)} />
          <Mic size={14} /><span><b>语音核对（只给建议，不卡出片）</b>
            <em className={asrReady ? 'cap-found-warn' : undefined}>{summary?.capabilities.asr?.summary ?? '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.relayvsr} disabled={!!running}
            onChange={(event) => toggleCap('relayvsr', false, event.target.checked)} />
          <Sparkles size={14} /><span><b>RelayVSR 2X 精修（后期放大，可选）</b>
            <em className={relayvsrPresent ? 'cap-found-warn' : undefined}>
              {capFacts('relayvsr')?.FILES_VERIFIED
                ? (capFacts('relayvsr')?.INSTANCE_BOUND
                  ? '权重已逐字节核验，并已落位到当前 ComfyUI 实例'
                  : '权重已逐字节核验，等待落位')
                : relayvsrPresent
                  ? '本机已找到权重'
                  : summary?.capabilities?.relayvsr?.state === 'UNKNOWN'
                    ? '扫描未完成，待核验'
                    : summary ? `需要下载 ${gib(RELAYVSR_BYTES)}` : '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.image_loras} disabled={!!running}
            onChange={(event) => toggleCap('image_loras', false, event.target.checked)} />
          <Sparkles size={14} /><span><b>生图风格 LoRA 包（写实 / 奇幻，约 436 MiB）</b>
            <em>{imageLoraPack?.summary ?? '正在读取本机文件状态'}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.character_sheet} disabled={!!running}
            onChange={(event) => toggleCap('character_sheet', false, event.target.checked)} />
          <Sparkles size={14} /><span><b>角色五视图定妆照（约 304 MiB）</b>
            <em>{characterSheetPack?.summary ?? '正在读取本机文件状态'}</em></span>
        </label>
      </div>

      {redownloadPrompt && <div className="setup-confirm" role="group" aria-label="重复下载确认">
        <div>
          <b>「{CAP_LABELS[redownloadPrompt]}」本机已找到权重。</b>
          <span>默认直接复用已有权重，不重复下载；仍要重复下载会再下载约 {gib(redownloadPrompt === 'h3' ? h3Bytes : redownloadPrompt === 'image' ? imageBytes : redownloadPrompt === 'vlm' ? REVIEW_BYTES.vlm : REVIEW_BYTES.asr)} 并写入隔离缓存，不会删除已核验文件。</span>
        </div>
        <button type="button" className="btn-primary" onClick={() => {
          setCaps((current) => ({ ...current, [redownloadPrompt]: true }));
          setForce((current) => ({ ...current, [redownloadPrompt]: true }));
          setRedownloadPrompt(null);
        }}>重复下载</button>
        <button type="button" className="btn-secondary" onClick={() => {
          setCaps((current) => ({ ...current, [redownloadPrompt]: true }));
          setForce((current) => ({ ...current, [redownloadPrompt]: false }));
          setRedownloadPrompt(null);
        }}>仅复用，不下载</button>
        <button type="button" className="btn-text" onClick={() => setRedownloadPrompt(null)}>取消</button>
      </div>}

      <div className="setup-actions">
        <button type="button" className="btn-primary" disabled={!canPrepare || confirming}
          onClick={() => setConfirming(true)}>
          {running ? <Loader2 size={14} className="spin" /> : <Download size={14} />}
          {running ? running.label : actionLabel}
        </button>
        <GuideTip>
          {!projectId ? '可以先整备机器，作品稍后再建'
            : capabilityRows.length ? '点一次确认即可，下载后可离开页面'
              : pendingRepair ? '先补装缺失的基础工具，它不依赖显卡门槛'
                : '本机权重已齐，点一次自动登记方案并激活'}
        </GuideTip>
        {envBlocked && <span className="setup-warn"><AlertTriangle size={12} /> 先处理硬件或工具阻断项</span>}
        {storage?.requires_selection && <span className="setup-warn"><AlertTriangle size={12} /> 先在本页「下载与安装目录」选择非系统盘</span>}
      </div>

      {capabilityRows.length > 0 && <ul className="setup-breakdown" aria-label="分项下载量">
        {capabilityRows.map((row) => <li key={row.key}>
          <b>{row.label}</b><span>{gib(row.bytes)}</span>
        </li>)}
        <li className="setup-breakdown-total"><b>合计</b><span>{gib(downloadBytes)}</span></li>
      </ul>}

      {confirming && !running && <div className="setup-confirm" role="group" aria-label="确认准备制作环境">
        <div>
          <b>{downloadBytes > 0 ? `将下载约 ${gib(downloadBytes)}，并自动完成配置` : '本机权重已齐，将自动完成配置'}</b>
          {capabilityRows.length > 0 && <span>
            {capabilityRows.map((row) => `${row.label} ${gib(row.bytes)}`).join(' · ')}
          </span>}
          <span>目录 {storage?.root} · 不会改动你原有的 ComfyUI，也不会删除已核验的模型</span>
          <span>{onlyLoraPacks
            ? '复用或下载所缺权重，核验后加入素材生图与角色定妆照的 LoRA 列表。'
            : caps.relayvsr
            ? '复用已核验的权重，按需补齐 RelayVSR 核心组件并核对运行合同。'
            : '核验已有资源或补齐缺件 → 生成受管配置 → 核对 Ref2VA 运行合同 → 激活'}</span>
          {selectedVariant && !selectedVariant.auto_download_allowed && selectedVariant.acceptance_status !== 'BLOCKED' && (
            <span className="setup-warn"><AlertTriangle size={12} /> 候选方案：下载权重不等于验收，用于生产前必须先在本机真机跑通。点「开始准备」即确认下载候选权重。</span>
          )}
        </div>
        <button type="button" className="btn-primary" onClick={() => void prepare()}>开始准备</button>
        <button type="button" className="btn-text" onClick={() => setConfirming(false)}>取消</button>
      </div>}

      {outcomes.length > 0 && <ul className="setup-outcomes" aria-label="各项能力的准备结论">
        {outcomes.map((row) => <li key={row.key} className={`setup-outcome-${row.status.toLowerCase()}`}>
          <b>{row.label}</b>
          <span>{row.status === 'READY'
            ? '资源已就绪'
            : row.status === 'FILES_READY'
              ? '文件已核验 · 运行待验证'
              : row.status === 'REUSED' ? '已复用本机已有资源'
              : row.status === 'QUEUED' ? '已排队，后台继续'
                : '未完成'}</span>
          <em>{row.note}</em>
        </li>)}
      </ul>}

      <details className="setup-details">
        <summary><ChevronRight size={12} /> 检测明细与专业选项</summary>
        <div className="setup-detail-body">
          <p>已有安装：{scan ? scan.root : '常见目录未发现 ComfyUI（可以只安装受管环境）'}
            {scan?.software ? ` · ${comfyInstalled ? '主程序已安装' : '主程序未识别'}` : ''}</p>
          {adoptedRoot && <p>已采用的安装（只读复用）：{adoptedRoot}</p>}
          <p>扫描 {scan?.scanned_files ?? 0} 个文件 · 找到候选 {scan?.matches.length ?? 0} 项 ·
            还缺固定版本文件 {scan?.missing.length ?? 0} 项</p>
          <p>服务状态：{setupVerdict
            ? `${setupVerdict.label}${setupVerdict.services_verified ? '（服务已核验）' : ''}`
            : '读取中…'}
            {' · '}就绪层 {readiness?.layers.filter((layer) => layer.status === 'READY').length ?? 0}/{readiness?.layers.length ?? 0}
          </p>
          {setupVerdict && !['READY', 'RUNNING'].includes(setupVerdict.mode) && <p role="status" className="run-hint">
            {setupVerdict.detail}{setupVerdict.code ? `（${setupVerdict.code}）` : ''}
            {setupVerdict.validation_blockers.length > 0
              ? `；待处理：${setupVerdict.validation_blockers.join('、')}` : ''}
          </p>}
          <div className="setup-scroll-list">
            {(scan?.matches ?? []).map((row) => <div key={`${row.component_id}:${row.relative_path}`}>
              <b>{row.component_id}</b><span>{row.relative_path}</span><em>{gib(row.size)}</em>
            </div>)}
            {(scan?.matches ?? []).length === 0 && <p className="empty-inline">没有可复用的候选文件。</p>}
          </div>
          <p className="run-hint">审片与语音核对只给建议，缺失不阻断出片；下载与核验按固定清单逐字节进行。</p>
        </div>
      </details>
    </>}
  </section>;
}

export default SetupCenter;
