import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outdir = path.resolve(__dirname, '../.tmp-tests');
const outfile = path.join(outdir, 'skillsActionItems.mjs');
const enterpriseSkillsTabPath = path.resolve(__dirname, '../src/pages/enterprise-settings/tabs/SkillsTab.tsx');

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
const enterpriseSkillsTabSource = await readFile(enterpriseSkillsTabPath, 'utf8');
const normalizedEnterpriseSkillsTabSource = enterpriseSkillsTabSource.replace(/\s+/g, ' ');

test('getEnterpriseSkillActionIds returns the locked enterprise action order', () => {
  assert.deepEqual(getEnterpriseSkillActionIds(), [
    'settings',
    'upload-folder',
    'import-url',
    'browse-clawhub',
  ]);
});

test('enterprise actions keep settings, folder upload, URL import, then ClawHub in sequence', () => {
  const actions = getEnterpriseSkillActionIds();

  assert.ok(actions.indexOf('settings') < actions.indexOf('upload-folder'));
  assert.ok(actions.indexOf('upload-folder') < actions.indexOf('import-url'));
  assert.ok(actions.indexOf('import-url') < actions.indexOf('browse-clawhub'));
});

test('enterprise skills tab is wired to the shared action and upload seams', () => {
  assert.ok(normalizedEnterpriseSkillsTabSource.includes("import SkillsActionBar, { type SkillsActionBarAction } from '../../../components/skills/SkillsActionBar';"));
  assert.ok(normalizedEnterpriseSkillsTabSource.includes("import { createEnterpriseSkillUploadAdapter } from '../../../components/skills/skillUploadSurfaceAdapters';"));
  assert.ok(normalizedEnterpriseSkillsTabSource.includes("getEnterpriseSkillActionIds() .map((id) => actionConfig[id])") || normalizedEnterpriseSkillsTabSource.includes("getEnterpriseSkillActionIds().map((id) => actionConfig[id])"));
  assert.ok(normalizedEnterpriseSkillsTabSource.includes('previewRequest={uploadAdapter.previewRequest}'));
  assert.ok(normalizedEnterpriseSkillsTabSource.includes('applyRequest={uploadAdapter.applyRequest}'));
  assert.ok(normalizedEnterpriseSkillsTabSource.includes('onApplied={uploadAdapter.onApplied}'));
});

test('getAgentSkillActionIds returns the locked agent action order', () => {
  assert.deepEqual(getAgentSkillActionIds(), [
    'browse-clawhub',
    'import-presets',
    'upload-folder',
  ]);
});
