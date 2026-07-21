import assert from 'node:assert/strict';
import test from 'node:test';

import {
  onboardingKickoffKey,
  shouldKickoffOnboarding,
} from '../src/pages/agent-detail/onboardingKickoff.ts';

const ready = {
  websocketReady: true,
  messagesLoaded: true,
  runtimeStateLoaded: true,
  messageCount: 0,
  hasActiveRun: false,
};

test('onboarding kickoff waits for empty history and authoritative idle runtime state', () => {
  assert.equal(shouldKickoffOnboarding(ready), true);
  assert.equal(shouldKickoffOnboarding({ ...ready, messagesLoaded: false }), false);
  assert.equal(shouldKickoffOnboarding({ ...ready, runtimeStateLoaded: false }), false);
  assert.equal(shouldKickoffOnboarding({ ...ready, messageCount: 1 }), false);
  assert.equal(shouldKickoffOnboarding({ ...ready, hasActiveRun: true }), false);
});

test('onboarding deduplication key belongs to the agent-user pair, not a session', () => {
  assert.equal(onboardingKickoffKey('agent-1', 'user-1'), 'agent-1:user-1');
  assert.equal(
    onboardingKickoffKey('agent-1', 'user-1'),
    onboardingKickoffKey('agent-1', 'user-1'),
  );
});
