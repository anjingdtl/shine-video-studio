import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, Check, ChevronRight, Cpu, Download, Image as ImageIcon, Loader2, Mic,
  MonitorCheck, RefreshCw, Video,
} from 'lucide-react';
import { api, json, timeoutSignal } from '../api';
import type { VariantAvailability } from '../api';
import { GuideTip } from './GuideTip';
import {
  approveAndExecuteSetupPlan, isQueuedInstallation, waitForInstallationAttempt,
} from '../setupExecution';
import type { InstallationAttemptView } from '../setupExecution';

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
  host: { gpu?: string | null; vram_mb?: number | null; ram_mb?: number | null; free_gb?: number | null };
  recommended: VariantOption[];
  candidates: VariantOption[];
  blocked: VariantOption[];
};

type Readiness = { layers: Array<{ layer: string; status: string; blockers?: unknown[] }> };

type CatalogSummary = { components: Array<{ component_id: string; files: number }> };

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

const IMAGE_CATALOG = 'qwen-image-2.1-windows-x64';
const IMAGE_COMPONENTS = ['qwen-image-2.1-unet', 'qwen-image-2.1-text-encoder', 'qwen-image-2.1-vae'];
const IMAGE_BYTES = 17.3 * 1024 ** 3;
const REVIEW_COMPONENTS = { vlm: 'qwen3-vl-8b-fp8', asr: 'qwen3-asr-0.6b' };
const REVIEW_BYTES = { vlm: 10.6 * 1024 ** 3, asr: 1.9 * 1024 ** 3 };
const STRUCTURE_COMPONENTS = [
  'comfyui', 'comfy-runtime-wheels', 'ComfyUI-KJNodes', 'comfyui-videohelpersuite',
  'ComfyUI-MiniMax-H3-PDD-Acc', 'ComfyUI-SolAttn_triton', 'Comfyui_Minimax_h3_latent_Upscaler',
];

const TIER_LABELS: Record<string, string> = {
  standard: '标准档', lowend_12gb: '12GB 级档位', below_lowend: '低于 12GB 级档位', unknown: '显卡档位未知',
};

const CAP_LABELS: Record<'image' | 'vlm' | 'asr', string> = {
  image: '素材出图', vlm: '画面审片', asr: '语音核对',
};

