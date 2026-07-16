import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const dockerfile = readFileSync(
  new URL('../Dockerfile', import.meta.url),
  'utf8',
);
const compose = readFileSync(
  new URL('../../docker-compose.yml', import.meta.url),
  'utf8',
);

test('frontend pins the nginx build verified on the 3010 host', () => {
  assert.match(
    dockerfile,
    /FROM nginx:1\.31\.2-alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa/,
  );
  assert.doesNotMatch(dockerfile, /^FROM nginx:alpine$/m);
});

test('backend receives the configured Group planning and compact model ids', () => {
  assert.match(
    compose,
    /MULTI_AGENT_PLANNING_MODEL_ID: \$\{MULTI_AGENT_PLANNING_MODEL_ID:-\}/,
  );
  assert.match(
    compose,
    /MULTI_AGENT_COMPACT_MODEL_ID: \$\{MULTI_AGENT_COMPACT_MODEL_ID:-\}/,
  );
});

test('backend receives the durable Runtime rollout policy', () => {
  assert.match(
    compose,
    /AGENT_RUNTIME_V2_ENABLED: \$\{AGENT_RUNTIME_V2_ENABLED:-false\}/,
  );
  assert.match(
    compose,
    /AGENT_RUNTIME_V2_AGENT_IDS: \$\{AGENT_RUNTIME_V2_AGENT_IDS:-\}/,
  );
  assert.match(
    compose,
    /AGENT_RUNTIME_V2_SOURCE_TYPES: \$\{AGENT_RUNTIME_V2_SOURCE_TYPES:-task\}/,
  );
});

test('compose can isolate sibling deployments on the same Docker host', () => {
  assert.match(
    compose,
    /DOCKER_NETWORK: \$\{CLAWITH_DOCKER_NETWORK:-clawith_network\}/,
  );
  assert.match(
    compose,
    /name: \$\{CLAWITH_DOCKER_NETWORK:-clawith_network\}/,
  );
});
