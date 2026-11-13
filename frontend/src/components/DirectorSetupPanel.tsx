import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Cpu, Image as ImageIcon, Loader2, Mic, MonitorCheck, ShieldCheck, Sparkles, Wand2 } from 'lucide-react';
import { api, json, type AgentRoute, type Project, type ProviderProfile, type SystemStatus, type WorkflowProfile } from '../api';
import { SetupCenter } from './SetupCenter';
import OutputDirectoryPanel from './OutputDirectoryPanel';
import { LLMSetupPanel } from './LLMSetupPanel';
import { GuideTip } from './GuideTip';
import { approveAndExecuteSetupPlan, isQueuedInstallation, waitForInstallationAttempt } from '../setupExecution';

type Tab = 'env' | 'models' | 'extra';

type Advisory = {
  status: string;
  model_id: string;
  advisory: boolean;
  model_files_present: number;
  model_files_expected: number;
  runtime_ready: boolean;
  code?: string | null;
};

type EnvReport = { advisory_reviews?: { VLM?: Advisory; ASR?: Advisory } };

type VoiceReadiness = {
  status: string;
  voice_master_capable: boolean;
  roles: Array<{ role: string; code: string | null; files_present?: boolean | null }>;
};

type LlmReadiness = { status: string; roles: Array<{ role: string; profile_name: string | null; code: string | null }> };

type ImageState = { configured: boolean; layer: string; renderer: { state: string }; upstream: { required_bytes: number } };

type Props = {
  focusTab?: Tab;
  project?: Project;
  projectId: string;
  projectTitle: string;
  setProjectTitle: (value: string) => void;
  createProject: () => void;
  system: SystemStatus | null;
  profiles: ProviderProfile[];
  routes: AgentRoute[];
  workflowProfiles: WorkflowProfile[];
  busy: boolean;
  refreshEpoch: number;
  onRefresh: () => Promise<void>;
  onProjectSettings: (patch: Record<string, unknown>) => void;
  onCreateProfile: (payload: Record<string, unknown>) => void;
  onTestProfile: (profileId: string) => void;
  onSetKey: (profileId: string, key: string) => void;
  onDeleteProfile: (profileId: string) => void;
  onRoute: (role: string, profileId: string) => void;
  onTestComfy: (baseUrl: string) => Promise<{ ok: boolean; message: string; node_count?: number }>;
  onRegisterWorkflow: (payload: Record<string, unknown>) => void;
  onValidateWorkflow: (profileId: string) => void;
  onDeleteWorkflow: (profileId: string) => void;
  onBindStandardParameters: (profileId: string) => void;
  onOpenExpert: () => void;
};

