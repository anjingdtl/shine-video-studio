import { useState } from 'react';
import { Activity, AlertTriangle, ClipboardCheck, Play, Pause, ShieldCheck, Sparkles, Wrench } from 'lucide-react';
import type { Job, QCReport, QualityGate, RepairSuggestion, SystemStatus } from '../api';
import { statusLabels } from '../constants';

type Props = {
  jobs: Job[];
  system: SystemStatus | null;
  busy: boolean;
  scenario: string;
  setScenario: (value: string) => void;
  onTogglePause: (job: Job) => void;
  onLoadQC: (jobId: string) => Promise<QCReport[]>;
  onLoadGates: (jobId: string) => Promise<QualityGate[]>;
  onSuggest: (jobId: string) => Promise<RepairSuggestion>;
  onRepair: (jobId: string, plan: Record<string, unknown>) => void;
};

const gateOrder = ['technical', 'semantic', 'audio', 'style', 'final'];

export default function ProductionPanel({ jobs, system, busy, scenario, setScenario, onTogglePause, onLoadQC, onLoadGates, onSuggest, onRepair }: Props) {
  const [selected, setSelected] = useState('');
  const [qc, setQc] = useState<QCReport[]>([]);
  const [gates, setGates] = useState<QualityGate[]>([]);
  const [suggestion, setSuggestion] = useState<RepairSuggestion | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  async function openJob(job: Job) {
    setSelected(job.id);
    setSuggestion(null);
    setDetailBusy(true);
    try {
      const [reports, ledger] = await Promise.all([onLoadQC(job.id), onLoadGates(job.id)]);
      setQc(reports);
      setGates(ledger);
    } finally { setDetailBusy(false); }
  }

  async function suggest(job: Job) {
    setDetailBusy(true);
    try { setSuggestion(await onSuggest(job.id)); } finally { setDetailBusy(false); }
  }

  const selectedJob = jobs.find((job) => job.id === selected);
  const latestGates = gateOrder
    .map((name) => [...gates].reverse().find((gate) => gate.gate_name === name))
    .filter((gate): gate is QualityGate => Boolean(gate));
  return (
    <div className="card section-card monitor-card">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">04 / PRODUCTION MONITOR</div>
          <h2>任务、质检与返工</h2>
          <p>状态、租约、尝试、QC 证据与返工记录都保存在本机数据库。</p>
        </div>
        <div className="worker-indicator"><span className="pulse" />{system?.worker_online ? 'Worker 在线' : 'Worker 离线'}</div>
      </div>
      <div className={`mock-warning ${system?.render_mode === 'comfyui' ? 'real' : ''}`}>
        {system?.render_mode === 'comfyui' ? <AlertTriangle size={13} /> : <Sparkles size={13} />}
        <span>
          {system?.render_mode === 'comfyui'
            ? 'ComfyUI 模式 · 真实 H3 尚未连接或未验收，失败会停在人工复核，不会静默换模型。'
            : 'Mock 演示模式：只模拟调度、租约与 QC，不会生成或冒充视频。'}
        </span>
      </div>
      <div className="status-strip">
        <span><b>{system?.pending_jobs ?? 0}</b> 待处理</span>
        <span><b>{system?.running_jobs ?? 0}</b> 执行中</span>
        <span><b>{system?.blocked_jobs ?? 0}</b> 需复核</span>
        <span><b>{system?.failed_jobs ?? 0}</b> 失败</span>
        {system?.last_error && <span className="error"><AlertTriangle size={11} /> {system.last_error}</span>}
      </div>
      <div className="scenario-row">
        <label>下一项模拟结果
          <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            <option value="success">正常完成</option><option value="delay">延迟完成</option>
            <option value="fail">模拟失败（可修复）</option><option value="uncertain">提交结果不明</option>
            <option value="duplicate">幂等重复确认</option>
          </select>
        </label>
        <code>uv run --project backend python -m shine.worker</code>
      </div>
      <div className="job-list">
        {jobs.length === 0 ? <div className="empty-inline">暂无任务。通过分镜卡片加入队列。</div> : jobs.map((job) => (
          <div className={`job-row ${selected === job.id ? 'selected' : ''}`} key={job.id} onClick={() => openJob(job)}>
            <div className={`job-state ${job.status.toLowerCase()}`}><span /></div>
            <div className="job-details">
              <div>
                <b>{job.shot_name}</b>
                <span className={`job-mode ${job.provider === 'comfyui' ? 'real' : ''}`}>{job.provider.toUpperCase()}</span>
                {job.generation_mode && <span className="job-mode">{job.generation_mode}</span>}
                {job.stale && <span className="job-stale">需复核</span>}
              </div>
              <span>{statusLabels[job.status] ?? job.status}{job.failure_code ? ` · ${job.failure_code}` : ''}</span>
              <small>rev {job.shot_revision} · {job.attempts.length} 次尝试 · 返工 {job.semantic_retries}/{job.max_semantic_retries}{job.last_qc_result ? ` · QC ${job.last_qc_result}` : ''}</small>
            </div>
            {['PLANNED', 'READY', 'QUEUED'].includes(job.status) && <button className="icon-button tiny" title={job.paused ? '继续' : '暂停'} onClick={(event) => { event.stopPropagation(); onTogglePause(job); }}>
              {job.paused ? <Play size={12} /> : <Pause size={12} />}
            </button>}
          </div>
        ))}
      </div>
      {selectedJob && <div className="job-detail">
        <div className="detail-head">
          <div><b>{selectedJob.shot_name}</b><span>{selectedJob.id.slice(0, 12)}… · state v{selectedJob.state_version}</span></div>
          <div className="detail-actions">
            <button className="subtle-button" disabled={busy || detailBusy} onClick={() => suggest(selectedJob)}><Wrench size={12} /> 修复建议</button>
          </div>
        </div>
        <div className="detail-grid">
          <div>
            <span className="detail-label"><ClipboardCheck size={11} /> 尝试记录</span>
            {selectedJob.attempts.map((attempt) => (
              <div className="attempt-row" key={attempt.id}>
                <b>#{attempt.attempt_no}</b><span>{attempt.status}</span>
                <small>{attempt.failure_code ?? attempt.reconciliation_state ?? '—'}</small>
                <small>{attempt.progress}%</small>
              </div>
            ))}
            {selectedJob.repairs.length > 0 && <>
              <span className="detail-label"><Wrench size={11} /> 返工记录</span>
              {selectedJob.repairs.map((repair) => (
                <div className="attempt-row" key={repair.id}><b>{repair.outcome}</b><span>{repair.action}</span><small>{repair.root_cause}</small></div>
              ))}
            </>}
          </div>
          <div>
            <span className="detail-label"><ShieldCheck size={11} /> 质量门禁</span>
            {detailBusy && <div className="empty-inline">读取中…</div>}
            {!detailBusy && latestGates.length === 0 && <div className="empty-inline">尚无门禁记录。</div>}
            {latestGates.map((gate) => (
              <div className="qc-row" key={gate.id}>
                <div>
                  <b>{gate.gate_name}</b>
                  <span>{gate.status}{gate.required ? ' · 必需' : ' · 可选'}{gate.is_mock ? ' · MOCK' : ''}</span>
                </div>
                <small>覆盖：{gate.coverage}{gate.error_code ? ` · ${gate.error_code}` : ''}{gate.model ? ` · ${gate.model}` : ''}</small>
              </div>
            ))}
            {selectedJob.quality_verification && <small>验证级别：{selectedJob.quality_verification}</small>}
            <span className="detail-label"><Activity size={11} /> QC 证据</span>
            {detailBusy && <div className="empty-inline">读取中…</div>}
            {!detailBusy && qc.length === 0 && <div className="empty-inline">尚无 QC 报告。</div>}
            {qc.map((report) => (
              <div className="qc-row" key={report.id}>
                <div><b>{report.result}</b><span>{report.reviewer} · {report.is_mock ? 'MOCK' : 'REAL'}</span></div>
                {report.report.technical && <div className="check-grid">
                  {report.report.technical.checks.map((check) => <span className={`check ${check.status}`} key={check.name}>{check.name}: {check.status}</span>)}
                </div>}
                {report.report.hard_failures?.map((failure) => <small className="failure" key={failure.code}>{failure.code} · {failure.evidence}</small>)}
                {report.report.coverage && <small>覆盖：{Object.entries(report.report.coverage).map(([key, value]) => `${key}=${value}`).join(' · ')}</small>}
              </div>
            ))}
          </div>
        </div>
        {suggestion && <div className="repair-card">
          <div><b>根因：{suggestion.error_category}</b><span>{String(suggestion.plan.action)} · {String(suggestion.plan.root_cause ?? '')}</span></div>
          <div className="repair-actions">
            <small>{suggestion.auto_apply ? '可自动重试' : '需要人工确认'}</small>
            {!suggestion.auto_apply && String(suggestion.plan.action) !== 'wait_for_human' && <button className="secondary-button" onClick={() => onRepair(selectedJob.id, suggestion.plan)}>按建议重试</button>}
          </div>
        </div>}
      </div>}
    </div>
  );
}
