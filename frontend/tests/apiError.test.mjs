import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  ApiError,
  AppError,
  normalizeUnknownError,
  parseHttpError,
  parseHttpErrorResponse,
} from '../src/services/apiError.ts';

test('canonical envelope wins and retains all HTTP diagnostic context', async () => {
  const error = await parseHttpErrorResponse(new Response(JSON.stringify({
    detail: 'legacy message',
    error: {
      code: 'model_tool_calling_unverified',
      message: 'Model tools are not verified',
      trace_id: 'body-trace',
      run_id: 'run-1',
      agent_id: 'agent-1',
      stage: 'intake',
      details: { model: 'qwen' },
      retryable: false,
    },
  }), {
    status: 409,
    statusText: 'Conflict',
    headers: { 'X-Trace-Id': 'header-trace' },
  }));

  assert.ok(error instanceof ApiError);
  assert.ok(error instanceof AppError);
  assert.equal(error.message, 'Model tools are not verified');
  assert.equal(error.code, 'model_tool_calling_unverified');
  assert.equal(error.status, 409);
  assert.equal(error.traceId, 'body-trace');
  assert.equal(error.runId, 'run-1');
  assert.equal(error.agentId, 'agent-1');
  assert.equal(error.stage, 'intake');
  assert.deepEqual(error.details, { model: 'qwen' });
  assert.equal(error.retryable, false);
  assert.equal(error.detail, 'legacy message');
});

test('response trace header fills canonical trace id when the body omits it', async () => {
  const error = await parseHttpErrorResponse(new Response(JSON.stringify({
    error: { code: 'denied', message: 'Request denied' },
  }), { status: 403, headers: { 'X-Trace-Id': 'header-trace' } }));
  assert.equal(error.traceId, 'header-trace');
});

test('legacy detail supports strings, structured objects, and validation lists', () => {
  const stringError = parseHttpError({ status: 400, bodyText: JSON.stringify({ detail: 'Bad input' }) });
  assert.equal(stringError.message, 'Bad input');
  assert.equal(stringError.code, 'http_400');

  const objectDetail = { message: 'Email verification required', needs_verification: true };
  const objectError = parseHttpError({ status: 403, bodyText: JSON.stringify({ detail: objectDetail }) });
  assert.equal(objectError.message, 'Email verification required');
  assert.deepEqual(objectError.detail, objectDetail);

  const validationError = parseHttpError({
    status: 422,
    bodyText: JSON.stringify({ detail: [{ loc: ['body', 'name'], msg: 'Field required' }] }),
  });
  assert.equal(validationError.message, '名称: Field required');
});

test('legacy structured detail retains diagnostic fields during migration', () => {
  const error = parseHttpError({
    status: 503,
    bodyText: JSON.stringify({
      detail: {
        message: 'Worker unavailable',
        code: 'worker_unavailable',
        trace_id: 'legacy-trace',
        run_id: 'legacy-run',
        agent_id: 'legacy-agent',
        stage: 'execution',
        retryable: true,
      },
    }),
  });
  assert.equal(error.code, 'worker_unavailable');
  assert.equal(error.traceId, 'legacy-trace');
  assert.equal(error.runId, 'legacy-run');
  assert.equal(error.agentId, 'legacy-agent');
  assert.equal(error.stage, 'execution');
  assert.equal(error.retryable, true);
});

test('object details never degrade to object stringification when a message exists', () => {
  const error = parseHttpError({
    status: 400,
    bodyText: JSON.stringify({ detail: { error: { message: 'Nested backend message' } } }),
  });
  assert.equal(error.message, 'Nested backend message');
  assert.notEqual(error.message, '[object Object]');
});

test('plain text and empty responses receive useful messages', () => {
  assert.equal(parseHttpError({ status: 502, bodyText: 'Upstream unavailable' }).message, 'Upstream unavailable');
  assert.equal(parseHttpError({ status: 404, statusText: 'Not Found', bodyText: '' }).message, 'HTTP 404 Not Found');
});

test('unknown thrown values normalize to typed AppError instances', () => {
  const native = normalizeUnknownError(new Error('connection reset'), {
    code: 'network_error',
    source: 'http',
    retryable: true,
  });
  assert.ok(native instanceof AppError);
  assert.equal(native.message, 'connection reset');
  assert.equal(native.code, 'network_error');
  assert.equal(native.retryable, true);

  const object = normalizeUnknownError({ message: 'backend message' }, { source: 'runtime' });
  assert.equal(object.message, 'backend message');
  assert.equal(object.source, 'runtime');
});

test('normal requests and both upload transports use the shared parser', () => {
  const apiSource = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8');
  const enterpriseFetcher = readFileSync(
    new URL('../src/pages/enterprise-settings/utils/fetchJson.ts', import.meta.url),
    'utf8',
  );
  const enterpriseSettings = readFileSync(new URL('../src/pages/EnterpriseSettings.tsx', import.meta.url), 'utf8');
  const userManagement = readFileSync(new URL('../src/pages/UserManagement.tsx', import.meta.url), 'utf8');
  const adminCompanies = readFileSync(new URL('../src/pages/AdminCompanies.tsx', import.meta.url), 'utf8');

  assert.match(apiSource, /const apiError = await parseHttpErrorResponse\(res\)/);
  assert.match(apiSource, /throw await parseHttpErrorResponse\(res\)/);
  assert.match(apiSource, /reject\(parseHttpError\(\{/);
  assert.match(apiSource, /getResponseHeader\('X-Trace-Id'\)/);
  assert.match(enterpriseFetcher, /export \{ fetchJson \} from '\.\.\/\.\.\/\.\.\/services\/api';/);
  for (const pageSource of [enterpriseSettings, userManagement, adminCompanies]) {
    assert.doesNotMatch(pageSource, /async function fetchJson/);
  }
});