function gib(bytes?: number | null): string {
  if (!bytes) return '—';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

/**
 * 导演模式的基础配置：三步，一处确认。
 *
 * ① 安装与下载 —— 检测、选预设、确认下载都在同一张卡里完成；
 * ② 文本模型 —— 只有一个模型时自动配好全部工种并测一次，然后折叠；
 * ③ 独立能力 —— 画面审片、语音核对、素材出图各自可选，互不阻断。
 *
 * 工作流登记、运行配置、服务核验与激活都是自动的：这一页不再要求人选。
 */
export default function DirectorSetupPanel({
  focusTab = 'env', projectId, projectTitle, setProjectTitle, createProject,
  system, refreshEpoch, onRefresh,
}: Props) {
  const [tab, setTab] = useState<Tab>(focusTab);
  const [llm, setLlm] = useState<LlmReadiness | null>(null);
  const [voice, setVoice] = useState<VoiceReadiness | null>(null);
  const [advisory, setAdvisory] = useState<{ VLM?: Advisory; ASR?: Advisory }>({});
  const [image, setImage] = useState<ImageState | null>(null);
  const [extraBusy, setExtraBusy] = useState('');
  const [extraNote, setExtraNote] = useState('');

  useEffect(() => { setTab(focusTab); }, [focusTab]);

  const load = useCallback(async () => {
    const [llmState, voiceState, envState, imageState] = await Promise.allSettled([
      api<LlmReadiness>('/setup/llm-readiness'),
      api<VoiceReadiness>('/setup/voice-readiness'),
      api<EnvReport>('/setup/environment'),
      api<ImageState>('/setup/image-readiness'),
    ]);
    if (llmState.status === 'fulfilled') setLlm(llmState.value);
    if (voiceState.status === 'fulfilled') setVoice(voiceState.value);
    if (envState.status === 'fulfilled') setAdvisory(envState.value.advisory_reviews ?? {});
    if (imageState.status === 'fulfilled') setImage(imageState.value);
  }, []);

  useEffect(() => { void load(); }, [load, refreshEpoch]);

  const mode = system?.production_mode?.mode ?? null;
  const ready = mode === 'READY' || mode === 'RUNNING';
  const llmReady = llm?.status === 'READY';
  const missingRoles = (llm?.roles ?? []).filter((row) => row.code);

  async function installAdvisory(kind: 'VLM' | 'ASR') {
    if (!projectId) return;
    const model = kind === 'VLM' ? 'qwen3-vl-8b-fp8' : 'qwen3-asr-0.6b';
    const label = kind === 'VLM' ? '画面审片' : '语音核对';
    if (!window.confirm(`下载并启用${label}模型？\n这是独立能力，只给建议、不阻断出片；权重按固定清单逐字节核验。`)) return;
    setExtraBusy(kind);
    setExtraNote('');
    try {
      const storage = await api<{ free_bytes: number }>('/setup/storage');
      const plan = await api<{ plan_id: string; sha256: string; status: string; download_bytes: number }>(
        '/setup/install-plan', json('POST', {
          catalog: 'h3-ref2va-windows-x64',
          components: [model],
          project_id: projectId,
          disk_free: storage.free_bytes,
        }),
      );
      const queued = await approveAndExecuteSetupPlan({ ...plan, kind: 'INSTALL' }, () => undefined, { wait: false });
      if (isQueuedInstallation(queued)) {
        await waitForInstallationAttempt(queued.attempt_id);
      }
      setExtraNote(`${label}模型已下载并核验；可以在审片设置里启用它。`);
      await load();
      await onRefresh();
    } catch (error) {
      setExtraNote(error instanceof Error ? error.message : String(error));
    } finally {
      setExtraBusy('');
    }
  }

  const vlm = advisory.VLM;
  const asr = advisory.ASR;

  return <div className="director-page director-setup" id="director-setup">
    <section className="welcome-row">
      <div>
        <div className="eyebrow"><span className="eyebrow-line" /> 基础配置</div>
        <h1>先把机器调好，<em>再让 AI 开工。</em></h1>
        <p>检测、确认、自动完成。以后想补审片或语音能力，再到「独立能力」里单独开。</p>
      </div>
      <button className="btn-secondary" onClick={() => void onRefresh()}>刷新全部状态</button>
    </section>

    <section className="setup-status-grid">
      <div className={`setup-status ${ready ? 'ok' : 'pending'}`}>
        <div className="setup-status-icon"><Cpu size={16} /></div>
        <div><b>本机生成环境</b><span>{ready ? system?.production_mode?.label ?? '已就绪' : system?.production_mode?.detail ?? '尚未检查'}</span></div>
        {ready ? <Check size={15} /> : <AlertTriangle size={15} />}
      </div>
      <div className={`setup-status ${llmReady ? 'ok' : 'pending'}`}>
        <div className="setup-status-icon"><Wand2 size={16} /></div>
        <div><b>文本模型（LLM）</b><span>{llmReady ? '连接检查通过，可用于分镜与导演' : missingRoles.length ? `还有 ${missingRoles.length} 个工种没配模型` : '尚未配置'}</span></div>
        {llmReady ? <Check size={15} /> : <AlertTriangle size={15} />}
      </div>
      <div className={`setup-status ${vlm?.status === 'READY' || asr?.status === 'READY' ? 'ok' : ''}`}>
        <div className="setup-status-icon"><ShieldCheck size={16} /></div>
        <div><b>独立能力（可选）</b><span>{vlm?.status === 'READY' ? '画面审片可用' : '画面审片未装'}{' · '}
          {asr?.status === 'READY' ? '语音核对可用' : '语音核对未装'}</span></div>
        {vlm?.status === 'READY' || asr?.status === 'READY' ? <Check size={15} /> : <Sparkles size={15} />}
      </div>
    </section>

    <nav className="setup-tabs" aria-label="基础配置分区">
      <button type="button" className={tab === 'env' ? 'active' : ''} onClick={() => setTab('env')}>① 安装与下载</button>
      <button type="button" className={tab === 'models' ? 'active' : ''} onClick={() => setTab('models')}>② 文本模型</button>
      <button type="button" className={tab === 'extra' ? 'active' : ''} onClick={() => setTab('extra')}>③ 独立能力（可选）</button>
    </nav>

    {!projectId && <section className="setup-needs-project card">
      <div>
        <b>还差一个作品</b>
        <p>安装、生成方案和素材都要绑定到一个作品。先起个名字即可，之后可以改。</p>
      </div>
      <div className="create-project">
        <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)}
          placeholder="给作品起个名字（如：巷口的灯）" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
        <button className="btn-secondary" title="创建作品后即可继续配置" disabled={!projectTitle.trim()} onClick={createProject}>创建并继续</button>
        <GuideTip>建好作品后，这一页的按钮就会亮起来</GuideTip>
      </div>
    </section>}

    {tab === 'env' && <div className="setup-tab-body">
      <SetupCenter projectId={projectId} refreshEpoch={refreshEpoch} onRefresh={onRefresh} />
      <OutputDirectoryPanel />
    </div>}

    {tab === 'models' && <div className="setup-tab-body">
      <LLMSetupPanel onChanged={onRefresh} />
    </div>}

    {tab === 'extra' && <div className="setup-tab-body">
      <div className="notice">
        <Sparkles size={13} />
        <div>
          <p><b>这三项都不影响出片。</b>不装时用人工复核；装好后只给建议，仍然不阻断任何镜头。</p>
          <p>素材出图在「① 安装与下载」里勾选并测试；这里单独开关审片与语音。</p>
        </div>
      </div>

      <section className="card section-card capability-card">
        <div className="capability-head">
          <div>
            <div className="section-kicker">ADVISORY / VLM</div>
            <h3><MonitorCheck size={15} /> 画面审片</h3>
            <p>检查人物一致性、构图和明显穿帮；结果只作建议。</p>
          </div>
          <span className={`chip ${vlm?.status === 'READY' ? 'ok' : 'warn'}`}>
            {vlm?.status === 'READY' ? '已启用' : '未启用'}
          </span>
        </div>
        <p className="capability-facts">
          模型文件 {vlm?.model_files_present ?? 0}/{vlm?.model_files_expected ?? 13}
          {vlm && !vlm.runtime_ready ? ' · 运行依赖还需要补齐' : ''}
          {vlm?.code ? ' · 只影响建议，不影响出片' : ''}
        </p>
        {vlm?.status !== 'READY' && <button type="button" className="btn-secondary"
          disabled={!projectId || !!extraBusy} onClick={() => void installAdvisory('VLM')}>
          {extraBusy === 'VLM' ? <Loader2 size={13} className="spin" /> : <MonitorCheck size={13} />}
          下载并启用画面审片
        </button>}
      </section>

      <section className="card section-card capability-card">
        <div className="capability-head">
          <div>
            <div className="section-kicker">ADVISORY / ASR</div>
            <h3><Mic size={15} /> 语音核对</h3>
            <p>把生成的台词转回文字，核对是否念错、漏念；结果只作建议。</p>
          </div>
          <span className={`chip ${asr?.status === 'READY' ? 'ok' : 'warn'}`}>
            {asr?.status === 'READY' ? '已启用' : '未启用'}
          </span>
        </div>
        <p className="capability-facts">
          模型文件 {asr?.model_files_present ?? 0}/{asr?.model_files_expected ?? 10}
          {asr && !asr.runtime_ready ? ' · 运行依赖还需要补齐' : ''}
          {asr?.code ? ' · 只影响建议，不影响出片' : ''}
        </p>
        {asr?.status !== 'READY' && <button type="button" className="btn-secondary"
          disabled={!projectId || !!extraBusy} onClick={() => void installAdvisory('ASR')}>
          {extraBusy === 'ASR' ? <Loader2 size={13} className="spin" /> : <Mic size={13} />}
          下载并启用语音核对
        </button>}
      </section>

      <section className="card section-card capability-card">
        <div className="capability-head">
          <div>
            <div className="section-kicker">MATERIAL / IMAGE</div>
            <h3><ImageIcon size={15} /> 素材出图</h3>
            <p>生成人物、场景、道具参考图；不装时素材全部由你上传。</p>
          </div>
          <span className={`chip ${image?.layer === 'RENDER_VERIFIED' ? 'ok' : ''}`}>
            {image?.configured ? (image.layer === 'RENDER_VERIFIED' ? '已验证' : '已配置') : '未配置'}
          </span>
        </div>
        <p className="capability-facts">
          {image?.configured
            ? `权重约 ${gib(image.upstream.required_bytes)} · 渲染器${image.renderer.state === 'RUNNING' ? '运行中' : '空闲'}`
            : '还没有安装出图权重'}
        </p>
        <button type="button" className="btn-secondary" onClick={() => {
          setTab('env');
          window.setTimeout(() => document.getElementById('setup')?.scrollIntoView({ block: 'start' }), 40);
        }}>到「安装与下载」检测并测试出图</button>
      </section>

      <section className="card section-card capability-card">
        <div className="capability-head">
          <div>
            <div className="section-kicker">VOICE / TIMBRE</div>
            <h3><Mic size={15} /> 固定音色</h3>
            <p>需要每个角色始终同一音色时才用；不配时对白由 H3 原生生成。</p>
          </div>
          <span className={`chip ${voice?.voice_master_capable ? 'ok' : ''}`}>
            {voice?.voice_master_capable ? '可用' : '未配置'}
          </span>
        </div>
      </section>

      {extraNote && <p className="run-hint" role="status">{extraNote}</p>}
    </div>}

    <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> DIRECTOR SETUP</span><span>所有配置保存在本机</span></footer>
  </div>;
}
