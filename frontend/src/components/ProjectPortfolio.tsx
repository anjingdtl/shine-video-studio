import { useEffect, useState } from 'react';
import { Check, Clapperboard, Plus } from 'lucide-react';
import {
  api, type Asset, type Journey, type Project, type Shot, type StyleVersion,
} from '../api';

type ProjectRow = {
  id: string;
  title: string;
  approvedAssets: number;
  totalAssets: number;
  styleVersion: number | null;
  shots: number;
  outputs: number;
  nextStage: string | null;
  state: 'loading' | 'ready' | 'error';
};

const STAGE_LABELS: Record<string, string> = {
  script: '创意与剧本',
  materials: '统筹与素材',
  storyboard: '镜头拆解',
  package: '导演包',
  production: '视频生产',
  post: '后期成片',
};

function valueOf<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === 'fulfilled' ? result.value : fallback;
}

function emptyRow(project: Project): ProjectRow {
  return {
    id: project.id, title: project.title,
    approvedAssets: 0, totalAssets: 0, styleVersion: null, shots: 0, outputs: 0,
    nextStage: null, state: 'loading',
  };
}

type Props = {
  projects: Project[];
  currentProjectId: string;
  productionLabel: string;
  productionDetail?: string;
  projectTitle: string;
  setProjectTitle: (value: string) => void;
  busy: boolean;
  refreshEpoch: number;
  onOpen: (projectId: string) => void;
  onCreate: () => void;
};

/**
 * 项目管理：每个作品一行，素材、风格、分镜、制作方式、产出物和下一步都在同一行。
 * 这里只读各作品现有的真实接口，不额外推断任何完成态。
 */
