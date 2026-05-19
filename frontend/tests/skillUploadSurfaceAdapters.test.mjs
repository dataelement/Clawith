import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outdir = path.resolve(__dirname, '../.tmp-tests');
const outfile = path.join(outdir, 'skillUploadSurfaceAdapters.mjs');

await mkdir(outdir, { recursive: true });
await build({
  entryPoints: [path.resolve(__dirname, '../src/components/skills/skillUploadSurfaceAdapters.ts')],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile,
  logLevel: 'silent',
});

const {
  createEnterpriseSkillUploadAdapter,
  createAgentSkillUploadAdapter,
} = await import(pathToFileURL(outfile).href);

test('enterprise upload surface adapter delegates preview, apply, and refresh', async () => {
  const previewResult = { kind: 'preview-result' };
  const applyResult = { kind: 'apply-result' };
  const calls = [];
  const file = new File(['zip'], 'skills.zip', { type: 'application/zip' });
  const applyInput = {
    file,
    targetFolder: 'enterprise-skills',
    expectedDigest: 'digest-1',
    expectedTargetStateDigest: 'target-digest-1',
    replaceConfirmed: true,
  };

  const adapter = createEnterpriseSkillUploadAdapter({
    preview: async (...args) => {
      calls.push(['preview', ...args]);
      return previewResult;
    },
    apply: async (input) => {
      calls.push(['apply', input]);
      return applyResult;
    },
    refresh: async () => {
      calls.push(['refresh']);
    },
  });

  const previewed = await adapter.previewRequest(file, 'enterprise-skills');
  const applied = await adapter.applyRequest(applyInput);
  await adapter.onApplied(applyResult);

  assert.equal(previewed, previewResult);
  assert.equal(applied, applyResult);
  assert.deepEqual(calls, [
    ['preview', file, 'enterprise-skills'],
    ['apply', applyInput],
    ['refresh'],
  ]);
});

test('agent upload surface adapter delegates preview, apply, and waits for refresh', async () => {
  const previewResult = { kind: 'preview-result' };
  const applyResult = { kind: 'apply-result' };
  const calls = [];
  const file = new File(['zip'], 'skills.zip', { type: 'application/zip' });
  const applyInput = {
    file,
    targetFolder: 'agent-skills',
    expectedDigest: 'digest-2',
    expectedTargetStateDigest: 'target-digest-2',
    replaceConfirmed: true,
  };
  let refreshResolved = false;

  const adapter = createAgentSkillUploadAdapter({
    preview: async (...args) => {
      calls.push(['preview', ...args]);
      return previewResult;
    },
    apply: async (input) => {
      calls.push(['apply', input]);
      return applyResult;
    },
    refresh: async () => {
      await Promise.resolve();
      refreshResolved = true;
      calls.push(['refresh']);
    },
  });

  const previewed = await adapter.previewRequest(file, 'agent-skills');
  const applied = await adapter.applyRequest(applyInput);
  await adapter.onApplied(applyResult);

  assert.equal(previewed, previewResult);
  assert.equal(applied, applyResult);
  assert.equal(refreshResolved, true);
  assert.deepEqual(calls, [
    ['preview', file, 'agent-skills'],
    ['apply', applyInput],
    ['refresh'],
  ]);
});
