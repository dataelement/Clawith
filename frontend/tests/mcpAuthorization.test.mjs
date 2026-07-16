import assert from 'node:assert/strict';
import test from 'node:test';

import {
  closeMcpAuthorizationWindow,
  getSmitheryAuthorizationButtonLabel,
  getSmitheryAuthorizationTool,
  isSmitheryManagedMcpTool,
  navigateMcpAuthorizationWindow,
  openMcpAuthorizationWindow,
  requestMcpAuthorizationStatus,
  shouldPreopenMcpAuthorizationWindow,
} from '../src/pages/agent-detail/mcpAuthorization.ts';

const smitheryTool = {
  id: 'tool-1',
  type: 'mcp',
  mcp_authorization_provider: 'smithery',
};

test('only server-marked Smithery MCP groups expose authorization controls', () => {
  assert.equal(isSmitheryManagedMcpTool(smitheryTool), true);
  assert.equal(
    isSmitheryManagedMcpTool({ ...smitheryTool, type: 'builtin' }),
    false,
  );
  assert.equal(
    isSmitheryManagedMcpTool({ id: 'tool-2', type: 'mcp' }),
    false,
  );
  assert.equal(
    getSmitheryAuthorizationTool([
      { id: 'direct-tool', type: 'mcp' },
      smitheryTool,
    ]),
    smitheryTool,
  );
});

test('authorization button labels reflect unknown, auth-required, and connected states', () => {
  assert.equal(getSmitheryAuthorizationButtonLabel('unknown'), 'Authorize');
  assert.equal(getSmitheryAuthorizationButtonLabel('auth_required'), 'Re-authorize');
  assert.equal(getSmitheryAuthorizationButtonLabel('connected'), 'Check');
  assert.equal(getSmitheryAuthorizationButtonLabel('unavailable'), 'Check');
  assert.equal(shouldPreopenMcpAuthorizationWindow('unknown'), true);
  assert.equal(shouldPreopenMcpAuthorizationWindow('auth_required'), true);
  assert.equal(shouldPreopenMcpAuthorizationWindow('connected'), false);
  assert.equal(shouldPreopenMcpAuthorizationWindow('unavailable'), false);
});

test('authorization opens a safe placeholder during the original click gesture', () => {
  const calls = [];
  const popup = {
    closed: false,
    opener: { unsafe: true },
    location: { replace() {} },
    close() {},
  };

  const opened = openMcpAuthorizationWindow((url, target) => {
    calls.push({ url, target });
    return popup;
  });

  assert.equal(opened, popup);
  assert.deepEqual(calls, [{ url: 'about:blank', target: '_blank' }]);
  assert.equal(popup.opener, null);
});

test('authorization navigation uses the placeholder or a reliable same-page fallback', () => {
  const navigations = [];
  let closed = false;
  const popup = {
    closed: false,
    opener: null,
    location: {
      replace(url) {
        navigations.push(['popup', url]);
      },
    },
    close() {
      closed = true;
    },
  };

  assert.equal(
    navigateMcpAuthorizationWindow(
      popup,
      'https://provider.example/authorize',
      (url) => navigations.push(['same_page', url]),
    ),
    'popup',
  );
  assert.deepEqual(navigations, [
    ['popup', 'https://provider.example/authorize'],
  ]);

  popup.closed = true;
  assert.equal(
    navigateMcpAuthorizationWindow(
      popup,
      'https://provider.example/reauthorize',
      (url) => navigations.push(['same_page', url]),
    ),
    'same_page',
  );
  assert.deepEqual(navigations.at(-1), [
    'same_page',
    'https://provider.example/reauthorize',
  ]);

  popup.closed = false;
  closeMcpAuthorizationWindow(popup);
  assert.equal(closed, true);
});

test('authorization request submits only agent and assigned tool ids via GET', async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      async json() {
        return {
          provider: 'smithery',
          state: 'auth_required',
          connected: false,
          authorization_url: 'https://provider.example/authorize',
        };
      },
    };
  };

  const status = await requestMcpAuthorizationStatus(
    'agent-1',
    'tool-1',
    'browser-token',
    fakeFetch,
  );

  assert.equal(status.state, 'auth_required');
  assert.equal(status.authorizationUrl, 'https://provider.example/authorize');
  assert.deepEqual(calls, [
    {
      url: '/api/tools/agents/agent-1/mcp-tools/tool-1/authorization-status',
      init: {
        method: 'GET',
        cache: 'no-store',
        headers: { Authorization: 'Bearer browser-token' },
      },
    },
  ]);
  assert.equal('body' in calls[0].init, false);
});

test('malformed authorization responses fail closed', async () => {
  const fakeFetch = async () => ({
    ok: true,
    async json() {
      return {
        provider: 'smithery',
        state: 'auth_required',
        connected: false,
        authorization_url: 'javascript:alert(1)',
      };
    },
  });

  await assert.rejects(
    requestMcpAuthorizationStatus(
      'agent-1',
      'tool-1',
      'browser-token',
      fakeFetch,
    ),
    /invalid authorization status/i,
  );
});
