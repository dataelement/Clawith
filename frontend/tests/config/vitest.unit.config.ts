import { defineConfig, mergeConfig } from 'vitest/config';

import sharedConfig from './vitest.shared';

export default mergeConfig(
  sharedConfig,
  defineConfig({
    test: {
      name: 'unit',
      include: ['tests/unit/**/*.test.ts', 'tests/unit/**/*.test.tsx'],
      reporters: ['verbose', 'junit'],
      outputFile: {
        junit: './reports/junit.unit.xml',
      },
    },
  }),
);
