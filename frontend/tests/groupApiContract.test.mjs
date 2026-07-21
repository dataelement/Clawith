import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/services/groupApi.ts', import.meta.url),
  'utf8',
);

test('group invite reads backend candidates and posts only participant_id', () => {
  const payload = source.match(/interface InviteMemberPayload\s*{([^}]*)}/)?.[1] ?? '';
  assert.match(source, /memberCandidates:[\s\S]*member-candidates/);
  assert.match(source, /participant_type: participantType/);
  assert.match(payload, /participant_id: string;/);
  assert.doesNotMatch(payload, /participant_type/);
  assert.doesNotMatch(payload, /ref_id/);
});

test('group message backfill sends the forward cursor to the backend', () => {
  assert.match(source, /opts:\s*{\s*limit\?: number; before\?: string; after\?: string\s*}/);
  assert.match(source, /after:\s*opts\.after/);
  assert.doesNotMatch(source, /backend does not implement it yet/);
});

test('group runs expose exact state and explicit cancellation by run id', () => {
  assert.match(source, /activeRuns:[\s\S]*sessions\/\$\{sessionId\}\/runs/);
  assert.match(source, /runState:[\s\S]*sessions\/\$\{sessionId\}\/runs\/\$\{runId\}/);
  assert.match(source, /cancelRun:[\s\S]*runs\/\$\{runId\}\/cancel/);
});
