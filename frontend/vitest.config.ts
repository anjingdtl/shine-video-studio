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
    // Only this project's tests: a leftover dependency backup inside frontend/
    // (node_modules.broken-*) carries third-party spec files that must not be
    // collected as if they were ours.
    include: ['src/**/*.test.{ts,tsx}'],
    // jsdom + userEvent suites regularly take 4–5s on low-resource local
    // machines; the 5s default turns normal CPU contention into false
    // timeouts when the whole suite runs together. Assertions stay unchanged.
    testTimeout: 15000,
    hookTimeout: 15000,
  },
});
