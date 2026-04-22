import { defineConfig, mergeConfig } from 'vitest/config';

import sharedConfig from './vitest.shared';

export default mergeConfig(
  sharedConfig,
  defineConfig({
    test: {
      name: 'services',
      include: ['tests/services/**/*.test.ts', 'tests/services/**/*.test.tsx'],
      reporters: ['verbose', 'junit'],
      outputFile: {
        junit: './reports/junit.services.xml',
      },
    },
  }),
);
