import { Check, History, LockKeyhole, LockOpen } from 'lucide-react';
import type { Style, StyleVersion } from '../api';
import { styleFields } from '../constants';

type Props = {
  style: Style;
  setStyle: (updater: (value: Style) => Style) => void;
  currentStyle?: StyleVersion;
  styles: StyleVersion[];
  busy: boolean;
  onSave: () => void;
  onFreeze: () => void;
};

export default function StylePanel({ style, setStyle, currentStyle, styles, busy, onSave, onFreeze }: Props) {
  return (
    <section className="card section-card" id="style">
      <div className="section-heading">
        <div>
          <div className="section-kicker">02 / STYLE BIBLE</div>
          <h2>先定风格，再开机位</h2>
          <p>七维规则冻结为不可变快照；新镜头默认绑定当前版本，旧镜头保留历史版本。</p>
        </div>
        <div className={`frozen-badge ${currentStyle ? 'is-frozen' : ''}`}>
          {currentStyle ? <><LockKeyhole size={13} /> 已冻结 V{currentStyle.version}</> : <><LockOpen size={13} /> 尚未冻结</>}
        </div>
      </div>
      <div className="style-grid">
        {styleFields.map(([key, label, hint], index) => (
          <label className="style-field" key={key}>
            <span><i>0{index + 1}</i>{label}</span>
            <textarea
              value={style[key]}
              placeholder={hint}
              rows={2}
              onChange={(event) => setStyle((value) => ({ ...value, [key]: event.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="section-footer">
        <span>{currentStyle ? <><Check size={13} /> 当前哈希 {currentStyle.sha256.slice(0, 18)}…</> : '所有七项必须填写，并由你明确执行冻结'}</span>
        <div>
          <button className="secondary-button" disabled={busy || !style.photography_language} onClick={onSave}>保存草案</button>
          <button className="primary-button" disabled={busy || styleFields.some(([key]) => !style[key].trim())} onClick={onFreeze}>
            <LockKeyhole size={14} /> {currentStyle ? '冻结为新版本' : '批准并冻结'}
          </button>
        </div>
      </div>
      {styles.length > 0 && <div className="version-row">
        <span className="version-label"><History size={12} /> 版本记录</span>
        {styles.map((version) => (
          <span className={`version-chip ${version.id === currentStyle?.id ? 'current' : ''}`} key={version.id}>
            V{version.version} · {version.sha256.slice(0, 8)}
          </span>
        ))}
      </div>}
    </section>
  );
}
