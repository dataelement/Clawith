import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/hooks/useGroupRealtime.ts', import.meta.url),
  'utf8',
);

test('group realtime uses websocket push and forward cursor catch-up', () => {
  assert.match(source, /\/ws\/group\/\$\{groupId\}/);
  assert.match(source, /after,/);
  assert.doesNotMatch(source, /const USE_AFTER_CURSOR = false/);
});

test('group realtime accepts the canonical message.created payload', () => {
  assert.match(source, /payload\.type !== 'message\.created'/);
  assert.match(source, /payload\.message/);
  assert.match(source, /payload\.session_id/);
});
