import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);

test('chat attachments preserve Agent-root-relative paths for execute_code', () => {
  assert.doesNotMatch(source, /wsPath\.replace\(\/\^workspace\\\//);
  assert.match(source, /use the same Agent-root-relative path/);
  assert.match(source, /sandbox working directory is "\/"/);
});
