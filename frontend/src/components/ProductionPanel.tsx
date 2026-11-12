import { useState } from 'react';
import { Activity, AlertTriangle, ClipboardCheck, Play, Pause, ShieldCheck, Sparkles, Wrench } from 'lucide-react';
import type { Job, JobAttempt, QCReport, QualityGate, RepairSuggestion, SystemStatus } from '../api';
import {
  acceptanceLabels, advisoryReviewKindLabels, advisoryReviewLabels, checkStatusLabels, coverageLabels,
  failureLabels, gateLabels, gateStatusLabels, label, modeLabels, providerLabels, qcResultLabels,
  reconciliationLabels, repairActionLabels, repairOutcomeLabels, statusLabels,
} from '../constants';
import OperatorReview from './OperatorReview';

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
  onRecheck: (job: Job) => void;
  onAbandon: (jobId: string) => void;
};

const gateOrder = ['technical', 'semantic', 'audio', 'style', 'final'];

function isAdvisoryOnlyFinal(gate: QualityGate): boolean {
  const evidence = gate.evidence;
  const pending = evidence.advisory_pending_human_review;
  const unresolved = evidence.unresolved_gates;
  const unresolvedOptional = evidence.unresolved_optional_gates;
  return gate.gate_name === 'final'
    && Array.isArray(pending) && pending.length > 0
    && Array.isArray(unresolved) && unresolved.length === 0
    && Array.isArray(unresolvedOptional) && unresolvedOptional.length === 0;
}

