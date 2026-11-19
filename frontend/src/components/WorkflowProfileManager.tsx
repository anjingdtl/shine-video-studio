import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type { Project, WorkflowProfile } from '../api';
import WorkflowProfileImport from './WorkflowProfileImport';

type Props = {
  project?: Project;
  workflowProfiles: WorkflowProfile[];
  busy: boolean;
  onRegisterWorkflow: (payload: Record<string, unknown>) => void;
  onValidateWorkflow: (profileId: string) => void;
  onDeleteWorkflow: (profileId: string) => void;
  onBindStandardParameters?: (profileId: string) => void;
};

const defaultGraph = '{}';
const defaultMap = '{}';

/** 自定义工作流映射细调：标准方案由基础配置一键整备自动登记，
 *  宽方案/自定义图在这里登记并在真实渲染器上验证。唯一写入口仍是 /workflow-profiles。 */
export default function WorkflowProfileManager({
  project, workflowProfiles, busy, onRegisterWorkflow, onValidateWorkflow, onDeleteWorkflow,
  onBindStandardParameters,
}: Props) {
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

  return <>
    <div className="advanced-toggle">
      <button className="btn-text" onClick={() => setAdvanced((value) => !value)}>{advanced ? '收起' : '展开'} 工作流 Profile 注册</button>
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
      <button className="btn-secondary" disabled={busy} onClick={registerWorkflow}><Plus size={13} /> 注册工作流</button>
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
              && <button className="btn-secondary" disabled={busy} title="把这个 H3 节点自带的 width/height 输入接入界面画幅设置"
                onClick={() => onBindStandardParameters(profile.id)}>补齐宽高参数</button>}
            {sizeMapped && <span className="workflow-size-ok">宽高可调</span>}
            <button className="btn-secondary" disabled={busy} onClick={() => onValidateWorkflow(profile.id)}>验证本机节点</button>
            <button className="icon-button tiny" onClick={() => onDeleteWorkflow(profile.id)}><Trash2 size={12} /></button>
          </div>
          );
        })}
      </div>
    </div>}
  </>;
}
