import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Component tests run against the real components and a stubbed global fetch;
// no separate framework beyond vitest + Testing Library is introduced.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // jsdom + userEvent suites regularly take 4–5s on low-resource local
    // machines; the 5s default turns normal CPU contention into false
    // timeouts when the whole suite runs together. Assertions stay unchanged.
    testTimeout: 15000,
    hookTimeout: 15000,
  },
});
