import assert from 'node:assert/strict';
import test from 'node:test';

import { createVersionedFileAdapter } from '../src/pages/groups/versionedFileAdapter.ts';

test('writes and deletes with the token captured when the file was read', async () => {
  const calls = [];
  const adapter = createVersionedFileAdapter({
    async read(path) {
      assert.equal(path, 'plan.md');
      return { content: 'original', version_token: 'version-1' };
    },
    async write(path, content, expectedVersionToken) {
      calls.push(['write', path, content, expectedVersionToken]);
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
    ['write', 'plan.md', 'my edit', 'version-1'],
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