export default function ProductionPanel({ jobs, system, busy, scenario, setScenario, onTogglePause, onLoadQC, onLoadGates, onSuggest, onRepair, onRecheck, onAbandon }: Props) {
  const [selected, setSelected] = useState('');
  const [qc, setQc] = useState<QCReport[]>([]);
  const [gates, setGates] = useState<QualityGate[]>([]);
  const [suggestion, setSuggestion] = useState<RepairSuggestion | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  /** 任务会持续累积：先按“需要我处理”筛选，再限制一屏渲染条数。 */
  const [jobFilter, setJobFilter] = useState<'all' | 'attention' | 'active' | 'done'>('all');
  const [jobLimit, setJobLimit] = useState(12);

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
  const currentAttempt = selectedJob?.attempts.reduce<JobAttempt | null>((latest, attempt) =>
    !latest || attempt.attempt_no > latest.attempt_no ? attempt : latest, null);
  const currentGates = gates.filter(gate => gate.attempt_id === currentAttempt?.id && gate.artifact_id === selectedJob?.artifact_id);
  const latestGates = gateOrder
    .map((name) => [...currentGates].reverse().find((gate) => gate.gate_name === name))
    .filter((gate): gate is QualityGate => Boolean(gate));
  const production = system?.production_mode ?? null;
  const realReady = production !== null && ['READY', 'RUNNING'].includes(production.mode);
  const attentionJobs = jobs.filter((job) => ['NEEDS_REVIEW', 'FAILED', 'BLOCKED'].includes(job.status));
  const activeJobs = jobs.filter((job) => ['SUBMITTING', 'RUNNING', 'QC_PENDING', 'PLANNED', 'READY', 'QUEUED'].includes(job.status));
  const doneJobs = jobs.filter((job) => ['PASSED', 'CANCELLED'].includes(job.status));
  const statusRank = (status: string) => (['NEEDS_REVIEW', 'FAILED', 'BLOCKED'].includes(status) ? 0
    : ['SUBMITTING', 'RUNNING', 'QC_PENDING'].includes(status) ? 1
      : ['PLANNED', 'READY', 'QUEUED'].includes(status) ? 2 : 3);
  const filteredJobs = (jobFilter === 'attention' ? attentionJobs
    : jobFilter === 'active' ? activeJobs
      : jobFilter === 'done' ? doneJobs
        : [...jobs].sort((left, right) => statusRank(left.status) - statusRank(right.status)));
  const shownJobs = filteredJobs.slice(0, jobLimit);
  return (
    <div className="card section-card monitor-card">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">⑥ 进度与结果</div>
          <h2>制作进度与结果</h2>
          <p>点开任务看进度、技术检查与审片建议；建议不阻断制作。</p>
        </div>
        <div className="worker-indicator"><span className="pulse" />{system?.worker_online ? '后台制作服务在线' : '后台制作服务离线'}</div>
      </div>
      <div className={`mock-warning ${realReady ? 'real' : ''}`}>
        {realReady ? <AlertTriangle size={13} /> : <Sparkles size={13} />}
        <span>
          {production
            ? `${production.label}：${production.detail}审片只提供建议；发布前的审美与版权由你确认。`
            : '暂无法确认制作模式。'}
        </span>
      </div>
      <div className="status-strip">
        <span>网页服务 {system?.api_online ? '在线' : '离线'} · {production?.label ?? '状态未知'} · MiniMax-H3</span>
        <span><b>{system?.pending_jobs ?? 0}</b> 排队中</span>
        <span><b>{system?.running_jobs ?? 0}</b> 生成中</span>
        <span><b>{system?.blocked_jobs ?? 0}</b> 等人处理</span>
        <span><b>{system?.failed_jobs ?? 0}</b> 失败</span>
        {system?.last_error && <span className="error"><AlertTriangle size={11} /> {system.last_error}</span>}
      </div>
      {!realReady && <div className="scenario-row">
        <label>下一项演示结果
          <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            <option value="success">正常完成</option><option value="delay">延迟完成</option>
            <option value="fail">模拟失败（可修复）</option><option value="uncertain">提交结果不明</option>
            <option value="duplicate">重复提交确认（不重跑）</option>
          </select>
        </label>
        {!system?.worker_online && <code>uv run --project backend python -m shine.worker</code>}
      </div>}
      <div className="filter-chips" role="group" aria-label="任务筛选">
        <button type="button" className={`filter-chip ${jobFilter === 'all' ? 'active' : ''}`}
          onClick={() => { setJobFilter('all'); setJobLimit(12); }}>全部 <em>{jobs.length}</em></button>
        <button type="button" className={`filter-chip ${jobFilter === 'attention' ? 'active' : ''}`}
          onClick={() => { setJobFilter('attention'); setJobLimit(12); }}>需要处理 <em>{attentionJobs.length}</em></button>
        <button type="button" className={`filter-chip ${jobFilter === 'active' ? 'active' : ''}`}
          onClick={() => { setJobFilter('active'); setJobLimit(12); }}>正在跑 <em>{activeJobs.length}</em></button>
        <button type="button" className={`filter-chip ${jobFilter === 'done' ? 'active' : ''}`}
          onClick={() => { setJobFilter('done'); setJobLimit(12); }}>已完成 <em>{doneJobs.length}</em></button>
      </div>
      <div className="job-list">
        {jobs.length === 0 ? <div className="empty-inline">还没有任务。在左侧分镜卡片上点「加入队列」。</div>
          : shownJobs.length === 0 ? <div className="empty-inline">这个筛选下没有任务，换一个分类看看。</div>
            : shownJobs.map((job) => (
          <div className={`job-row ${selected === job.id ? 'selected' : ''}`} key={job.id} onClick={() => openJob(job)}>
            <div className={`job-state ${job.status.toLowerCase()}`}><span /></div>
            <div className="job-details">
              <div>
                <b>{job.shot_name}</b>
                <span className={`job-mode ${job.provider === 'comfyui' ? 'real' : ''}`}>{label(providerLabels, job.provider)}</span>
                {job.generation_mode && <span className="job-mode">{label(modeLabels, job.generation_mode)}</span>}
                {job.request.self_review_scope === 'basic' && <span className="job-mode">基础自审</span>}
                {job.acceptance_status && <span className="job-mode">{label(acceptanceLabels, job.acceptance_status)}</span>}
                {job.stale && <span className="job-stale">内容已更新，待复核</span>}
              </div>
              <span>{label(statusLabels, job.status)}{job.failure_code ? ` · ${label(failureLabels, job.failure_code)}` : ''}</span>
              <small>第 {job.shot_revision} 版 · 尝试 {job.attempts.length} 次 · 返工 {job.semantic_retries}/{job.max_semantic_retries}{job.last_qc_result ? ` · 质检 ${label(qcResultLabels, job.last_qc_result)}` : ''}</small>
            </div>
            {['PLANNED', 'READY', 'QUEUED'].includes(job.status) && <button className="icon-button tiny" title={job.paused ? '继续' : '暂停'} onClick={(event) => { event.stopPropagation(); onTogglePause(job); }}>
              {job.paused ? <Play size={12} /> : <Pause size={12} />}
            </button>}
          </div>
        ))}
      </div>
      {filteredJobs.length > shownJobs.length && <button type="button" className="list-more"
        onClick={() => setJobLimit((limit) => limit + 20)}>
        显示更多任务（还有 {filteredJobs.length - shownJobs.length} 个）
      </button>}
      {selectedJob && <div className="job-detail">
        {selectedJob.provider === 'comfyui' && (selectedJob.status === 'NEEDS_REVIEW'
          || (selectedJob.status === 'PASSED' && selectedJob.acceptance_status !== 'accepted')) && <OperatorReview key={selectedJob.id} jobId={selectedJob.id} />}
        <div className="detail-head">
          <div><b>{selectedJob.shot_name}</b><span>编号 {selectedJob.id.slice(0, 12)}… · 状态版本 {selectedJob.state_version}{selectedJob.acceptance_note ? ` · ${selectedJob.acceptance_note}` : ''}</span></div>
          <div className="detail-actions">
            {selectedJob.provider === 'comfyui' && selectedJob.status === 'NEEDS_REVIEW' && selectedJob.failure_code === 'QC_NEEDS_REVIEW' && <button className="btn-secondary" disabled={busy || detailBusy} onClick={() => onRecheck(selectedJob)}>只重跑机器审片（保留视频）</button>}
            <button className="btn-secondary" disabled={busy || detailBusy} onClick={() => suggest(selectedJob)}><Wrench size={12} /> 给修复建议</button>
          </div>
        </div>
        <div className="detail-grid">
          <div>
            <span className="detail-label"><ClipboardCheck size={11} /> 尝试记录</span>
            {selectedJob.attempts.map((attempt) => (
              <div className="attempt-row" key={attempt.id}>
                <b>第 {attempt.attempt_no} 次</b><span>{label(statusLabels, attempt.status)}</span>
                <small>{attempt.failure_code ? label(failureLabels, attempt.failure_code) : label(reconciliationLabels, attempt.reconciliation_state)}</small>
                <small>进度 {attempt.progress}%</small>
              </div>
            ))}
            {selectedJob.repairs.length > 0 && <>
              <span className="detail-label"><Wrench size={11} /> 返工记录</span>
              {selectedJob.repairs.map((repair) => (
                <div className="attempt-row" key={repair.id}><b>{label(repairOutcomeLabels, repair.outcome)}</b><span>{label(repairActionLabels, repair.action)}</span><small>{label(failureLabels, repair.root_cause)}</small></div>
              ))}
            </>}
          </div>
          <div>
            <span className="detail-label"><ShieldCheck size={11} /> 五道质量关</span>
            {detailBusy && <div className="empty-inline">读取中…</div>}
            {!detailBusy && latestGates.length === 0 && <div className="empty-inline">还没有质检记录。</div>}
            {latestGates.map((gate) => (
              <div className="qc-row" key={gate.id}>
                <div>
                  <b>{label(gateLabels, gate.gate_name)}</b>
                  <span>{gate.evidence?.advisory === true
                    ? `建议项 · ${label(gateStatusLabels, gate.status)} · 不阻断制作`
                    : isAdvisoryOnlyFinal(gate)
                      ? '小模型建议待查看 · 不阻断制作'
                      : `${label(gateStatusLabels, gate.status)}${gate.gate_name === 'technical' && gate.required ? ' · 技术必查' : gate.required ? ' · 必查' : ' · 可选'}`}{gate.is_mock ? ' · 演示数据' : ''}</span>
                </div>
                <small>{gate.coverage ? `覆盖：${label(coverageLabels, gate.coverage)}` : ''}{gate.error_code ? ` · ${label(failureLabels, gate.error_code)}` : ''}{gate.model ? ` · 用的模型：${gate.model}` : ''}</small>
              </div>
            ))}
            {selectedJob.quality_verification && <small>证据来源：{selectedJob.quality_verification === 'real' ? '真实生成' : selectedJob.quality_verification === 'mock' ? '演示数据' : selectedJob.quality_verification} · 小模型建议和待人工确认状态会单独留档。</small>}
            {selectedJob.request.self_review_scope === 'basic' && <small>本次只自动查了分辨率、人物和对白三项，不等于人工验收。</small>}
            {selectedJob.advisory_reviews && selectedJob.advisory_reviews.length > 0 && <>
              <span className="detail-label"><Sparkles size={11} /> 建议式审片（独立队列，不占出片时间）</span>
              {selectedJob.advisory_reviews.map((review) => (
                <div className="qc-row" key={review.id}>
                  <div>
                    <b>{label(advisoryReviewKindLabels, review.kind ?? '')}</b>
                    <span>{label(advisoryReviewLabels, review.status)}</span>
                  </div>
                  <small>{review.model_id ?? '未知模型'}{review.artifact_sha256 ? ` · 视频 ${review.artifact_sha256.slice(0, 12)}…` : ''}{review.error_code ? ` · ${label(failureLabels, review.error_code)}` : ''}</small>
                </div>
              ))}
            </>}
            <span className="detail-label"><Activity size={11} /> 质检明细</span>
            {detailBusy && <div className="empty-inline">读取中…</div>}
            {!detailBusy && qc.length === 0 && <div className="empty-inline">还没有质检报告。</div>}
            {qc.map((report) => (
              <div className="qc-row" key={report.id}>
                <div><b>{label(qcResultLabels, report.result)}</b><span>{report.reviewer} · {report.is_mock ? '演示数据' : '真实检测'}</span></div>
                {report.report.technical && <div className="check-grid">
                  {report.report.technical.checks.map((check) => <span className={`check ${check.status}`} key={check.name}>{check.name}：{label(checkStatusLabels, check.status)}</span>)}
                </div>}
                {report.report.hard_failures?.map((failure) => <small className="failure" key={failure.code}>{label(failureLabels, failure.code)} · {failure.evidence}</small>)}
                {report.report.coverage && <small>覆盖：{Object.entries(report.report.coverage).map(([key, value]) => `${key}=${label(coverageLabels, value)}`).join(' · ')}</small>}
              </div>
            ))}
          </div>
        </div>
        {suggestion && <div className="repair-card">
          <div><b>问题归类：{label(failureLabels, suggestion.error_category)}</b><span>建议动作：{label(repairActionLabels, String(suggestion.plan.action))}{suggestion.plan.root_cause ? ` · 原因：${label(failureLabels, String(suggestion.plan.root_cause))}` : ''}</span></div>
          <div className="repair-actions">
            <small>{suggestion.auto_apply ? '可以自动重试' : '需要人工确认后再动'}</small>
            {!suggestion.auto_apply && String(suggestion.plan.action) !== 'wait_for_human' && <button className="btn-secondary" onClick={() => onRepair(selectedJob.id, suggestion.plan)}>按建议重试</button>}
          </div>
        </div>}
        {selectedJob.status === 'NEEDS_REVIEW' && <div className="repair-card">
          <div>
            <b>这次生成的外部结果无法自行核实</b>
            <span>系统不会自动重投。放弃只会结束这次任务，已经生成的产物仍按原样保留在作品里。</span>
          </div>
          <div className="repair-actions">
            <button className="btn-secondary" disabled={busy} onClick={() => onAbandon(selectedJob.id)}>放弃这次生成（保留产物，不重投）</button>
          </div>
        </div>}
      </div>}
    </div>
  );
}
