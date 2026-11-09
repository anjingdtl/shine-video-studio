import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Cpu, Mic, Settings2, Sparkles, Wand2 } from 'lucide-react';
import { api, type AgentRoute, type Project, type ProviderProfile, type SystemStatus, type WorkflowProfile } from '../api';
import { SetupPanel } from './SetupPanel';
import { VariantReadinessCard } from './VariantReadinessCard';
import { DeploymentRecommendationCard, type DeploymentChoice } from './DeploymentRecommendationCard';
import { ImageCapabilityCard } from './ImageCapabilityCard';
import SettingsPanel from './SettingsPanel';

type Tab = 'env' | 'models' | 'voice';

/** 官方 W6A8 低显存候选：它有自己的部署目录、自己的生成方案和自己的验收状态，
 *  不与 INT8 基线共用任何一条结论。 */
const LOWEND_VARIANT_ID = 'ref2va-lowend-candidate';

type VoiceReadiness = {
  status: string;
  voice_master_capable: boolean;
  roles: Array<{ role: string; code: string | null; files_present?: boolean | null }>;
};

type LlmReadiness = { status: string; roles: Array<{ role: string; profile_name: string | null; code: string | null }> };

type Props = {
  /** 进入基础配置时直达的分区：导演模式内“去配置模型服务/生成方案”等引导会指到 models。 */
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
  /** 新用户不知道专业模式在哪：由 App 负责切过去。 */
  onOpenExpert: () => void;
};

function roleText(role: { role: string; code: string | null }): string {
  const names: Record<string, string> = { design: '角色声音设计', base: '声音克隆', asr: '语音转写', align: '强制对齐' };
  const name = names[role.role] ?? role.role;
  return role.code ? `${name}：未配置（可选）` : `${name}：可用`;
}

/** 导演模式的基础配置页：新用户不切换到专业模式也能把机器装好。
 *
 * 三件事按依赖顺序排列：先看本机能不能跑（安装/下载），再配文本模型与生成
 * 方案（LLM/H3 工作流），最后才是可选的固定音色。所有面板都复用专业模式的
 * 同一套接口，导演模式不另造一套能力。
 */
