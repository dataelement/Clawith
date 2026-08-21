import assert from 'node:assert/strict';
import test from 'node:test';

import { belongsInOtherSessions } from '../src/pages/agent-detail/sessionVisibility.ts';

test('external group sessions stay visible when their placeholder owner is the viewer', () => {
  assert.equal(
    belongsInOtherSessions({
      user_id: 'viewer-1',
      source_channel: 'feishu',
      participant_type: 'group',
      is_group: true,
    }, 'viewer-1'),
    true,
  );
});

test('agent sessions stay visible regardless of their stored user id', () => {
  assert.equal(
    belongsInOtherSessions({
      user_id: 'viewer-1',
      source_channel: 'agent',
      participant_type: 'agent',
    }, 'viewer-1'),
    true,
  );
});

test('the viewer own direct session stays out of other sessions', () => {
  assert.equal(
    belongsInOtherSessions({
      user_id: 'viewer-1',
      source_channel: 'web',
      participant_type: 'user',
      is_group: false,
    }, 'viewer-1'),
    false,
  );
});

test('another user direct session remains visible', () => {
  assert.equal(
    belongsInOtherSessions({
      user_id: 'user-2',
      source_channel: 'web',
      participant_type: 'user',
      is_group: false,
    }, 'viewer-1'),
    true,
  );
});
