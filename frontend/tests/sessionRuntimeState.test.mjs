import assert from 'node:assert/strict';
import test from 'node:test';

import {
  failClosedSessionActiveRun,
  runtimeCompletionNeedsMessageRefresh,
  sessionActiveRunFromResponse,
  sessionRuntimeStateResponseIsValid,
  terminalAssistantMessageAlreadyPresent,
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
  pendingToolReconciliations: [],
};

test('runtime-state request failure preserves display identity but disables actions', () => {
  assert.deepEqual(failClosedSessionActiveRun(waitingRun), {
    ...waitingRun,
    canResume: false,
    canCancel: false,
  });
  assert.equal(failClosedSessionActiveRun(null), null);
});

test('settled lane transition refreshes canonical messages after websocket loss', () => {
  assert.equal(runtimeCompletionNeedsMessageRefresh(waitingRun, null), true);
  assert.equal(runtimeCompletionNeedsMessageRefresh(null, null), false);
  assert.equal(runtimeCompletionNeedsMessageRefresh(waitingRun, waitingRun), false);
});

test('websocket terminal packet does not duplicate a canonical refreshed answer', () => {
  assert.equal(
    terminalAssistantMessageAlreadyPresent(
      [{ id: 'message-1', role: 'assistant', content: 'final answer', _streaming: false }],
      'message-1',
      'final answer',
    ),
    true,
  );
  assert.equal(
    terminalAssistantMessageAlreadyPresent(
      [{ id: 'message-1', role: 'assistant', content: 'final answer', _streaming: false }],
      'message-2',
      'final answer',
    ),
    false,
  );
  assert.equal(
    terminalAssistantMessageAlreadyPresent(
      [{ role: 'assistant', content: 'final answer', _streaming: true }],
      null,
      'final answer',
    ),
    false,
  );
  assert.equal(
    terminalAssistantMessageAlreadyPresent(
      [{ role: 'assistant', content: 'final answer', _streaming: false }],
      null,
      'final answer',
    ),
    true,
  );
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

test('unknown write reconciliation is parsed strictly and disables plain resume', () => {
  const parsed = sessionActiveRunFromResponse({
    active_run: {
      run_id: 'run-1',
      thread_id: 'session-1',
      session_id: 'session-1',
      status: 'waiting_user',
      waiting_type: 'user',
      correlation_id: 'confirm-1',
      model_step_count: 3,
      can_resume: false,
      can_cancel: true,
      pending_tool_reconciliations: [{
        execution_id: 'execution-1',
        tool_call_id: 'call-1',
        tool_name: 'write_file',
        result_summary: 'outcome unknown',
        error_code: 'workspace_write_outcome_unknown',
        can_reconcile: true,
      }],
    },
  });

  assert.equal(parsed?.canResume, false);
  assert.deepEqual(parsed?.pendingToolReconciliations, [{
    executionId: 'execution-1',
    toolCallId: 'call-1',
    toolName: 'write_file',
    resultSummary: 'outcome unknown',
    errorCode: 'workspace_write_outcome_unknown',
    canReconcile: true,
  }]);
  assert.equal(sessionActiveRunFromResponse({
    active_run: {
      run_id: 'run-1',
      thread_id: 'session-1',
      session_id: 'session-1',
      status: 'waiting_user',
      pending_tool_reconciliations: [{ execution_id: 'execution-1' }],
    },
  }), null);
});

test('onboarding only treats an authoritative runtime-state payload as loaded', () => {
  assert.equal(sessionRuntimeStateResponseIsValid({ active_run: null }, null), true);
  assert.equal(sessionRuntimeStateResponseIsValid({}, null), false);
  assert.equal(
    sessionRuntimeStateResponseIsValid({ active_run: { status: 'running' } }, null),
    false,
  );
  assert.equal(
    sessionRuntimeStateResponseIsValid({ active_run: waitingRun }, waitingRun),
    true,
  );
});
