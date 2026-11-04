import { useState } from 'react';
import { Activity, Check, Cpu, Plus, Server, ShieldCheck, Trash2, Wifi, X } from 'lucide-react';
import type { AgentRoute, Project, ProviderProfile, SystemHealth, SystemStatus, WorkflowProfile } from '../api';
import { DIALOGUE_LANGUAGES, label, statusLabels, workerPhaseLabels } from '../constants';
import { LLMSetupPanel } from './LLMSetupPanel';
import StorageSettings from './StorageSettings';
import WorkflowProfileImport from './WorkflowProfileImport';

type Props = {
  project?: Project;
  onRefresh: () => void | Promise<void>;
  profiles: ProviderProfile[];
  routes: AgentRoute[];
  workflowProfiles: WorkflowProfile[];
  system: SystemStatus | null;
  busy: boolean;
  onProjectSettings: (patch: Record<string, unknown>) => void;
  onCreateProfile: (payload: Record<string, unknown>) => void;
  onTestProfile: (profileId: string) => void;
  onSetKey: (profileId: string, key: string) => void;
  onDeleteProfile: (profileId: string) => void;
  onRoute: (role: string, profileId: string) => void;
  onTestComfy: (baseUrl: string) => Promise<SystemHealth>;
  onRegisterWorkflow: (payload: Record<string, unknown>) => void;
  onValidateWorkflow: (profileId: string) => void;
  onDeleteWorkflow: (profileId: string) => void;
  /** Map the H3 node's own width/height inputs, when the registered graph has them. */
  onBindStandardParameters?: (profileId: string) => void;
};

const defaultGraph = '{}';
const defaultMap = '{}';

