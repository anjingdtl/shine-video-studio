import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PreferencesPanel from '../components/PreferencesPanel';
import { __resetThemeStoreForTests, getTheme, setTheme } from '../theme/themeStore';

beforeEach(() => {
  window.localStorage.clear();
  __resetThemeStoreForTests();
  setTheme('light');
  document.documentElement.style.zoom = '';
});

afterEach(() => {
  cleanup();
  document.documentElement.style.zoom = '';
});

describe('six-theme preference picker', () => {
  it('renders six visual theme cards and switches on click', async () => {
    const user = userEvent.setup();
    render(<PreferencesPanel onNavigate={vi.fn()} />);
    const cards = document.querySelectorAll('.theme-card');
    expect(cards).toHaveLength(6);
    expect(screen.getByText('星云银白')).toBeInTheDocument();
    expect(screen.getByText('星际墨夜')).toBeInTheDocument();
    expect(screen.getByText('云阙灵境')).toBeInTheDocument();
    expect(screen.getByText('霓虹之城')).toBeInTheDocument();
    expect(screen.getByText('胶片时光')).toBeInTheDocument();
    expect(screen.getByText('次元物语')).toBeInTheDocument();

    const xianxia = screen.getByRole('button', { name: /云阙灵境主题/ });
    await user.click(xianxia);
    expect(getTheme()).toBe('xianxia');
    expect(document.documentElement.dataset.theme).toBe('xianxia');
    expect(window.localStorage.getItem('shine-theme')).toBe('xianxia');
    expect(xianxia).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('当前')).toBeInTheDocument();

    const comic = screen.getByRole('button', { name: /次元物语主题/ });
    await user.click(comic);
    expect(getTheme()).toBe('comic');
    expect(comic).toHaveAttribute('aria-pressed', 'true');
    expect(xianxia).toHaveAttribute('aria-pressed', 'false');
  });

  it('switches with the keyboard and keeps a visible focus target', async () => {
    const user = userEvent.setup();
    render(<PreferencesPanel onNavigate={vi.fn()} />);
    const card = screen.getByRole('button', { name: /胶片时光主题/ });
    card.focus();
    expect(document.activeElement).toBe(card);
    await user.keyboard('{Enter}');
    expect(getTheme()).toBe('cinema');
    expect(document.documentElement.dataset.theme).toBe('cinema');
  });

  it('keeps the existing text-size preference and explains theme isolation', async () => {
    const user = userEvent.setup();
    render(<PreferencesPanel onNavigate={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '大' }));
    expect(document.documentElement.style.zoom).toBe('1.12');
    expect(window.localStorage.getItem('shine-font')).toBe('large');
    expect(window.localStorage.getItem('shine-font-zoom')).toBe('1.12');
    expect(screen.getByText(/界面主题仅调整工作台外观/)).toBeInTheDocument();
    expect(screen.getByText('各类配置去哪儿找')).toBeInTheDocument();
  });
});
