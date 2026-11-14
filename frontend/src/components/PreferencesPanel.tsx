import { useEffect, useState } from 'react';
import { ArrowRight, Check, Film, MonitorCog, Mountain, Moon, ShieldCheck, Sparkles, Sun, Type, Zap } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTheme } from '../theme/useTheme';
import { setTheme } from '../theme/themeStore';
import { THEMES, type ThemeId } from '../theme/themes';

type Props = { onNavigate: (id: string) => void };

const FONT_STEPS = [
  { key: 'small', text: '小', zoom: '0.92' },
  { key: 'standard', text: '标准', zoom: '1' },
  { key: 'large', text: '大', zoom: '1.12' },
];

const THEME_ICONS: Record<ThemeId, LucideIcon> = {
  light: Sun,
  dark: Moon,
  xianxia: Mountain,
  cyberpunk: Zap,
  cinema: Film,
  comic: Sparkles,
};

const CONFIG_GUIDES: Array<{ id: string; title: string; desc: string }> = [
  { id: 'settings', title: 'AI 模型（LLM）配置', desc: '添加/更换写作、审片用的模型，填密钥，测试连接' },
  { id: 'settings', title: 'ComfyUI 生成引擎连接', desc: '填本机 ComfyUI 地址并测试连通' },
  { id: 'settings', title: '生成工作流模板', desc: '「模型与系统」底部「工作流 Profile 注册」' },
  { id: 'setup', title: '模型与节点安装', desc: '点击检测，再确认下载缺项并自动配置' },
  { id: 'setup', title: '新机环境自检', desc: '显卡、磁盘、上游可达性、就绪分层检查' },
];

export default function PreferencesPanel({ onNavigate }: Props) {
  const theme = useTheme();
  const [fontKey, setFontKey] = useState(() => {
    try {
      return window.localStorage.getItem('shine-font') ?? 'standard';
    } catch {
      return 'standard';
    }
  });

  useEffect(() => {
    const step = FONT_STEPS.find((item) => item.key === fontKey) ?? FONT_STEPS[1];
    document.documentElement.style.zoom = step.zoom;
    try {
      window.localStorage.setItem('shine-font', fontKey);
      window.localStorage.setItem('shine-font-zoom', step.zoom);
    } catch {
      // Blocked storage keeps the in-memory text size for this session.
    }
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
      <div className="pref-block">
        <h3><Sparkles size={13} /> 界面主题</h3>
        <p>六套工作台外观，点击卡片立即切换，不需要保存或重启。</p>
        <div className="theme-grid" role="group" aria-label="界面主题">
          {THEMES.map((item) => {
            const Icon = THEME_ICONS[item.id];
            const active = theme === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={`theme-card ${active ? 'active' : ''}`}
                aria-pressed={active}
                aria-label={`${item.name}主题：${item.description}`}
                onClick={() => setTheme(item.id)}
              >
                <span className="theme-preview" style={{ background: item.preview.background }} aria-hidden="true">
                  <span className="theme-preview-bar" style={{ background: item.preview.text }} />
                  <span className="theme-preview-card" style={{ background: item.preview.surface, borderColor: item.preview.accent }}>
                    <span className="theme-preview-line" style={{ background: item.preview.text }} />
                    <span className="theme-preview-line short" style={{ background: item.preview.text }} />
                    <span className="theme-preview-button" style={{ background: item.preview.accent, color: item.preview.onAccent }}>Aa</span>
                  </span>
                </span>
                <span className="theme-card-title">
                  <Icon size={13} aria-hidden="true" />
                  {item.name}
                  {active && <span className="theme-card-current"><Check size={10} aria-hidden="true" />当前</span>}
                </span>
                <span className="theme-card-desc">{item.description}</span>
              </button>
            );
          })}
        </div>
        <p className="theme-note">
          <ShieldCheck size={12} aria-hidden="true" />
          界面主题仅调整工作台外观，不影响作品导演风格、成片颜色与生成结果。
        </p>
      </div>
      <div className="pref-grid">
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