function gib(bytes?: number | null): string {
  if (bytes == null) return '—';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function mbAsGib(mb?: number | null): string {
  if (mb == null) return '未识别';
  return `${(mb / 1024).toFixed(1)} GiB`;
}

function variantPresence(variant: VariantOption, scan: ExistingScan | null): { found: number; required: number } {
  const required = weightIdsOf(variant);
  if (!scan || required.length === 0) return { found: 0, required: required.length };
  const found = required.filter((id) => scan.matches.some((row) => row.component_id === id)).length;
  return { found, required: required.length };
}

/** 候选变体的 required_component_ids 可能为空（图推导权重），下载与 presence 用 install 清单。 */
function weightIdsOf(variant: VariantOption): string[] {
  return variant.required_component_ids?.length
    ? variant.required_component_ids
    : (variant.install_component_ids ?? []);
}

const SNAPSHOT_KEY = 'shine-setup-center-snapshot-v1';

type DetectionSnapshot = {
  detectedAt: string;
  env: EnvReport; storage: Storage; runtime: RuntimeState; readiness: Readiness;
  scan: ExistingScan | null; imageScan: ExistingScan | null;
  variants: VariantRecommendation; image: ImageReadiness | null;
  catalog: CatalogSummary | null; adoptedRoot: string | null;
  variantId: string; caps: { image: boolean; vlm: boolean; asr: boolean };
};

function readSnapshot(): DetectionSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(SNAPSHOT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DetectionSnapshot;
    return parsed && parsed.variants ? parsed : null;
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
  onRefresh: () => void | Promise<void>;
  onNeedProject?: () => void;
};

export function SetupCenter({ projectId, refreshEpoch = 0, onRefresh, onNeedProject }: Props) {
  // 检测只由用户点击触发；结果存快照，切页/刷新回来直接读快照，不重复检测。
  const [restored] = useState<DetectionSnapshot | null>(() => readSnapshot());
  const [phase, setPhase] = useState<'idle' | 'detecting' | 'ready' | 'error'>(restored ? 'ready' : 'idle');
  const [problem, setProblem] = useState('');
  const [env, setEnv] = useState<EnvReport | null>(restored?.env ?? null);
  const [storage, setStorage] = useState<Storage | null>(restored?.storage ?? null);
  const [runtime, setRuntime] = useState<RuntimeState | null>(restored?.runtime ?? null);
  const [readiness, setReadiness] = useState<Readiness | null>(restored?.readiness ?? null);
  const [scan, setScan] = useState<ExistingScan | null>(restored?.scan ?? null);
  const [imageScan, setImageScan] = useState<ExistingScan | null>(restored?.imageScan ?? null);
  const [variants, setVariants] = useState<VariantRecommendation | null>(restored?.variants ?? null);
  const [image, setImage] = useState<ImageReadiness | null>(restored?.image ?? null);
  const [catalog, setCatalog] = useState<CatalogSummary | null>(restored?.catalog ?? null);
  /** 已采用的 ComfyUI 根目录：存在时不再创建受管实例，直接复用。 */
  const [adoptedRoot, setAdoptedRoot] = useState<string | null>(restored?.adoptedRoot ?? null);
  /**
   * 后端权威的「这套方案在本机处于哪一步」。界面不再用扫描文件数
   * 推断生产事实：扫描只看目录，核验/绑定/登记/节点核对各有自己的证据。
   */
  const [availabilityRows, setAvailabilityRows] = useState<VariantAvailability[] | null>(null);
  const [variantId, setVariantId] = useState(restored?.variantId ?? '');
  const [caps, setCaps] = useState(restored?.caps ?? { image: true, vlm: false, asr: false });
  const [detectedAt, setDetectedAt] = useState(restored?.detectedAt ?? '');
  /** 用户确认「重复下载」的能力：已有权重也重新下载。 */
  const [force, setForce] = useState({ image: false, vlm: false, asr: false });
  const [redownloadPrompt, setRedownloadPrompt] = useState<null | 'image' | 'vlm' | 'asr'>(null);
  const [attempt, setAttempt] = useState<InstallationAttemptView | null>(null);
  const [running, setRunning] = useState<{ label: string; note: string } | null>(null);
  const [result, setResult] = useState('');
  const [imageTesting, setImageTesting] = useState('');
  /** 二次确认就在卡片里完成，不弹系统对话框：看到体积与目录，再点一次开始。 */
  const [confirming, setConfirming] = useState(false);
  const mounted = useRef(true);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => { writeSnapshot({ variantId, caps }); }, [variantId, caps]);

  const loadImage = useCallback(async () => {
    try { setImage(await api<ImageReadiness>('/setup/image-readiness')); } catch { /* 保持上次结果 */ }
  }, []);

  const detect = useCallback(async () => {
    setPhase('detecting');
    setProblem('');
    setResult('');
    try {
      const [environment, disk, state, ready, existing, imageState, variantRows, catalogRows] = await Promise.all([
        api<EnvReport>('/setup/environment'),
        api<Storage>('/setup/storage'),
        api<RuntimeState>('/setup/runtime'),
        api<Readiness>('/setup/readiness'),
        api<{ installations: string[] }>('/setup/existing-installations'),
        api<ImageReadiness>('/setup/image-readiness').catch(() => null),
        api<VariantRecommendation>('/setup/variant-recommendations'),
        api<CatalogSummary>('/setup/catalog').catch(() => null),
      ]);
      // 作品 id 在首屏是异步到达的：没有作品时不去问「已采用的安装」，
      // 等 id 到了 effect 会重新检测一次。
      const availability = projectId
        ? await api<{ adopted_root?: string | null; variants?: VariantAvailability[] }>(
          `/setup/variant-availability?project_id=${encodeURIComponent(projectId)}`,
        ).catch(() => null)
        : null;
      if (!mounted.current) return;
      setEnv(environment); setStorage(disk); setRuntime(state); setReadiness(ready);
      setImage(imageState); setVariants(variantRows); setCatalog(catalogRows);
      setAdoptedRoot(availability?.adopted_root ?? null);
      setAvailabilityRows(availability?.variants ?? null);
      const root = existing.installations?.[0];
      let main: ExistingScan | null = null;
      let imageInstall: ExistingScan | null = null;
      if (root) {
        const post = (body: unknown) => api<ExistingScan>('/setup/existing-installations/inspect',
          json('POST', body));
        main = await post({ path: root });
        imageInstall = await post({ path: root, catalog: IMAGE_CATALOG }).catch(() => null);
      }
      if (!mounted.current) return;
      setScan(main); setImageScan(imageInstall);
      const preferred = variantRows.recommended.find((row) => row.auto_download_allowed)
        ?? variantRows.recommended[0] ?? variantRows.candidates[0];
      setVariantId((current) => current || preferred?.variant_id || '');
      const stamp = new Date().toISOString();
      setDetectedAt(stamp);
      writeSnapshot({
        detectedAt: stamp, env: environment, storage: disk, runtime: state, readiness: ready,
        scan: main, imageScan: imageInstall, variants: variantRows, image: imageState,
        catalog: catalogRows, adoptedRoot: availability?.adopted_root ?? null,
      });
      setPhase('ready');
    } catch (error) {
      if (!mounted.current) return;
      setProblem(error instanceof Error ? error.message : String(error));
      setPhase('error');
    }
  }, [projectId]);

  // 挂载不自动检测：有快照读快照，没有就等用户点「检测本机」。
  // 「刷新全部状态」（refreshEpoch 变化）是用户动作，才重新检测。
  const epochRef = useRef(refreshEpoch);
  useEffect(() => {
    if (epochRef.current === refreshEpoch) return;
    epochRef.current = refreshEpoch;
    void detect();
  }, [refreshEpoch, detect]);

  // 作品 id 异步到达或切换时，只补一个轻量的「已采用安装 / 方案事实」读取，
  // 不重跑检测。
  useEffect(() => {
    if (!projectId || phase !== 'ready') return;
    let alive = true;
    api<{ adopted_root?: string | null; variants?: VariantAvailability[] }>(
      `/setup/variant-availability?project_id=${encodeURIComponent(projectId)}`,
    ).then((row) => {
      if (!alive) return;
      setAdoptedRoot(row.adopted_root ?? null);
      setAvailabilityRows(row.variants ?? null);
    }).catch(() => {});
    return () => { alive = false; };
  }, [projectId, phase]);

  const allVariants = useMemo(() => {
    if (!variants) return [];
    const rows = [...variants.recommended, ...variants.candidates, ...variants.blocked];
    const seen = new Set<string>();
    return rows.filter((row) => (seen.has(row.variant_id) ? false : (seen.add(row.variant_id), true)));
  }, [variants]);

  const selectedVariant = allVariants.find((row) => row.variant_id === variantId) ?? null;
  const availabilityOf = (id: string | undefined) =>
    id ? availabilityRows?.find((row) => row.variant_id === id) ?? null : null;
  const selectedAvailability = availabilityOf(selectedVariant?.variant_id);
  const selectedPresence = selectedVariant ? variantPresence(selectedVariant, scan) : { found: 0, required: 0 };
  const comfyInstalled = !!scan?.software?.installed;
  // 生产事实以后端台账为准：扫描只说明「目录里有这些文件」。
  // 只有后端也读不到时才退回扫描计数，且扫描结果永远不等于「已核验」。
  const h3Complete = selectedAvailability
    ? selectedAvailability.installed
    : selectedPresence.required > 0 && selectedPresence.found === selectedPresence.required;
  const h3Verified = selectedAvailability ? selectedAvailability.verified : false;

  const componentFiles = useMemo(() => Object.fromEntries(
    (catalog?.components ?? []).map((row) => [row.component_id, row.files]),
  ), [catalog]);
  const vlmPresence = scan
    ? { found: scan.matches.filter((row) => row.component_id === REVIEW_COMPONENTS.vlm).length }
    : { found: 0 };
  const asrPresence = scan
    ? { found: scan.matches.filter((row) => row.component_id === REVIEW_COMPONENTS.asr).length }
    : { found: 0 };
  const vlmTotal = componentFiles[REVIEW_COMPONENTS.vlm] || 13;
  const asrTotal = componentFiles[REVIEW_COMPONENTS.asr] || 10;
  const vlmReady = vlmPresence.found >= vlmTotal;
  const asrReady = asrPresence.found >= asrTotal;

  // Only conditions that really block resource preparation count here.  A
  // repairable missing tool, a low-end GPU or an occupied port must not stop
  // the automatic preparation entry.
  const blockingEnv = (env?.findings ?? []).filter((finding) =>
    finding.repairability !== 'AUTO_REPAIRABLE'
    && finding.repairability !== 'ALREADY_AVAILABLE'
    && (finding.scopes ?? finding.scope ?? []).includes('RESOURCE_PREPARATION'));
  const envBlocked = env?.findings ? blockingEnv.length > 0 : (env?.blockers.length ?? 0) > 0;
  const pendingRepair = env?.repair_plan?.status === 'COMPLETE' && (env.repair_plan.packages.length ?? 0) > 0;
  const servicePort = env?.port?.selected ?? env?.port?.port ?? null;
  const portAdjusted = !!env?.port?.identity && env.port.identity !== 'FREE';
  const imageBytes = image?.upstream?.required_bytes || IMAGE_BYTES;
  // 出图权重可能已经由受管运行时装好（文件已核验），不能因为「已有 ComfyUI 里没找到」就再下载一遍。
  const imagePresent = (!!image?.configured && image.layer !== 'NOT_CONFIGURED')
    || (!!imageScan && IMAGE_COMPONENTS.every((id) => imageScan.matches.some((row) => row.component_id === id)));
  // 勾选且（缺权重 或 用户确认重复下载）才计入下载体积。
  const imageWant = caps.image && (!imagePresent || force.image);
  const vlmWant = caps.vlm && (!vlmReady || force.vlm);
  const asrWant = caps.asr && (!asrReady || force.asr);

  const downloadBytes = (!h3Complete ? (selectedVariant?.total_bytes ?? 0) : 0)
    + (imageWant ? imageBytes : 0)
    + (vlmWant ? REVIEW_BYTES.vlm : 0)
    + (asrWant ? REVIEW_BYTES.asr : 0);

  const canPrepare = !!projectId && !envBlocked && !running && !!storage && !storage.requires_selection
    && !!selectedVariant;

  async function prepare() {
    if (!projectId) { onNeedProject?.(); return; }
    if (!canPrepare) return;
    setConfirming(false);
    setRunning({ label: '检查本机环境…', note: '' });
    setResult('');
    try {
      const ensure = () => { if (!mounted.current) throw new Error('页面已切换，自动准备已停止；已执行的记录都保留。'); };
      const environment = await api<EnvReport>('/setup/environment');
      ensure();
      const freshBlocking = (environment.findings ?? []).filter((finding) =>
        finding.repairability !== 'AUTO_REPAIRABLE'
        && finding.repairability !== 'ALREADY_AVAILABLE'
        && (finding.scopes ?? finding.scope ?? []).includes('RESOURCE_PREPARATION'));
      if (environment.findings ? freshBlocking.length > 0 : environment.blockers.length > 0) {
        throw new Error(environment.findings
          ? `本机还有需要先处理的条件：${freshBlocking.map((finding) => finding.message ?? finding.code).join('；')}`
          : '本机还有阻断项，先处理上面的提示。');
      }
      const port = environment.port?.selected ?? environment.port?.port;
      if (!port) throw new Error('服务端口状态读取不到，不能安全地准备 ComfyUI。');
      if (downloadBytes > 0) {
        setRunning({ label: '检查下载网络…', note: '' });
        const network = await api<{ blocked: string[] }>('/setup/connectivity');
        if (network.blocked.length) throw new Error(`下载源无法连接：${network.blocked.join('、')}`);
      }
      const ready = await api<Readiness>('/setup/readiness');
      ensure();
      const filesReady = ready.layers.some((layer) => layer.layer === 'L2_files_verified' && layer.status === 'READY');
      // 以后端台账的最新事实决定「要不要动权重」：只信扫描结果会导致
      // 已核验的 44GB 权重被再次规划下载。
      const freshAvailability = projectId
        ? await api<{ variants?: VariantAvailability[] }>(
          `/setup/variant-availability?project_id=${encodeURIComponent(projectId)}`,
        ).catch(() => null)
        : null;
      ensure();
      if (freshAvailability?.variants) setAvailabilityRows(freshAvailability.variants);
      const freshRow = freshAvailability?.variants?.find(
        (row) => row.variant_id === selectedVariant?.variant_id,
      ) ?? null;
      const evidenceInstalled = freshRow?.installed ?? h3Complete;
      const evidenceVerified = freshRow?.verified ?? (selectedAvailability ? h3Verified : filesReady);

      if (!evidenceVerified) {
        // 先尝试复用本机已有安装：只读核验 + 登记，不复制、不下载。
        // 仅当选中的方案权重确实在本机时才复用；选了别的方案就直接下载它。
        let reused = false;
        if (evidenceInstalled && comfyInstalled && scan) {
          setRunning({ label: '核验本机已有权重…', note: '只读哈希，不复制、不修改你的 ComfyUI' });
          try {
            const verify = await api<{ status: string; rejected?: unknown[]; verified?: unknown[] }>(
              '/setup/existing-installations/verify',
              { ...json('POST', { path: scan.root, snapshot: scan.snapshot }), signal: timeoutSignal(3_600_000) },
            );
            ensure();
            if (verify.status === 'VERIFYING') {
              throw new Error('核验正在进行中，稍等一会儿再点一次。');
            }
            if ((verify.rejected?.length ?? 0) === 0) {
              setRunning({ label: '登记这套已有安装…', note: '不复制、不覆盖' });
              await api('/setup/existing-installations/adopt', json('POST', {
                project_id: projectId, path: scan.root, snapshot: scan.snapshot,
                instance_name: 'shine-h3', port,
              }));
              ensure();
              reused = true;
            }
          } catch (error) {
            reused = false;
            setProblem(`复用已有安装没有成功：${error instanceof Error ? error.message : String(error)}。改为安装受管环境。`);
          }
        }
        if (!reused) {
          const components = new Set<string>(STRUCTURE_COMPONENTS);
          for (const id of selectedVariant ? weightIdsOf(selectedVariant) : []) components.add(id);
          if (vlmWant) components.add(REVIEW_COMPONENTS.vlm);
          if (asrWant) components.add(REVIEW_COMPONENTS.asr);
          setRunning({ label: '创建安装计划…', note: '只包含这台电脑缺的部分' });
          const plan = await api<{ plan_id: string; sha256: string; status: string; download_bytes: number }>(
            '/setup/install-plan', json('POST', {
              catalog: selectedVariant?.catalog_id ?? 'h3-ref2va-windows-x64',
              components: [...components],
              project_id: projectId,
              disk_free: storage?.free_bytes ?? 0,
              variant_id: selectedVariant?.variant_id,
              candidate_download_acknowledged: !!selectedVariant && !selectedVariant.auto_download_allowed,
            }),
          );
          ensure();
          setRunning({ label: '下载并校验权重…', note: `${gib(plan.download_bytes)}，可以随时暂停；关掉页面不会中断` });
          const queued = await approveAndExecuteSetupPlan({ ...plan, kind: 'INSTALL' }, ensure, { wait: false });
          if (isQueuedInstallation(queued)) {
            await waitForInstallationAttempt(queued.attempt_id, (view) => {
              if (mounted.current) { setAttempt(view); setRunning({ label: '下载并校验权重…', note: `${gib(view.confirmed_bytes)} / ${gib(view.total_bytes)}` }); }
            });
          }
        }
      }

      if (imageWant) {
        setRunning({ label: '安装素材出图能力…', note: `约 ${gib(imageBytes)}，独立计划` });
        const imagePlan = await api<{ plan_id: string; sha256: string; status: string; download_bytes: number }>(
          '/setup/install-plan', json('POST', {
            catalog: IMAGE_CATALOG, components: IMAGE_COMPONENTS, project_id: projectId,
            disk_free: storage?.free_bytes ?? 0,
          }),
        );
        const queued = await approveAndExecuteSetupPlan({ ...imagePlan, kind: 'INSTALL' }, ensure, { wait: false });
        if (isQueuedInstallation(queued)) {
          await waitForInstallationAttempt(queued.attempt_id, (view) => {
            if (mounted.current) setRunning({ label: '安装素材出图能力…', note: `${gib(view.confirmed_bytes)} / ${gib(view.total_bytes)}` });
          });
        }
      }

      if (adoptedRoot) {
        // 已经采用的本机安装就是运行环境：不再创建第二个受管实例。
        setRunning({ label: '复用本机已采用的安装…', note: adoptedRoot });
      } else {
        setRunning({ label: '准备受管运行环境…', note: '' });
        let complete = false;
        for (let step = 0; step < 8; step += 1) {
          const plan = await api<{ complete: boolean; plan_id?: string; sha256?: string; status?: string; action?: string; port?: number }>(
            '/setup/next-instance-plan', json('POST', {
              project_id: projectId, instance_name: 'shine-h3', port, mode: 'local',
              ...(selectedVariant ? { catalog: selectedVariant.catalog_id, variant_id: selectedVariant.variant_id } : {}),
            }),
          );
          ensure();
          if (plan.complete) { complete = true; break; }
          await approveAndExecuteSetupPlan({ plan_id: plan.plan_id!, sha256: plan.sha256!, status: plan.status ?? 'AWAITING_APPROVAL', kind: 'INSTANCE_LIFECYCLE' }, ensure);
        }
        if (!complete) throw new Error('环境准备步骤超过预期，已停止。');
      }

      setRunning({ label: '自动登记生成方案…', note: '不需要你手动选择' });
      await api('/setup/profiles', json('POST', { project_id: projectId }));
      ensure();
      const current = await api<RuntimeState>('/setup/runtime');
      if (current.status === 'ACTIVE' && current.services_verified
          && current.candidate?.project_id === projectId) {
        // 这部作品的配置已经激活并核验过：不重复启动服务、不打断正在跑的 Worker。
        setRunning({ label: '完成', note: '' });
        await onRefresh();
        await detect();
        setResult('制作环境已就绪：本机配置已激活并通过服务核验，生成方案已自动登记。可以回到制作流程继续。');
        return;
      }
      setRunning({ label: '生成受管配置…', note: '' });
      await api('/setup/configuration', json('POST', { project_id: projectId }));
      const state = await api<RuntimeState>('/setup/runtime');
      ensure();
      if (!state.candidate || state.candidate.project_id !== projectId) {
        throw new Error('没有生成匹配的运行配置，请重新检测后再试。');
      }
      setRunning({ label: '启动服务核对工作流，完成后自动停止…', note: '第一次会慢一些' });
      const validated = await api<{ status: string }>('/setup/services/validate', {
        ...json('POST', { project_id: projectId, recipe_sha256: state.candidate.recipe_sha256 }),
        signal: timeoutSignal(3_600_000),
      });
      if (validated.status !== 'READY') throw new Error(`服务检测没有通过（${validated.status}）。`);
      await api('/setup/runtime/activate', json('POST', {
        project_id: projectId, recipe_sha256: state.candidate.recipe_sha256,
      }));
      setRunning({ label: '完成', note: '' });
      await onRefresh();
      await detect();
      setResult('制作环境已就绪：生成方案已自动登记、服务已核验、配置已激活。可以回到制作流程继续。');
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      if (mounted.current) setRunning(null);
    }
  }

  /** 只读检测出的补装计划在这里一次确认；不做任意命令、不装驱动。 */
  async function repairTools() {
    const plan = env?.repair_plan;
    if (!plan) return;
    setProblem(''); setResult('');
    setRunning({ label: '正在补装基础工具…', note: '固定清单 + SHA-256；已下载的部分会保留，Node/npm/uv 不会重复安装' });
    try {
      const result = await api<{ status: string; message?: string; unsatisfied_packages?: string[] }>(
        '/setup/tool-repair/execute', {
          ...json('POST', { plan_id: plan.plan_id }),
          signal: timeoutSignal(3_600_000),
        });
      if (result.status !== 'SUCCEEDED') {
        throw new Error(`补装未完成：${result.status}`
          + (result.message ? `（${result.message}）` : '')
          + (result.unsatisfied_packages?.length ? `，仍需处理：${result.unsatisfied_packages.join('、')}` : '')
          + '。已下载的数据会保留，可以重试。');
      }
      setResult('基础工具已补装并通过复查。');
      await detect();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      if (mounted.current) setRunning(null);
    }
  }

  /** 检测到出图权重后，直接在这里验证一次真实出图。 */
  async function testImage() {
    setImageTesting('启动本机出图渲染器…');
    try {
      if (image?.renderer.state !== 'RUNNING') {
        await api('/setup/image-runtime/start', json('POST', {}));
        for (let tick = 0; tick < 40; tick += 1) {
          await new Promise((resolve) => setTimeout(resolve, 3000));
          const state = await api<ImageReadiness>('/setup/image-readiness');
          setImage(state);
          if (state.renderer.state === 'RUNNING') break;
        }
      }
      setImageTesting('验证三类真实小样…');
      const run = await api<{ run_id: string }>('/setup/image-runtime/validate', json('POST', {
        project_id: projectId,
        idempotency_key: `image-validate-${projectId}-${image?.renderer.state ?? 'unknown'}`,
        families: ['character', 'scene', 'prop'],
      }));
      setImageTesting(`已排队（批次 ${run.run_id.slice(0, 8)}…），后台出图完成后这里会更新结果。`);
      await loadImage();
    } catch (error) {
      setImageTesting(error instanceof Error ? error.message : String(error));
    }
  }

  /** 已有权重时勾选能力：先二次确认「重复下载 / 仅复用」，不静默重下。 */
  function toggleCap(key: 'image' | 'vlm' | 'asr', present: boolean, checked: boolean) {
    if (checked && present) { setRedownloadPrompt(key); return; }
    setCaps((current) => ({ ...current, [key]: checked }));
    if (!checked) setForce((current) => ({ ...current, [key]: false }));
  }

  const verdict = verdictLine(variants);
  const modelChips = [
    { id: 'comfyui', label: 'ComfyUI', found: comfyInstalled ? 1 : 0, total: 1 },
    { id: 'h3', label: 'H3 视频权重', found: selectedPresence.found, total: selectedPresence.required || 6 },
    { id: 'image', label: '出图权重', found: imagePresent ? 1 : 0, total: 1 },
    { id: 'vlm', label: '画面审片', found: vlmPresence.found, total: vlmTotal },
    { id: 'asr', label: '语音核对', found: asrPresence.found, total: asrTotal },
  ];

  return <section className="card section-card setup-center" id="setup">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">00 / 制作环境</div>
        <h2>制作环境</h2>
        <p>检测一次，看清这台电脑能跑什么；确认一次，剩下的自动完成。</p>
      </div>
      <div className="setup-detect-bar">
        <button type="button" className="btn-secondary" disabled={phase === 'detecting' || !!running}
          onClick={() => void detect()}>
          {phase === 'detecting' ? <RefreshCw size={13} className="spin" /> : <Cpu size={13} />}
          {phase === 'ready' ? '重新检测' : '检测本机'}
        </button>
        {phase === 'ready' && detectedAt && <span className="run-hint">上次检测 {new Date(detectedAt).toLocaleTimeString()}</span>}
        {phase === 'idle' && <GuideTip>只读检测，不下载、不启动任何东西；检测一次后结果保留，切页不再重复检测</GuideTip>}
      </div>
    </div>

    {phase === 'error' && <p className="form-error" role="alert">检测没有完成：{problem}</p>}
    {phase === 'detecting' && <p className="run-hint">正在读取显卡、显存、磁盘和已有安装…</p>}
    {phase === 'idle' && <p className="run-hint">点「检测本机」看清这台电脑能跑什么；检测只在你点击时运行。</p>}

    {phase === 'ready' && <>
      <p className="setup-verdict"><b>{verdict}</b></p>

      {selectedAvailability && !selectedAvailability.production_ready && selectedAvailability.next_step && (
        <p className="run-hint" data-testid="setup-next-step">
          还差一步：{selectedAvailability.next_step.label}。{selectedAvailability.next_step.reason}
        </p>
      )}

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
          <span>已固定下载源与哈希，约 {gib(env?.repair_plan?.download_bytes ?? 0)}；使用 Windows 自带 PowerShell 5.1 补装，完成后自动复查。</span>
        </div>
        <button type="button" className="btn-secondary" disabled={!!running} onClick={() => void repairTools()}>
          自动补装
        </button>
      </div>}

      <div className="setup-model-chips" role="list" aria-label="本机已有的模型">
        {modelChips.map((chip) => <span role="listitem" key={chip.id}
          className={`setup-model-chip ${chip.found >= chip.total ? 'ok' : chip.found ? 'part' : ''}`}>
          {chip.found >= chip.total ? <Check size={11} /> : <AlertTriangle size={11} />}
          {chip.label}
          <em>{chip.total > 1 ? `${chip.found}/${chip.total}` : chip.found ? '已有' : '未装'}</em>
        </span>)}
      </div>

      <div className="setup-presets">
        <h3>选一套 H3 生成方案 <GuideTip tight>只有「可直接安装」的方案会真的下载；候选方案要先在本机跑通</GuideTip></h3>
        <div className="setup-preset-list">
          {allVariants.map((row) => {
            const presence = variantPresence(row, scan);
            const fact = availabilityOf(row.variant_id);
            const local = fact ? fact.installed : presence.required > 0 && presence.found === presence.required;
            const blocked = row.acceptance_status === 'BLOCKED';
            // 已有一套权重不锁死其他方案：候选也可选、可显式确认后下载；
            // 只有没有可核对权重来源的 BLOCKED 槽位才禁用。
            const selectable = !blocked;
            const reason = fact
              ? fact.reason
              : local ? '本机已找到这套权重'
                : row.auto_download_allowed ? `需要下载 ${gib(row.total_bytes)}`
                  : blocked ? '这套没有可核对的权重来源，暂不能使用'
                    : '候选 · 未真机验收；确认后可下载权重（下载不等于验收）';
            return <label key={row.variant_id} className={`setup-preset${variantId === row.variant_id ? ' active' : ''}${selectable ? '' : ' disabled'}`}>
              <input type="radio" name="h3-preset" checked={variantId === row.variant_id} disabled={!selectable || !!running}
                onChange={() => setVariantId(row.variant_id)} />
              <span className="setup-preset-text">
                <b>{row.title}</b>
                <em>{reason}</em>
              </span>
              <span className={`chip ${local ? 'ok' : row.auto_download_allowed ? '' : 'warn'}`}>
                {fact?.verified && fact.bound ? '本机已核验 · 已绑定'
                  : fact?.verified ? '本机已核验'
                    : local ? '本机已有' : row.auto_download_allowed ? '可安装' : blocked ? '候选' : '候选 · 可下载'}
              </span>
            </label>;
          })}
        </div>
      </div>

      <div className="setup-capabilities">
        <h3>要准备哪些能力 <GuideTip tight>勾选才会下载；不勾的以后可以随时回来补</GuideTip></h3>
        <label className="setup-cap locked">
          <Video size={14} /><span><b>H3 视频生成</b><em>
            {selectedAvailability?.verified
              ? (selectedAvailability.bound ? '权重已逐字节核验，并已绑定到当前 ComfyUI 实例' : '权重已逐字节核验，等待实例绑定')
              : `${selectedPresence.found}/${selectedPresence.required || 6} 个权重文件已找到`}
          </em></span>
          <span className="chip ok">必装</span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.image} disabled={!!running}
            onChange={(event) => toggleCap('image', imagePresent, event.target.checked)} />
          <ImageIcon size={14} /><span><b>素材出图（人物 / 场景 / 道具）</b>
            <em className={imagePresent ? 'cap-found-warn' : undefined}>{imagePresent ? '本机已找到权重' : `需要下载 ${gib(imageBytes)}`}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.vlm} disabled={!!running}
            onChange={(event) => toggleCap('vlm', vlmReady, event.target.checked)} />
          <MonitorCheck size={14} /><span><b>画面审片（只给建议，不卡出片）</b>
            <em className={vlmReady ? 'cap-found-warn' : undefined}>{vlmReady ? '本机已有' : vlmPresence.found ? `只找到 ${vlmPresence.found}/${vlmTotal} 个文件，需要补齐` : `需要下载 ${gib(REVIEW_BYTES.vlm)}`}</em></span>
        </label>
        <label className="setup-cap">
          <input type="checkbox" checked={caps.asr} disabled={!!running}
            onChange={(event) => toggleCap('asr', asrReady, event.target.checked)} />
          <Mic size={14} /><span><b>语音核对（只给建议，不卡出片）</b>
            <em className={asrReady ? 'cap-found-warn' : undefined}>{asrReady ? '本机已有' : asrPresence.found ? `只找到 ${asrPresence.found}/${asrTotal} 个文件，需要补齐` : `需要下载 ${gib(REVIEW_BYTES.asr)}`}</em></span>
        </label>
      </div>

      {redownloadPrompt && <div className="setup-confirm" role="group" aria-label="重复下载确认">
        <div>
          <b>「{CAP_LABELS[redownloadPrompt]}」本机已找到权重。</b>
          <span>默认直接复用已有权重，不重复下载；仍要重复下载会再下载约 {gib(redownloadPrompt === 'image' ? imageBytes : redownloadPrompt === 'vlm' ? REVIEW_BYTES.vlm : REVIEW_BYTES.asr)} 并写入隔离缓存，不会删除已核验文件。</span>
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
          {running ? running.label : downloadBytes > 0 ? `确认并下载安装（约 ${gib(downloadBytes)}）` : '确认并完成配置（不下载）'}
        </button>
        <GuideTip>
          {!projectId ? '先在上方创建作品'
            : downloadBytes > 0 ? '点一次确认即可，下载后可离开页面' : '本机权重已齐，点一次自动登记方案并激活'}
        </GuideTip>
        {envBlocked && <span className="setup-warn"><AlertTriangle size={12} /> 先处理硬件或工具阻断项</span>}
        {storage?.requires_selection && <span className="setup-warn"><AlertTriangle size={12} /> 先在「作品输出目录」选择非系统盘</span>}
      </div>

      {confirming && !running && <div className="setup-confirm" role="group" aria-label="确认准备制作环境">
        <div>
          <b>{downloadBytes > 0 ? `将下载约 ${gib(downloadBytes)}，并自动完成配置` : '本机权重已齐，将自动完成配置'}</b>
          <span>目录 {storage?.root} · 不会改动你原有的 ComfyUI，也不会删除已核验的模型</span>
          <span>复用/下载 → 登记生成方案 → 生成受管配置 → 启动检测服务 → 激活</span>
          {selectedVariant && !selectedVariant.auto_download_allowed && selectedVariant.acceptance_status !== 'BLOCKED' && (
            <span className="setup-warn"><AlertTriangle size={12} /> 候选方案：下载权重不等于验收，用于生产前必须先在本机真机跑通。点「开始准备」即确认下载候选权重。</span>
          )}
        </div>
        <button type="button" className="btn-primary" onClick={() => void prepare()}>开始准备</button>
        <button type="button" className="btn-text" onClick={() => setConfirming(false)}>取消</button>
      </div>}

      {running && <p className="run-hint" role="status">{running.label}{running.note ? ` · ${running.note}` : ''}</p>}
      {attempt && !attempt.terminal && <p className="run-hint">
        {attempt.stage === 'WAITING_FOR_TARGET' || attempt.waiting_for_target
          ? '正在等待前面的资源安装任务完成；轮到这个任务时会自动开始，不需要你重复点击。'
          : `已确认 ${gib(attempt.confirmed_bytes)} / ${gib(attempt.total_bytes)} · 关闭页面不会中断下载`}
      </p>}
      {result && <p className="ok-line" role="status">{result}</p>}
      {problem && phase === 'ready' && <p className="form-error" role="alert">{problem}</p>}

      {image?.configured && <div className="setup-image-test">
        <div>
          <b>素材出图能力</b>
          <em>{image.layer === 'RENDER_VERIFIED' ? '三类小样都已验证'
            : image.layer === 'GRAPH_VERIFIED' ? '节点已核对，可以验证真实出图'
              : image.renderer.state === 'RUNNING' ? '渲染器运行中' : '权重已就位'}</em>
        </div>
        <button type="button" className="btn-secondary" disabled={!!imageTesting} onClick={() => void testImage()}>
          {imageTesting ? <Loader2 size={13} className="spin" /> : <ImageIcon size={13} />}
          测试出图
        </button>
        {imageTesting && <p className="run-hint" role="status">{imageTesting}</p>}
      </div>}

      <details className="setup-details">
        <summary><ChevronRight size={12} /> 检测明细与专业选项</summary>
        <div className="setup-detail-body">
          <p>已有安装：{scan ? scan.root : '常见目录未发现 ComfyUI（可以只安装受管环境）'}
            {scan?.software ? ` · ${comfyInstalled ? '主程序已安装' : '主程序未识别'}` : ''}</p>
          <p>扫描 {scan?.scanned_files ?? 0} 个文件 · 找到候选 {scan?.matches.length ?? 0} 项 ·
            还缺固定版本文件 {scan?.missing.length ?? 0} 项</p>
          <p>服务状态：{runtime?.status === 'ACTIVE' && runtime.services_verified ? '配置已激活，服务已核验' : '待配置或待核验'}
            {' · '}就绪层 {readiness?.layers.filter((layer) => layer.status === 'READY').length ?? 0}/{readiness?.layers.length ?? 0}
          </p>
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
