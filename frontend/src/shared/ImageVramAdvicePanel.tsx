import type { ImageVramAdvice } from '../api';

export const IMAGE_VRAM_EVIDENCE_LABELS: Record<string, string> = {
  measured_on_this_machine: '本机实测',
  measured_on_similar_hardware: '相近硬件实测',
  reported_by_users: '用户反馈（未实测）',
  estimated: '推算（尚未实测）',
};

function mbAsGib(mb: number): string {
  return `${(mb / 1024).toFixed(1)} GiB`;
}

type Props = {
  advice?: ImageVramAdvice | null;
  gpuName?: string | null;
  compact?: boolean;
};

/**
 * 生图显存适配建议（纯只读提示，永不拦截或禁用控件）。
 *
 * 读取后端 capability.image_vram_advice 返回的同一份事实：当前显存档位、
 * 16 GB 实测占用基线、当前空闲显存、证据等级与各档位对照表。
 */
export function ImageVramAdvicePanel({ advice, gpuName, compact = false }: Props) {
  if (!advice) return null;

  const evidenceLevel = advice.evidence?.level ?? null;
  const evidenceLabel = evidenceLevel ? (IMAGE_VRAM_EVIDENCE_LABELS[evidenceLevel] ?? evidenceLevel) : null;
  const classes = Array.isArray(advice.classes) ? advice.classes : [];
  const notes = Array.isArray(advice.notes) ? advice.notes : [];

  return (
    <div
      className={`image-vram-advice${compact ? ' is-compact' : ''}`}
      data-testid="image-vram-advice"
      data-status={advice.status}
      role="note"
      aria-label="生图显存适配建议"
    >
      <div className="image-vram-advice-head">
        <strong>生图显存适配建议</strong>
        <span className="image-vram-tag">仅供参考，不拦截出图</span>
        {advice.class && <span className="image-vram-tag">{advice.class.label}</span>}
        {evidenceLabel && <span className="image-vram-tag is-evidence">{evidenceLabel}</span>}
      </div>

      <div className="image-vram-advice-meta">
        {advice.gpu ? (
          <span>
            {gpuName ? `${gpuName} · ` : ''}
            总显存 {mbAsGib(advice.gpu.total_mb)}（{advice.gpu.total_mb} MB）
            {advice.gpu.free_mb != null
              ? ` · 当前空闲 ${mbAsGib(advice.gpu.free_mb)}（${advice.gpu.free_mb} MB）`
              : ''}
          </span>
        ) : (
          <span>显卡与显存：未识别到 NVIDIA 显卡</span>
        )}
        <span>
          参考占用：模型约 {advice.usage_mb} MB · 整卡峰值约 {advice.peak_total_mb} MB（{advice.measured_on}）
          · 启动需空闲 ≥ {advice.start_free_mb} MB
        </span>
      </div>

      <p className="image-vram-advice-headline">
        <strong>{advice.headline}</strong>
      </p>

      {advice.evidence?.detail && (
        <p className="image-vram-advice-detail">{advice.evidence.detail}</p>
      )}

      {notes.length > 0 && (
        <ul className="image-vram-advice-notes">
          {notes.map((note, idx) => (
            <li key={`${idx}-${note}`}>{note}</li>
          ))}
        </ul>
      )}

      {classes.length > 0 && (
        <table className="image-vram-classes" aria-label="各显存档位建议">
          <thead>
            <tr>
              <th>显存档位</th>
              <th>证据等级</th>
              <th>适配建议</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((row) => {
              const isCurrent = advice.class?.id === row.id;
              const rowEvidence = row.evidence
                ? (IMAGE_VRAM_EVIDENCE_LABELS[row.evidence] ?? row.evidence)
                : '启动门槛';
              return (
                <tr
                  key={row.id}
                  className={isCurrent ? 'is-current' : undefined}
                  data-current={isCurrent ? 'true' : undefined}
                  aria-current={isCurrent ? 'true' : undefined}
                >
                  <td>
                    <span>{row.label}</span>
                    {isCurrent && <span className="image-vram-current-tag">当前本机</span>}
                  </td>
                  <td>{rowEvidence}</td>
                  <td>{row.advice}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
