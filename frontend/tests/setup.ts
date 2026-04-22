import { vi } from 'vitest';
import { expect, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Clean up after each test
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

// Extend expect with Testing Library matchers
expect.extend({
  // Custom matchers can be added here
});
