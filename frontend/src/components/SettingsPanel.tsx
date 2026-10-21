import { useState } from 'react';
import { Activity, Check, Cpu, KeyRound, Plus, Server, ShieldCheck, Trash2, Wifi, X } from 'lucide-react';
import type { AgentRoute, Project, ProviderProfile, SystemHealth, SystemStatus, WorkflowProfile } from '../api';

type Props = {
  project?: Project;
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
  onDeleteWorkflow: (profileId: string) => void;
};

const defaultGraph = `{
  "1": {"class_type": "H3BaseSampler", "inputs": {"positive_prompt": "", "seed": 0, "duration": 6.0}},
  "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}}
}`;
const defaultMap = `{"positive_prompt": "1.inputs.positive_prompt", "seed": "1.inputs.seed", "duration": "1.inputs.duration"}`;

export default function SettingsPanel({
  project, profiles, routes, workflowProfiles, system, busy, onProjectSettings, onCreateProfile,
  onTestProfile, onSetKey, onDeleteProfile, onRoute, onTestComfy, onRegisterWorkflow, onDeleteWorkflow,
}: Props) {
  const [name, setName] = useState('Local LLM');
  const [kind, setKind] = useState('openai_compatible');
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:11434/v1');
  const [model, setModel] = useState('qwen2.5:7b');
  const [apiKey, setApiKey] = useState('');
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [comfyUrl, setComfyUrl] = useState('http://127.0.0.1:8188');
  const [comfyResult, setComfyResult] = useState<SystemHealth | null>(null);
  const [workflowName, setWorkflowName] = useState('local-h3-t2va');
  const [workflowMode, setWorkflowMode] = useState('T2VA');
  const [graphText, setGraphText] = useState(defaultGraph);
  const [mapText, setMapText] = useState(defaultMap);
  const [requiredNodes, setRequiredNodes] = useState('H3BaseSampler');
  const [advanced, setAdvanced] = useState(false);

  function createProfile() {
    onCreateProfile({ name, kind, base_url: baseUrl, model, api_key: apiKey || null, timeout_s: 60, max_retries: 2 });
    setApiKey('');
  }

  function registerWorkflow() {
    try {
      const graph = JSON.parse(graphText);
      const parameterMap = JSON.parse(mapText);
      onRegisterWorkflow({
        name: workflowName, mode: workflowMode, graph, parameter_map: parameterMap,
        required_nodes: requiredNodes.split(',').map((value) => value.trim()).filter(Boolean),
        project_id: project?.id ?? null,
      });
    } catch {
      window.alert('工作流 JSON 或参数映射不是合法 JSON。');
    }
  }

  return (
    <section className="card section-card" id="settings">
      <div className="section-heading">
        <div>
          <div className="section-kicker">05 / SYSTEM SETTINGS</div>
          <h2>模型、ComfyUI 与本机状态</h2>
          <p>密钥只写入本机安全存储，不回传前端；未验收的管理接口保持 loopback。</p>
        </div>
        <div className="section-icon"><Server size={17} /></div>
      </div>

      <div className="settings-grid">
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
            <label className="field-line">叙事语言<input value={project.language} onChange={(event) => onProjectSettings({ language: event.target.value })} /></label>
          </> : <div className="empty-inline">创建项目后可配置。</div>}
        </div>

        <div className="settings-block">
          <h3><KeyRound size={14} /> LLM Provider</h3>
          <div className="provider-form">
            <input value={name} placeholder="配置名称" onChange={(event) => setName(event.target.value)} />
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="openai_compatible">OpenAI-compatible</option>
              <option value="anthropic">Anthropic 原生</option>
              <option value="ollama">Ollama</option>
              <option value="mock">Mock</option>
            </select>
            <input value={baseUrl} placeholder="Base URL" onChange={(event) => setBaseUrl(event.target.value)} />
            <input value={model} placeholder="Model ID" onChange={(event) => setModel(event.target.value)} />
            <input value={apiKey} type="password" placeholder="API Key（不会回显）" onChange={(event) => setApiKey(event.target.value)} />
            <button className="primary-button" disabled={busy} onClick={createProfile}><Plus size={13} /> 添加</button>
          </div>
          {profiles.map((profile) => (
            <div className="profile-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{profile.kind} · {profile.model || '未设置模型'} · {profile.has_api_key ? '已存密钥' : '无密钥'}</span></div>
              <div className="profile-actions">
                <input
                  value={keyDrafts[profile.id] ?? ''}
                  type="password"
                  placeholder="更新密钥"
                  onChange={(event) => setKeyDrafts((rows) => ({ ...rows, [profile.id]: event.target.value }))}
                />
                <button className="text-button" disabled={!keyDrafts[profile.id]} onClick={() => { onSetKey(profile.id, keyDrafts[profile.id]); setKeyDrafts((rows) => ({ ...rows, [profile.id]: '' })); }}>保存密钥</button>
                <button className="text-button" onClick={() => onTestProfile(profile.id)}>测试</button>
                <button className="text-button" onClick={() => onRoute('shot_planner', profile.id)}>镜头规划</button>
                <button className="text-button" onClick={() => onRoute('script_planner', profile.id)}>剧本规划</button>
                <button className="icon-button tiny" onClick={() => onDeleteProfile(profile.id)}><Trash2 size={12} /></button>
              </div>
            </div>
          ))}
          <div className="route-row">
            {routes.length === 0 ? <span className="empty-inline">未配置角色路由；Agent 将使用本地确定性规划器。</span> : routes.map((route) => (
              <span className="route-chip" key={route.role}>{route.role} → {route.profile_name}</span>
            ))}
          </div>
        </div>

        <div className="settings-block">
          <h3><Wifi size={14} /> ComfyUI 连接</h3>
          <div className="inline-form">
            <input value={comfyUrl} onChange={(event) => setComfyUrl(event.target.value)} />
            <button className="secondary-button" disabled={busy} onClick={async () => setComfyResult(await onTestComfy(comfyUrl))}>测试连接</button>
          </div>
          {comfyResult && <div className={`comfy-result ${comfyResult.ok ? 'ok' : 'bad'}`}>
            {comfyResult.ok ? <Check size={13} /> : <X size={13} />}
            <span>{comfyResult.message}{comfyResult.node_count ? ` · ${comfyResult.node_count} 节点` : ''}</span>
          </div>}
          <div className="guardrail-note"><Cpu size={13} /><span>真实 MiniMax-H3 必须通过本机 ComfyUI；当前未进行 GPU 真机验收。</span></div>
        </div>

        <div className="settings-block">
          <h3><Activity size={14} /> Worker 实例</h3>
          {system?.workers.length ? system.workers.map((worker) => (
            <div className="worker-row" key={worker.worker_id}>
              <span className={`status-led ${worker.online ? 'mock' : 'offline'}`} />
              <div><b>{worker.worker_id}</b><span>{worker.status} · {worker.current_phase ?? 'idle'} · 处理 {worker.processed_count}</span></div>
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
        </div>
        <button className="primary-button" disabled={busy} onClick={registerWorkflow}><Plus size={13} /> 注册工作流</button>
        <div className="workflow-list">
          {workflowProfiles.map((profile) => (
            <div className="workflow-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{profile.mode} · SHA {profile.graph_sha256.slice(0, 10)}… · {profile.required_nodes.join(', ')}</span></div>
              <button className="icon-button tiny" onClick={() => onDeleteWorkflow(profile.id)}><Trash2 size={12} /></button>
            </div>
          ))}
        </div>
      </div>}
    </section>
  );
}
