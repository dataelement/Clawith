import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outdir = path.resolve(__dirname, '../.tmp-tests');
const outfile = path.join(outdir, 'enterpriseOrgData.mjs');

await mkdir(outdir, { recursive: true });
await build({
  entryPoints: [path.resolve(__dirname, '../src/pages/enterprise-settings/utils/orgData.ts')],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile,
  logLevel: 'silent',
});

const {
  normalizeDepartmentItems,
  getDepartmentTotalMembers,
  normalizeMembersResponse,
} = await import(pathToFileURL(outfile).href);

test('normalizes legacy array and paged department payloads into a department array', () => {
  const legacy = [{ id: 'dept-1', name: 'Sales' }];
  const paged = { items: [{ id: 'dept-2', name: 'Support' }], total_member: 7 };

  assert.deepEqual(normalizeDepartmentItems(legacy), legacy);
  assert.deepEqual(normalizeDepartmentItems(paged), paged.items);
  assert.deepEqual(normalizeDepartmentItems(null), []);
  assert.deepEqual(normalizeDepartmentItems({ items: null }), []);
});

test('preserves department total counts only when present in the object payload', () => {
  assert.equal(getDepartmentTotalMembers({ items: [], total_member: 12 }), 12);
  assert.equal(getDepartmentTotalMembers([{ id: 'dept-1' }]), null);
  assert.equal(getDepartmentTotalMembers(null), null);
});

test('defensively normalizes members responses to arrays', () => {
  const members = [{ id: 'member-1', name: 'Ada' }];

  assert.deepEqual(normalizeMembersResponse(members), members);
  assert.deepEqual(normalizeMembersResponse({ items: members }), []);
  assert.deepEqual(normalizeMembersResponse(undefined), []);
});
