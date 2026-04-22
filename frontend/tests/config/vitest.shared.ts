import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        'tests/',
        'src/types/',
        'src/stories/',
        'src/assets/',
        '**/index.ts',
        '**/types.ts',
        '**/__generated__/**',
        '**/*.stories.tsx',
        '**/*.d.ts',
        'src/main.tsx',
        'src/App.tsx',
      ],
    },
  },
  resolve: {
    alias: {
      '@': '/src',
    },
  },
});
