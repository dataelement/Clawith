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
