import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  activeRunForSession,
  failClosedSessionActiveRun,
  mergeSessionToolMessage,
  mergeSessionToolMessages,
  runtimeCompletionNeedsMessageRefresh,
  runtimeTerminalPacketNeedsMessageRefresh,
  sessionActiveRunFromResponse,
  sessionRuntimeStateResponseIsValid,
  mergeTerminalAssistantMessage,
  terminalAssistantMessageAlreadyPresent,
  toolReconciliationNeedsUserAction,
  toolReconciliationsByCallId,
  waitingSessionActiveRunHint,
} from '../src/pages/agent-detail/sessionRuntimeState.ts';

const agentDetailSource = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);

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

test('active run controls are projected only onto their own selected session', () => {
  assert.equal(activeRunForSession(waitingRun, 'session-2'), null);
  assert.equal(activeRunForSession(waitingRun, null), null);
  assert.equal(activeRunForSession(waitingRun, 'session-1'), waitingRun);
});

test('session Tool cache restores a running card after switching back', () => {
  const running = {
    role: 'tool_call',
    content: '',
    toolName: 'move_file',
    toolCallId: 'call-1',
    toolArgs: { path: 'draft.md' },
    toolStatus: 'running',
  };

  assert.deepEqual(mergeSessionToolMessages([
    { id: 'user-1', role: 'user', content: 'move it' },
  ], [running]), [
    { id: 'user-1', role: 'user', content: 'move it' },
    running,
  ]);
});

test('session Tool cache updates by call id without downgrading canonical history', () => {
  const running = {
    role: 'tool_call',
    content: '',
    toolName: 'move_file',
    toolCallId: 'call-1',
    toolArgs: { path: 'draft.md' },
    toolStatus: 'running',
  };
  const done = { ...running, toolStatus: 'done', toolResult: 'moved' };

  assert.deepEqual(mergeSessionToolMessage([running], done), [done]);
  assert.deepEqual(mergeSessionToolMessage([done], running), [done]);
});

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

test('terminal websocket packet refreshes canonical tool-call history', () => {
  assert.equal(runtimeTerminalPacketNeedsMessageRefresh('completed'), true);
  assert.equal(runtimeTerminalPacketNeedsMessageRefresh('failed'), true);
  assert.equal(runtimeTerminalPacketNeedsMessageRefresh('cancelled'), true);
  assert.equal(runtimeTerminalPacketNeedsMessageRefresh('waiting_user'), false);
  assert.equal(runtimeTerminalPacketNeedsMessageRefresh(undefined), false);
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

test('poll-before-websocket ordering merges runtime diagnostics into the canonical message', () => {
  const canonical = {
    id: 'message-1',
    role: 'assistant',
    content: 'request failed',
  };
  const runtimeError = {
    message: 'request failed',
    code: 'provider_rate_limited',
    traceId: 'worker-trace',
    runId: 'run-1',
  };

  assert.deepEqual(
    mergeTerminalAssistantMessage(
      [canonical],
      { ...canonical, runtimeError },
    ),
    [{ ...canonical, runtimeError }],
  );
  assert.equal(
    mergeTerminalAssistantMessage([], { ...canonical, runtimeError }).length,
    1,
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

test('workspace reconciliation fields preserve status and file counts', () => {
  const parsed = sessionActiveRunFromResponse({
    active_run: {
      run_id: 'run-1',
      thread_id: 'session-1',
      session_id: 'session-1',
      status: 'waiting_user',
      pending_tool_reconciliations: [{
        execution_id: 'execution-1',
        tool_call_id: 'call-1',
        tool_name: 'write_file',
        can_reconcile: true,
        resolution_status: 'conflicted',
        savedCount: 1,
        pendingCount: 2,
        conflictedCount: 1,
        workspaceResolution: true,
      }],
    },
  });

  assert.deepEqual(parsed?.pendingToolReconciliations[0], {
    executionId: 'execution-1',
    toolCallId: 'call-1',
    toolName: 'write_file',
    resultSummary: null,
    errorCode: null,
    canReconcile: true,
    resolutionStatus: 'conflicted',
    savedCount: 1,
    pendingCount: 2,
    conflictedCount: 1,
    workspaceResolution: true,
  });
  assert.equal(toolReconciliationNeedsUserAction(parsed.pendingToolReconciliations[0]), true);
  assert.equal(
    toolReconciliationsByCallId(parsed.pendingToolReconciliations).get('call-1')?.executionId,
    'execution-1',
  );
});

test('only settled verification avoids user action; pending receipts always keep an exit', () => {
  assert.equal(toolReconciliationNeedsUserAction({
    executionId: 'execution-1',
    toolCallId: 'call-1',
    toolName: 'write_file',
    canReconcile: false,
    resolutionStatus: 'checking',
    workspaceResolution: true,
  }), false);
  assert.equal(toolReconciliationNeedsUserAction({
    executionId: 'execution-1',
    toolCallId: 'call-1',
    toolName: 'write_file',
    canReconcile: true,
    resolutionStatus: 'saved',
    workspaceResolution: true,
  }), true);
});

test('workspace reconciliation keeps decisions in the composer and passive status on the tool row', () => {
  assert.match(agentDetailSource, /toolCallId: msg\.toolCallId/);
  assert.match(agentDetailSource, /reconciliationsByToolCallId\.get\(tc\.toolCallId\)/);
  assert.match(agentDetailSource, /Agent 处理后的文件与工作区中的源文件不同。请选择要保留哪一个。/);
  assert.match(agentDetailSource, /使用 Agent 的结果/);
  assert.match(agentDetailSource, /保留源文件/);
  assert.match(agentDetailSource, /className="chat-tool-reconciliation"/);
  assert.doesNotMatch(agentDetailSource, /locateToolReconciliation/);
  assert.doesNotMatch(agentDetailSource, /analysis-tool-reconciliation__actions/);
});

test('loaded Tool rows cannot be hidden behind a stale zero session count', () => {
  assert.match(
    agentDetailSource,
    /Math\.max\(totalToolCount \?\? 0, toolItems\.length\)/,
  );
  assert.match(
    agentDetailSource,
    /toolCallsTotal', \{ count: resolvedToolCount \}/,
  );
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
