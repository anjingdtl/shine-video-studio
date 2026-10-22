import { defineConfig, loadEnv, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', 'SHINE_VITE_').SHINE_VITE_API_TARGET || 'http://127.0.0.1:8000';
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(target).hostname)) {
    throw new Error('The development API proxy must remain on loopback.');
  }
  const proxy: ProxyOptions = {
    target,
    changeOrigin: false,
    configure(server) {
      server.on('proxyReq', (proxyRequest, request) => {
        // Operator same-origin checks must see the browser-facing origin.
        if (request.headers.host) proxyRequest.setHeader('host', request.headers.host);
      });
    },
  };
  return { plugins: [react()], server: { host: '127.0.0.1', proxy: { '/api': proxy, '/health': proxy } } };
});
