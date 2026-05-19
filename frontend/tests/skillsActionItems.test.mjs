import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outdir = path.resolve(__dirname, '../.tmp-tests');
const outfile = path.join(outdir, 'skillsActionItems.mjs');

await mkdir(outdir, { recursive: true });
await build({
  entryPoints: [path.resolve(__dirname, '../src/components/skills/skillsActionItems.ts')],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile,
  logLevel: 'silent',
});

const { getEnterpriseSkillActionIds, getAgentSkillActionIds } = await import(pathToFileURL(outfile).href);

test('getEnterpriseSkillActionIds returns the locked enterprise action order', () => {
  assert.deepEqual(getEnterpriseSkillActionIds(), [
    'settings',
    'upload-folder',
    'import-url',
    'browse-clawhub',
  ]);
});

test('getAgentSkillActionIds returns the locked agent action order', () => {
  assert.deepEqual(getAgentSkillActionIds(), [
    'browse-clawhub',
    'import-presets',
    'upload-folder',
  ]);
});
