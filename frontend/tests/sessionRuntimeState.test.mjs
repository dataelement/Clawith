import assert from 'node:assert/strict';
import test from 'node:test';

import {
  failClosedSessionActiveRun,
  sessionActiveRunFromResponse,
  waitingSessionActiveRunHint,
} from '../src/pages/agent-detail/sessionRuntimeState.ts';

const waitingRun = {
  runId: 'run-1',
  threadId: 'session-1',
  sessionId: 'session-1',
  status: 'waiting_user',
  waitingType: 'user',
  waitingReason: 'Continue?',
  correlationId: 'confirm-1',
  modelStepCount: 2,
  canResume: true,
  canCancel: true,
};

test('runtime-state request failure preserves display identity but disables actions', () => {
  assert.deepEqual(failClosedSessionActiveRun(waitingRun), {
    ...waitingRun,
    canResume: false,
    canCancel: false,
  });
  assert.equal(failClosedSessionActiveRun(null), null);
});

test('waiting websocket packet is only a non-actionable hint', () => {
  assert.deepEqual(
    waitingSessionActiveRunHint({
      runId: 'run-1',
      sessionId: 'session-1',
      correlationId: 'confirm-1',
      current: waitingRun,
    }),
    {
      ...waitingRun,
      threadId: 'session-1',
      sessionId: 'session-1',
      status: 'waiting_user',
      waitingType: 'user',
      waitingReason: null,
      correlationId: 'confirm-1',
      canResume: false,
      canCancel: false,
    },
  );
});

test('only a valid persisted runtime-state response grants actions', () => {
  assert.deepEqual(
    sessionActiveRunFromResponse({
      active_run: {
        run_id: 'run-1',
        thread_id: 'session-1',
        session_id: 'session-1',
        status: 'waiting_user',
        waiting_type: 'user',
        waiting_reason: 'Continue?',
        correlation_id: 'confirm-1',
        model_step_count: 2,
        can_resume: true,
        can_cancel: true,
      },
    }),
    waitingRun,
  );

  assert.equal(
    sessionActiveRunFromResponse({
      active_run: {
        run_id: 'run-1',
        thread_id: 'session-1',
        session_id: 'session-1',
        status: 'waiting_user',
        correlation_id: null,
        can_resume: true,
        can_cancel: true,
      },
    })?.canResume,
    false,
  );
});
