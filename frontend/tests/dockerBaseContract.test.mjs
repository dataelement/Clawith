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