export default function DirectorSetupPanel({
  focusTab = 'env',
  project, projectId, projectTitle, setProjectTitle, createProject, system, profiles, routes,
  workflowProfiles, busy, refreshEpoch, onRefresh,
  onProjectSettings, onCreateProfile, onTestProfile, onSetKey, onDeleteProfile, onRoute,
  onTestComfy, onRegisterWorkflow, onValidateWorkflow, onDeleteWorkflow, onBindStandardParameters,
  onOpenExpert,
}: Props) {
  const [tab, setTab] = useState<Tab>(focusTab);
  /** 推荐卡片里的用途选择：只决定下方显示哪些安装计划，不替你下载任何东西。 */
  const [deploymentChoice, setDeploymentChoice] = useState<DeploymentChoice>({ h3_video: true, image_prep: true });
  const [llm, setLlm] = useState<LlmReadiness | null>(null);
  const [voice, setVoice] = useState<VoiceReadiness | null>(null);

  const loadSummaries = useCallback(async () => {
    const [llmState, voiceState] = await Promise.allSettled([
      api<LlmReadiness>('/setup/llm-readiness'),
      api<VoiceReadiness>('/setup/voice-readiness'),
    ]);
    if (llmState.status === 'fulfilled') setLlm(llmState.value);
    if (voiceState.status === 'fulfilled') setVoice(voiceState.value);
  }, []);

  useEffect(() => { void loadSummaries(); }, [loadSummaries, refreshEpoch]);

  const mode = system?.production_mode?.mode ?? null;
  const profiled = workflowProfiles.filter((profile) => profile.mode.toLowerCase() === 'ref2va');
  const ready = mode === 'READY' || mode === 'RUNNING';
  const llmReady = llm?.status === 'READY';
  const missingRoles = (llm?.roles ?? []).filter((row) => row.code);
  const blockedVoice = (voice?.roles ?? []).filter((row) => row.code);

  return <div className="director-page director-setup" id="director-setup">
    <section className="welcome-row">
      <div>
        <div className="eyebrow"><span className="eyebrow-line" /> 基础配置</div>
        <h1>先把机器调好，<em>再让 AI 开工。</em></h1>
        <p>这三步不需要专业模式：装好本机环境、配置文本模型、选一个生成方案。任何一步没完成，制作页都会明确告诉你缺什么。</p>
      </div>
      <button className="secondary-button" onClick={() => void onRefresh()}>刷新全部状态</button>
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
      <div className={`setup-status ${profiled.length ? 'ok' : 'pending'}`}>
        <div className="setup-status-icon"><Settings2 size={16} /></div>
        <div><b>生成方案</b><span>{profiled.length ? `${profiled.length} 个生成方案已登记` : '还没有可用的生成方案'}</span></div>
        {profiled.length ? <Check size={15} /> : <AlertTriangle size={15} />}
      </div>
    </section>

    <nav className="setup-tabs" aria-label="基础配置分区">
      <button type="button" className={tab === 'env' ? 'active' : ''} onClick={() => setTab('env')}>① 安装与下载</button>
      <button type="button" className={tab === 'models' ? 'active' : ''} onClick={() => setTab('models')}>② 模型与生成方案</button>
      <button type="button" className={tab === 'voice' ? 'active' : ''} onClick={() => setTab('voice')}>③ 固定音色（可选）</button>
    </nav>

    {!projectId && <section className="setup-needs-project card">
      <div>
        <b>还差一个作品</b>
        <p>安装计划、生成方案和素材都要绑定到一个作品。先起个名字即可，之后可以改；这一步不需要本机环境已经就绪。</p>
      </div>
      <div className="create-project">
        <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)}
          placeholder="给作品起个名字（如：巷口的灯）" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
        <button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>创建并继续</button>
      </div>
    </section>}

    {tab === 'env' && <div className="setup-tab-body">
      <div className="module-guide">
        <Sparkles size={13} />
        <div>
          <p><b>第一次使用：</b>点「检测本机」看已有环境；没有 ComfyUI 或 H3 模型时，按页面给出的安装计划下载。安装会先让你确认体积与目录，不会静默下载。</p>
          <p><b>已经装过：</b>可以复用现有目录，系统会核对固定版本文件，不会重复下载，也不会覆盖你的自定义节点。</p>
        </div>
      </div>
      <DeploymentRecommendationCard onChoice={setDeploymentChoice} />
      <SetupPanel projectId={projectId} onRefresh={onRefresh} refreshEpoch={refreshEpoch}
        includeImageCapability={deploymentChoice.image_prep} />
      {deploymentChoice.image_prep
        ? <ImageCapabilityCard projectId={projectId} busy={busy} />
        : <p className="run-hint">你选择了不安装生图组合：素材全部自行上传即可，正式视频生产不受影响。需要时可在上方重新勾选。</p>}
    </div>}

    {tab === 'models' && <div className="setup-tab-body">
      <div className="module-guide">
        <Settings2 size={13} />
        <div>
          <p><b>文本模型</b>负责写分镜、导演、摄影与剪辑方案；一个模型可以承担全部文本工种。密钥只存在本机。</p>
          <p><b>生成方案</b>决定 H3 怎么出片。只有登记并通过本机节点验证的方案，才能用于真实制作；方案缺少宽高映射时，制作页的分辨率设置会明确报错，而不是偷偷按旧尺寸渲染。</p>
        </div>
      </div>
      <section className="card section-card">
        <div className="section-heading compact">
          <div>
            <div className="section-kicker">GENERATION PROFILES</div>
            <h2>生成方案与画幅</h2>
            <p>这里的方案就是导演页「开始制作」可选的生成方案。带「宽高可调」的方案才会按你选的分辨率渲染。</p>
          </div>
        </div>
        {profiled.length === 0 && <div className="blocked-callout">
          <AlertTriangle size={15} />
          <span>还没有生成方案。先在「安装与下载」完成环境检测，系统会自动登记并验证本项目可用的出片方案。</span>
        </div>}
        <div className="workflow-list">
          {profiled.map((profile) => {
            const sized = 'width' in (profile.parameter_map ?? {}) && 'height' in (profile.parameter_map ?? {});
            return <div className="workflow-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{profile.mode} · SHA {profile.graph_sha256.slice(0, 10)}…</span></div>
              {sized ? <span className="workflow-size-ok">宽高可调</span> : <button className="small-action" disabled={busy}
                onClick={() => onBindStandardParameters(profile.id)}>补齐宽高参数</button>}
              <button className="small-action" disabled={busy} onClick={() => onValidateWorkflow(profile.id)}>验证本机节点</button>
            </div>;
          })}
        </div>
      </section>
      <VariantReadinessCard
        projectId={projectId}
        variantId={LOWEND_VARIANT_ID}
        title="W6A8 · 低显存候选"
        onChanged={onRefresh}
      />
      <SettingsPanel
        onRefresh={onRefresh}
        project={project}
        profiles={profiles}
        routes={routes}
        workflowProfiles={workflowProfiles}
        system={system}
        busy={busy}
        onProjectSettings={onProjectSettings}
        onCreateProfile={onCreateProfile}
        onTestProfile={onTestProfile}
        onSetKey={onSetKey}
        onDeleteProfile={onDeleteProfile}
        onRoute={onRoute}
        onTestComfy={onTestComfy}
        onRegisterWorkflow={onRegisterWorkflow}
        onValidateWorkflow={onValidateWorkflow}
        onDeleteWorkflow={onDeleteWorkflow}
        onBindStandardParameters={onBindStandardParameters}
      />
    </div>}

    {tab === 'voice' && <div className="setup-tab-body">
      <section className="card section-card">
        <div className="section-heading compact">
          <div>
            <div className="section-kicker">OPTIONAL / VOICE</div>
            <h2><Mic size={15} /> 固定音色是加分项，不是开拍前提</h2>
            <p>H3 会按台词直接生成对白声音。只有当你需要「每个角色始终是同一个音色」时，才需要绑定音色参考。</p>
          </div>
        </div>
        <div className="voice-state">
          <b>本机声音能力：{voice?.voice_master_capable ? '可以生成角色音色参考' : '未配置（不影响出片）'}</b>
          {blockedVoice.length > 0 && <ul>{blockedVoice.map((row) => <li key={row.role}>{roleText(row)}</li>)}</ul>}
          <p>不绑定音色时，每个镜头的对白由 H3 原生生成；音色会在镜头之间自由变化，这是当前方案的已知取舍。</p>
          <p>需要固定音色：在专业模式「制片准备 → 音色」里设计并冻结音色，再把它作为音频参考绑定到镜头。音色参考同样受 2–15 秒时长限制。</p>
        </div>
        <button type="button" className="secondary-button" onClick={onOpenExpert}>去专业模式配置音色</button>
      </section>
    </div>}

    <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> DIRECTOR SETUP</span><span>所有配置保存在本机</span></footer>
  </div>;
}
