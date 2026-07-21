import assert from 'node:assert/strict';
import test from 'node:test';

import { createVersionedFileAdapter } from '../src/pages/groups/versionedFileAdapter.ts';
import {
  GroupWorkspaceUploadError,
  groupWorkspaceUploadPath,
  readGroupWorkspaceTextUpload,
} from '../src/pages/groups/groupWorkspaceUpload.ts';

test('writes and deletes with the token captured when the file was read', async () => {
  const calls = [];
  const adapter = createVersionedFileAdapter({
    async read(path) {
      assert.equal(path, 'plan.md');
      return { content: 'original', version_token: 'version-1' };
    },
    async write(path, content, expectedVersionToken, requireAbsent) {
      calls.push(['write', path, content, expectedVersionToken, requireAbsent]);
      return { content, version_token: 'version-2' };
    },
    async delete(path, expectedVersionToken) {
      calls.push(['delete', path, expectedVersionToken]);
    },
  });

  assert.deepEqual(await adapter.read('plan.md'), { content: 'original' });
  await adapter.write('plan.md', 'my edit');
  await adapter.delete('plan.md');

  assert.deepEqual(calls, [
    ['write', 'plan.md', 'my edit', 'version-1', false],
    ['delete', 'plan.md', 'version-2'],
  ]);
});

test('a second save uses the token returned by the first save without a hidden reread', async () => {
  const expectedTokens = [];
  let nextVersion = 2;
  const adapter = createVersionedFileAdapter({
    async read() {
      return { content: 'original', version_token: 'version-1' };
    },
    async write(_path, content, expectedVersionToken) {
      expectedTokens.push(expectedVersionToken);
      return { content, version_token: `version-${nextVersion++}` };
    },
    async delete() {},
  });

  await adapter.read('memory.md');
  await adapter.write('memory.md', 'first edit');
  await adapter.write('memory.md', 'second edit');

  assert.deepEqual(expectedTokens, ['version-1', 'version-2']);
});

test('version snapshots distinguish a listed token from a path absent in the loaded directory', () => {
  const adapter = createVersionedFileAdapter({
    async read() { return { content: '', version_token: null }; },
    async write(_path, content) { return { content, version_token: 'v1' }; },
    async delete() {},
  });

  adapter.remember('existing.md', 'version-1');

  assert.deepEqual(adapter.snapshot('existing.md'), { known: true, versionToken: 'version-1' });
  assert.deepEqual(adapter.snapshot('new.md'), { known: false, versionToken: null });
});

test('a new file write uses create-only protection until the backend returns its first token', async () => {
  const conditions = [];
  const adapter = createVersionedFileAdapter({
    async read() { return { content: '', version_token: null }; },
    async write(_path, content, expectedVersionToken, requireAbsent) {
      conditions.push([expectedVersionToken, requireAbsent]);
      return { content, version_token: 'created-v1' };
    },
    async delete() {},
  });

  await adapter.write('new.md', 'created');
  await adapter.write('new.md', 'updated');

  assert.deepEqual(conditions, [
    [null, true],
    ['created-v1', false],
  ]);
});

test('group workspace uploads stay in the current directory and decode exact UTF-8 text', async () => {
  assert.equal(groupWorkspaceUploadPath('reports/weekly', 'summary.md'), 'reports/weekly/summary.md');
  const content = await readGroupWorkspaceTextUpload({
    async arrayBuffer() {
      return new TextEncoder().encode('# 周报\n').buffer;
    },
  });
  assert.equal(content, '# 周报\n');
});

test('group workspace upload rejects traversal, binary extensions, and invalid UTF-8', async () => {
  assert.throws(
    () => groupWorkspaceUploadPath('reports', '../secret.md'),
    (error) => error instanceof GroupWorkspaceUploadError && error.code === 'invalid_name',
  );
  assert.throws(
    () => groupWorkspaceUploadPath('', 'archive.zip'),
    (error) => error instanceof GroupWorkspaceUploadError && error.code === 'unsupported_type',
  );
  await assert.rejects(
    readGroupWorkspaceTextUpload({
      async arrayBuffer() {
        return Uint8Array.from([0xff, 0xfe, 0xfd]).buffer;
      },
    }),
    (error) => error instanceof GroupWorkspaceUploadError && error.code === 'invalid_utf8',
  );
});
