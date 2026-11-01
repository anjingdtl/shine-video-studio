import { useEffect, useState } from 'react';
import { ArrowRight, MonitorCog, Moon, Sun, Type } from 'lucide-react';

type Props = { onNavigate: (id: string) => void };

const FONT_STEPS = [
  { key: 'small', text: '小', zoom: '0.92' },
  { key: 'standard', text: '标准', zoom: '1' },
  { key: 'large', text: '大', zoom: '1.12' },
];

const CONFIG_GUIDES: Array<{ id: string; title: string; desc: string }> = [
  { id: 'settings', title: 'AI 模型（LLM）配置', desc: '添加/更换写作、审片用的模型，填密钥，测试连接' },
  { id: 'settings', title: 'ComfyUI 生成引擎连接', desc: '填本机 ComfyUI 地址并测试连通' },
  { id: 'settings', title: '生成工作流模板', desc: '「模型与系统」底部「工作流 Profile 注册」' },
  { id: 'preproduction', title: '模型与节点安装', desc: '「制片准备 → 模型与节点」页签，登记/安装/审批' },
  { id: 'setup', title: '新机环境自检', desc: '显卡、磁盘、上游可达性、就绪分层检查' },
];

export default function PreferencesPanel({ onNavigate }: Props) {
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    window.localStorage.getItem('shine-theme') === 'dark' ? 'dark' : 'light');
  const [fontKey, setFontKey] = useState(() =>
    window.localStorage.getItem('shine-font') ?? 'standard');

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem('shine-theme', theme);
  }, [theme]);

  useEffect(() => {
    const step = FONT_STEPS.find((item) => item.key === fontKey) ?? FONT_STEPS[1];
    document.documentElement.style.zoom = step.zoom;
    window.localStorage.setItem('shine-font', fontKey);
    window.localStorage.setItem('shine-font-zoom', step.zoom);
  }, [fontKey]);

  return (
    <section className="card section-card preferences-card" id="preferences">
      <div className="section-heading compact">
        <div>
          <div className="section-kicker">07 / PREFERENCES</div>
          <h2>显示与偏好</h2>
          <p>界面怎么看着舒服你说了算：主题、文字大小都保存在本机，下次打开自动生效。</p>
        </div>
        <div className="section-icon"><MonitorCog size={17} /></div>
      </div>
      <div className="pref-grid">
        <div className="pref-block">
          <h3><Sun size={13} /> 主题</h3>
          <p>浅色适合明亮房间，深色适合暗光环境长时间盯片。</p>
          <div className="pref-options">
            <button className={`pref-option ${theme === 'light' ? 'active' : ''}`} onClick={() => setTheme('light')}>
              <Sun size={15} /> 浅色
            </button>
            <button className={`pref-option ${theme === 'dark' ? 'active' : ''}`} onClick={() => setTheme('dark')}>
              <Moon size={15} /> 深色
            </button>
          </div>
        </div>
        <div className="pref-block">
          <h3><Type size={13} /> 文字大小</h3>
          <p>整体缩放界面文字与控件，不改变任何内容排版。</p>
          <div className="pref-options">
            {FONT_STEPS.map((step) => (
              <button key={step.key} className={`pref-option ${fontKey === step.key ? 'active' : ''}`} onClick={() => setFontKey(step.key)}>
                {step.text}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="pref-block config-guide">
        <h3>各类配置去哪儿找</h3>
        <div className="guide-list">
          {CONFIG_GUIDES.map((guide) => (
            <button className="guide-row" key={guide.title} onClick={() => onNavigate(guide.id)}>
              <div><b>{guide.title}</b><span>{guide.desc}</span></div>
              <ArrowRight size={14} />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
