import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  formatRuntimeErrorDiagnostics,
  normalizeRuntimeError,
  runtimeErrorDisablesReconnect,
} from '../src/services/runtimeError.ts';

const source = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);
const runtimeErrorSource = readFileSync(
  new URL('../src/services/runtimeError.ts', import.meta.url),
  'utf8',
);

test('direct chat reattaches an active run from its last durable event cursor', () => {
  assert.match(source, /type: 'attach_run'/);
  assert.match(source, /runtimeEventCursorRef/);
  assert.match(source, /event_cursor/);
  assert.match(source, /run_id: active\.runId/);
});

test('replayed tool packets keep one row by stable tool call id', () => {
  assert.match(source, /msg\.toolCallId === toolMsg\.toolCallId/);
  assert.match(source, /existing\.toolStatus === 'done' && toolMsg\.toolStatus === 'running'/);
  assert.match(source, /toolCallId: message\.toolCallId/);
  assert.match(source, /toolCallId: m\.toolCallId/);
});

test('an authoritative active run keeps a thinking indicator visible after reload', () => {
  assert.match(source, /\['queued', 'running'\]\.includes\(activeRun\.status\)/);
  assert.match(source, /showDirectRunThinking/);
  assert.match(source, /\{showDirectRunThinking && \(/);
  assert.match(source, /lastChatMessage\.toolStatus === 'running'/);
});

test('direct chat renders canonical runtime diagnostics and keeps legacy fallbacks', () => {
  assert.match(source, /normalizeRuntimeError\(d\)/);
  assert.match(source, /formatRuntimeErrorDiagnostics\(msg\.runtimeError\)/);
  assert.match(runtimeErrorSource, /canonical\.message/);
  assert.match(runtimeErrorSource, /packet\.content/);
  assert.match(runtimeErrorSource, /packet\.delivery_error/);
  assert.match(runtimeErrorSource, /Code: \$\{error\.code\}/);
  assert.match(runtimeErrorSource, /Trace: \$\{error\.traceId\}/);
  assert.match(runtimeErrorSource, /Run: \$\{error\.runId\}/);
  assert.match(source, /runtimeError: normalizeRuntimeError\(\{ error: (?:message|m)\.runtime_error \}\)/);
});

test('runtime error classification uses stable codes instead of English message fragments', () => {
  assert.match(runtimeErrorSource, /error\.code === 'model_unavailable'/);
  assert.match(runtimeErrorSource, /error\.code === 'agent_expired'/);
  assert.doesNotMatch(runtimeErrorSource, /\.includes\(/);
});

test('runtime packet normalization prefers canonical context and supports legacy delivery errors', () => {
  assert.deepEqual(normalizeRuntimeError({
    content: 'legacy message',
    code: 'legacy_code',
    error: {
      message: 'safe backend message',
      code: 'provider_rate_limited',
      trace_id: 'trace-1',
      run_id: 'run-1',
      agent_id: 'agent-1',
      stage: 'execution',
    },
  }), {
    message: 'safe backend message',
    code: 'provider_rate_limited',
    traceId: 'trace-1',
    runId: 'run-1',
    agentId: 'agent-1',
    stage: 'execution',
  });

  const legacy = normalizeRuntimeError({
    content: 'Delivery failed',
    delivery_error: 'session_deleted',
    run_id: 'run-2',
  });
  assert.equal(legacy.code, 'session_deleted');
  assert.equal(
    formatRuntimeErrorDiagnostics(legacy),
    'Code: session_deleted · Run: run-2',
  );
  assert.equal(runtimeErrorDisablesReconnect({ message: 'No model', code: 'other' }), false);
  assert.equal(runtimeErrorDisablesReconnect({ message: 'anything', code: 'model_unavailable' }), true);
});
