import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const workspacePanel = readFileSync(
  new URL('../src/components/WorkspaceOperationPanel.tsx', import.meta.url),
  'utf8',
);

test('HTML preview keeps agent-generated scripts in an opaque-origin sandbox', () => {
  const sandboxMatch = workspacePanel.match(/sandbox="([^"]+)"/);

  assert.ok(sandboxMatch, 'HTML preview iframe must declare a sandbox');
  assert.match(sandboxMatch[1], /\ballow-scripts\b/);
  assert.doesNotMatch(sandboxMatch[1], /\ballow-same-origin\b/);
});