export default function SettingsPanel({
  project, onRefresh, workflowProfiles, system, busy, onProjectSettings, onTestComfy, onRegisterWorkflow, onValidateWorkflow, onDeleteWorkflow,
  onBindStandardParameters,
}: Props) {
  const [comfyUrl, setComfyUrl] = useState('http://127.0.0.1:8188');
  const [comfyResult, setComfyResult] = useState<SystemHealth | null>(null);
  const [workflowName, setWorkflowName] = useState('');
  const [workflowMode, setWorkflowMode] = useState('T2VA');
  const [graphText, setGraphText] = useState(defaultGraph);
  const [mapText, setMapText] = useState(defaultMap);
  const [transferText, setTransferText] = useState('{}');
  const [requiredNodes, setRequiredNodes] = useState('');
  const [advanced, setAdvanced] = useState(false);

  function registerWorkflow() {
    try {
      const graph = JSON.parse(graphText);
      const parameterMap = JSON.parse(mapText);
      const mediaTransfers = JSON.parse(transferText);
      onRegisterWorkflow({
        name: workflowName, mode: workflowMode, graph, parameter_map: parameterMap,
        media_transfers: mediaTransfers,
        required_nodes: requiredNodes.split(',').map((value) => value.trim()).filter(Boolean),
        project_id: project?.id ?? null,
      });
    } catch {
      window.alert('工作流、参数映射或媒体传输配置不是合法 JSON。');
    }
  }

  return (
    <section className="card section-card" id="settings">
      <div className="section-heading">
        <div>
          <div className="section-kicker">05 / SYSTEM SETTINGS</div>
          <h2>模型、ComfyUI 与本机状态</h2>
          <p>密钥只存本机安全存储，页面上永远不会显示；管理接口只监听本机（127.0.0.1）。</p>
        </div>
        <div className="section-icon"><Server size={17} /></div>
      </div>

      <div className="settings-grid">
        <StorageSettings />
        <div className="settings-block">
          <h3><ShieldCheck size={14} /> 项目策略</h3>
          {project ? <>
            <label className="switch-row">
              <span><b>允许远端语义审片</b><small>开启后才允许把派生证据帧发送到用户配置的 VLM。</small></span>
              <input type="checkbox" checked={project.allow_remote_review} onChange={(event) => onProjectSettings({ allow_remote_review: event.target.checked })} />
            </label>
            <label className="switch-row">
              <span><b>允许有界自动返工</b><small>只对临时/提示词/工作流类问题自动重试，硬错误仍转人工。</small></span>
              <input type="checkbox" checked={project.auto_repair_enabled} onChange={(event) => onProjectSettings({ auto_repair_enabled: event.target.checked })} />
            </label>
            <label className="field-line">叙事语言
              <select
                value={DIALOGUE_LANGUAGES.some(item => item.id === project.language) ? project.language : 'Chinese'}
                onChange={(event) => onProjectSettings({ language: event.target.value })}
              >
                {DIALOGUE_LANGUAGES.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
          </> : <div className="empty-inline">创建项目后可配置。</div>}
        </div>

        <LLMSetupPanel onChanged={onRefresh} />

        <div className="settings-block" id="comfy-config">
          <h3><Wifi size={14} /> ComfyUI 生成引擎连接</h3>
          <div className="inline-form">
            <input value={comfyUrl} onChange={(event) => setComfyUrl(event.target.value)} />
            <button className="secondary-button" disabled={busy} onClick={async () => setComfyResult(await onTestComfy(comfyUrl))}>测试连接</button>
          </div>
          {comfyResult && <div className={`comfy-result ${comfyResult.ok ? 'ok' : 'bad'}`}>
            {comfyResult.ok ? <Check size={13} /> : <X size={13} />}
            <span>{comfyResult.message}{comfyResult.node_count ? ` · ${comfyResult.node_count} 节点` : ''}</span>
          </div>}
          <div className="guardrail-note"><Cpu size={13} /><span>仅本机 MiniMax-H3 Ref2VA；节点验证、真实提交和人工电影质量审批分别记录。</span></div>
        </div>

        <div className="settings-block">
          <h3><Activity size={14} /> 后台执行器状态</h3>
          {system?.workers.length ? system.workers.map((worker) => (
            <div className="worker-row" key={worker.worker_id}>
              <span className={`status-led ${worker.online ? 'mock' : 'offline'}`} />
              <div><b>{worker.worker_id}</b><span>{label(statusLabels, worker.status)} · {label(workerPhaseLabels, worker.current_phase ?? 'idle')} · 已处理 {worker.processed_count} 个</span></div>
              <small>{worker.online ? '在线' : '离线'}</small>
            </div>
          )) : <div className="empty-inline">尚无 Worker 心跳。启动独立 Worker 后会在此显示。</div>}
          {system?.last_error && <div className="error-line">{system.last_error}</div>}
        </div>
      </div>

      <div className="advanced-toggle">
        <button className="text-button" onClick={() => setAdvanced((value) => !value)}>{advanced ? '收起' : '展开'} 工作流 Profile 注册</button>
      </div>
      {advanced && <div className="workflow-editor">
        <WorkflowProfileImport onLoad={(candidate) => {
          if (typeof candidate.name === 'string') setWorkflowName(candidate.name);
          if (typeof candidate.mode === 'string' && ['T2VA', 'I2VA', 'FL2VA', 'L2VA', 'Ref2VA'].includes(candidate.mode)) {
            setWorkflowMode(candidate.mode);
          }
          setGraphText(JSON.stringify(candidate.graph, null, 2));
          setMapText(JSON.stringify(candidate.parameter_map, null, 2));
          setTransferText(JSON.stringify(candidate.media_transfers ?? {}, null, 2));
          setRequiredNodes(Array.isArray(candidate.required_nodes) ? candidate.required_nodes.join(', ') : '');
        }} />
        <div className="provider-form">
          <input value={workflowName} placeholder="Profile 名称" onChange={(event) => setWorkflowName(event.target.value)} />
          <select value={workflowMode} onChange={(event) => setWorkflowMode(event.target.value)}>
            <option value="T2VA">T2VA</option><option value="I2VA">I2VA</option><option value="FL2VA">FL2VA</option>
            <option value="L2VA">L2VA</option><option value="Ref2VA">Ref2VA</option>
          </select>
          <input value={requiredNodes} placeholder="必需节点，逗号分隔" onChange={(event) => setRequiredNodes(event.target.value)} />
        </div>
        <div className="json-grid">
          <label>ComfyUI API Workflow JSON<textarea value={graphText} rows={10} onChange={(event) => setGraphText(event.target.value)} /></label>
          <label>参数映射（逻辑名 → node.inputs.field）<textarea value={mapText} rows={10} onChange={(event) => setMapText(event.target.value)} /></label>
          <label>媒体传输配置 JSON（音频或视频引用需要明确配置）<textarea value={transferText} rows={5} onChange={(event) => setTransferText(event.target.value)} /></label>
        </div>
        <button className="primary-button" disabled={busy} onClick={registerWorkflow}><Plus size={13} /> 注册工作流</button>
        <div className="workflow-list">
          {workflowProfiles.map((profile) => {
            const sizeMapped = 'width' in (profile.parameter_map ?? {}) && 'height' in (profile.parameter_map ?? {});
            return (
            <div className="workflow-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{profile.mode} · SHA {profile.graph_sha256.slice(0, 10)}… · {profile.required_nodes.join(', ')}</span></div>
              <details><summary>模型、容量、连续性与验证来源</summary>
                <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ identity: profile.model_identity, parameter_map: profile.parameter_map, validation: profile.validated_against }, null, 2)}</pre>
                <p>提示：引用槽位超容量或缺映射时，任务会被拦下并提示「参考素材还没接入工作流」；动态槽位关闭；视频传输未验收；硬接续待测试。</p>
              </details>
              {!sizeMapped && onBindStandardParameters
                && <button className="small-action" disabled={busy} title="把这个 H3 节点自带的 width/height 输入接入界面画幅设置"
                  onClick={() => onBindStandardParameters(profile.id)}>补齐宽高参数</button>}
              {sizeMapped && <span className="workflow-size-ok">宽高可调</span>}
              <button className="small-action" disabled={busy} onClick={() => onValidateWorkflow(profile.id)}>验证本机节点</button>
              <button className="icon-button tiny" onClick={() => onDeleteWorkflow(profile.id)}><Trash2 size={12} /></button>
            </div>
            );
          })}
        </div>
      </div>}
    </section>
  );
}
