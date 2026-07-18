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
const composeCi = readFileSync(
  new URL('../../docker-compose.ci.yml', import.meta.url),
  'utf8',
);
const deployCompose = readFileSync(
  new URL('../../deploy/docker-compose.yml', import.meta.url),
  'utf8',
);
const deployMultiCompose = readFileSync(
  new URL('../../deploy/docker-compose-multi.yml', import.meta.url),
  'utf8',
);
const rootEnvExample = readFileSync(
  new URL('../../.env.example', import.meta.url),
  'utf8',
);
const deployEnvExample = readFileSync(
  new URL('../../deploy/.env.example', import.meta.url),
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

test('supported compose deployments always enable the durable Runtime', () => {
  for (const [source, serviceCount] of [
    [compose, 1],
    [composeCi, 1],
    [deployCompose, 1],
    [deployMultiCompose, 2],
  ]) {
    assert.equal(
      source.match(/AGENT_RUNTIME_V2_ENABLED: ["']true["']/g)?.length,
      serviceCount,
    );
    assert.equal(
      source.match(/AGENT_RUNTIME_V2_AGENT_IDS: (?:""|'')/g)?.length,
      serviceCount,
    );
    assert.equal(
      source.match(/AGENT_RUNTIME_V2_SOURCE_TYPES: (?:""|'')/g)?.length,
      serviceCount,
    );
    assert.doesNotMatch(source, /AGENT_RUNTIME_V2_[A-Z_]+: \$\{/);
  }

  for (const source of [rootEnvExample, deployEnvExample]) {
    assert.doesNotMatch(source, /^AGENT_RUNTIME_V2_[A-Z_]+=/m);
  }
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
