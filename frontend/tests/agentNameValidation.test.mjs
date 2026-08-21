import assert from 'node:assert/strict';
import test from 'node:test';

import { validateAgentName } from '../src/utils/agentNameValidation.ts';

test('agent name validation follows the backend 2 to 100 character contract', () => {
  assert.equal(validateAgentName(''), 'required');
  assert.equal(validateAgentName('   '), 'required');
  assert.equal(validateAgentName('A'), 'too_short');
  assert.equal(validateAgentName(' Agent '), null);
  assert.equal(validateAgentName('界'.repeat(100)), null);
  assert.equal(validateAgentName('界'.repeat(101)), 'too_long');
  assert.equal(validateAgentName('🤖'), 'too_short');
  assert.equal(validateAgentName('🤖'.repeat(51)), null);
  assert.equal(validateAgentName('🤖'.repeat(101)), 'too_long');
});
