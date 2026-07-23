import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const editor = readFileSync(
  new URL('../src/pages/groups/GroupTextFileEditor.tsx', import.meta.url),
  'utf8',
);

function draftAfterCachedMount(source, cachedContent) {
  const effectStart = source.indexOf('const { data, isLoading, error, refetch }');
  const effectEnd = source.indexOf('const commit = async');
  const effects = source.slice(effectStart, effectEnd);
  const hydrateIndex = effects.indexOf('if (data && !dirty) setDraft(data.content);');
  const resetIndex = effects.indexOf("setDraft('');");

  assert.notEqual(hydrateIndex, -1, 'cached-data hydration effect is missing');
  assert.notEqual(resetIndex, -1, 'query-key reset effect is missing');

  let draft = '';
  const mountUpdates = [
    { index: hydrateIndex, apply: () => { draft = cachedContent; } },
    { index: resetIndex, apply: () => { draft = ''; } },
  ].sort((left, right) => left.index - right.index);

  for (const update of mountUpdates) update.apply();
  return draft;
}

test('cached group announcement remains visible when its tab remounts', () => {
  const cachedContent = 'cached group announcement';

  // React runs mount effects in declaration order. A structurally equal refetch keeps the cached
  // data reference, so this mount sequence is the only chance to hydrate the controlled textarea.
  assert.equal(draftAfterCachedMount(editor, cachedContent), cachedContent);
});
