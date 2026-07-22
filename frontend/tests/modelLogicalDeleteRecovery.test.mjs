import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolveEffectiveChatModelId,
  runtimeReadyModels,
} from '../src/pages/agent-detail/modelSelection.mjs';

const verifiedModel = {
  id: 'verified',
  enabled: true,
  supports_tool_calling: true,
};

test('deleted saved ids cannot block a verified company model', () => {
  assert.equal(
    resolveEffectiveChatModelId({
      models: [verifiedModel],
      overrideModelId: 'deleted',
      agentPrimaryModelId: 'deleted',
      tenantDefaultModelId: 'deleted',
    }),
    'verified',
  );
});

test('failed and unverified models are not chat runtime candidates', () => {
  assert.deepEqual(
    runtimeReadyModels([
      verifiedModel,
      { id: 'failed', enabled: true, supports_tool_calling: false },
      { id: 'unverified', enabled: true, supports_tool_calling: null },
      { id: 'disabled', enabled: false, supports_tool_calling: true },
    ]).map((model) => model.id),
    ['verified'],
  );
});

test('chat remains blocked when no model passed the runtime test', () => {
  assert.equal(
    resolveEffectiveChatModelId({
      models: [{ id: 'unverified', enabled: true, supports_tool_calling: null }],
      overrideModelId: null,
      agentPrimaryModelId: 'unverified',
      tenantDefaultModelId: 'unverified',
    }),
    null,
  );
});