export function ProjectPortfolio({
  projects, currentProjectId, productionLabel, productionDetail,
  projectTitle, setProjectTitle, busy, refreshEpoch, onOpen, onCreate,
}: Props) {
  const [rows, setRows] = useState<ProjectRow[]>(() => projects.map(emptyRow));

  useEffect(() => {
    let alive = true;
    // 刷新时保留上一次读到的值，只给新出现的作品显示“读取中”，避免整表闪烁。
    setRows((current) => projects.map((project) => current.find((row) => row.id === project.id) ?? emptyRow(project)));
    void Promise.all(projects.map(async (project): Promise<ProjectRow> => {
      const [assetResult, styleResult, shotResult, journeyResult, deliveryResult] = await Promise.allSettled([
        api<Asset[]>(`/projects/${project.id}/assets`),
        api<StyleVersion[]>(`/projects/${project.id}/styles`),
        api<Shot[]>(`/projects/${project.id}/shots`),
        api<Journey>(`/projects/${project.id}/journey`),
        api<unknown[]>(`/projects/${project.id}/deliveries`),
      ]);
      const assets = valueOf(assetResult, [] as Asset[]);
      const styles = valueOf(styleResult, [] as StyleVersion[]);
      const shots = valueOf(shotResult, [] as Shot[]);
      const journey = valueOf(journeyResult, null as Journey | null);
      const deliveries = valueOf(deliveryResult, [] as unknown[]);
      const currentStyle = styles.find((style) => style.id === project.frozen_style_version_id)
        ?? styles.at(-1) ?? null;
      const failed = [assetResult, styleResult, shotResult, journeyResult]
        .some((result) => result.status === 'rejected');
      return {
        id: project.id,
        title: project.title,
        approvedAssets: assets.filter((asset) => asset.approved && asset.locked).length,
        totalAssets: assets.length,
        styleVersion: currentStyle?.version ?? null,
        shots: shots.length,
        outputs: deliveries.length,
        nextStage: journey?.next_stage ?? null,
        state: failed ? 'error' : 'ready',
      };
    })).then((loaded) => {
      if (alive) setRows(loaded);
    }).catch(() => {
      if (alive) setRows((current) => current.map((row) => ({ ...row, state: 'error' })));
    });
    return () => { alive = false; };
  }, [projects, refreshEpoch]);

  return <section className="card portfolio" aria-label="项目管理">
    <header className="portfolio-head">
      <div>
        <div className="section-kicker">PROJECT PORTFOLIO</div>
        <h2>全部作品</h2>
        <p>每个作品一行：素材、风格、分镜、制作方式、产出物和下一步。换作品不用先切换再找信息。</p>
      </div>
      <span className="portfolio-count">{projects.length} 个作品</span>
    </header>

    <div className="portfolio-create">
      <div className="portfolio-create-copy">
        <b>新建作品</b>
        <span>起个名字就能开始；素材、风格、镜头和成片都会留在本机。</span>
      </div>
      <div className="portfolio-create-actions">
        <input className="new-project-inline" value={projectTitle}
          onChange={(event) => setProjectTitle(event.target.value)}
          placeholder="新作品名称"
          onKeyDown={(event) => event.key === 'Enter' && onCreate()}
          aria-label="新作品名称" />
        <button className="new-project-button" disabled={busy || !projectTitle.trim()} onClick={onCreate}>
          <Plus size={15} aria-hidden="true" />新建作品
        </button>
      </div>
    </div>

    <div className="portfolio-table-wrap">
      <table className="portfolio-table">
        <thead>
          <tr>
            <th scope="col">作品</th>
            <th scope="col">素材</th>
            <th scope="col">风格</th>
            <th scope="col">分镜</th>
            <th scope="col">制作方式</th>
            <th scope="col">产出物</th>
            <th scope="col">下一步</th>
            <th scope="col"><span className="visually-hidden">操作</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const current = row.id === currentProjectId;
            return <tr key={row.id} className={current ? 'current' : ''}>
              <th scope="row" className="portfolio-work">
                <span className="portfolio-icon"><Clapperboard size={14} /></span>
                <span className="portfolio-work-copy">
                  <b>{row.title}</b>
                  {current && <em>当前作品</em>}
                </span>
              </th>
              <td>
                <span className={`portfolio-metric ${row.totalAssets ? '' : 'muted'}`}>
                  {row.state === 'loading' ? '读取中…'
                    : <>{row.approvedAssets}<small> / {row.totalAssets} 项</small></>}
                </span>
              </td>
              <td>
                {row.state === 'loading' ? <span className="portfolio-metric muted">读取中…</span>
                  : row.styleVersion
                    ? <span className="portfolio-badge ok"><Check size={10} />第 {row.styleVersion} 版</span>
                    : <span className="portfolio-badge pending">待冻结</span>}
              </td>
              <td>
                <span className={`portfolio-metric ${row.shots ? '' : 'muted'}`}>
                  {row.state === 'loading' ? '读取中…' : <>{row.shots}<small> 镜</small></>}
                </span>
              </td>
              <td>
                <span className="portfolio-metric" title={productionDetail}>{productionLabel}</span>
              </td>
              <td>
                <span className={`portfolio-metric ${row.outputs ? '' : 'muted'}`}>
                  {row.state === 'loading' ? '读取中…'
                    : <>{row.outputs}<small> 个成片</small></>}
                </span>
              </td>
              <td>
                {row.state === 'loading' ? <span className="portfolio-metric muted">读取中…</span>
                  : row.state === 'error' ? <span className="portfolio-badge error">读取失败</span>
                    : row.nextStage
                      ? <span className="portfolio-next">{STAGE_LABELS[row.nextStage] ?? row.nextStage}</span>
                      : <span className="portfolio-badge ok"><Check size={10} />全部完成</span>}
              </td>
              <td className="portfolio-action">
                <button type="button" className={`portfolio-open ${current ? 'current' : ''}`}
                  onClick={() => onOpen(row.id)}
                  title={current ? `继续处理「${row.title}」` : `切换到「${row.title}」并继续`}>
                  {current ? '继续处理' : '打开作品'}
                </button>
              </td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
  </section>;
}

export default ProjectPortfolio;
