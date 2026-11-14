import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';
import './theme/themes.css';
import './theme/glass.css';
import { bootstrapTheme } from './theme/themeStore';

// Restore the one stored theme before the first React paint.
bootstrapTheme();
try {
  const savedZoom = window.localStorage.getItem('shine-font-zoom');
  if (savedZoom) document.documentElement.style.zoom = savedZoom;
} catch {
  // Blocked storage keeps the default zoom; the app still renders.
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
