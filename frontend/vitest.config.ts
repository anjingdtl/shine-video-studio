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
  },
});
