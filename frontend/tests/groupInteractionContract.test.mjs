import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const groupsPage = readFileSync(
  new URL('../src/pages/groups/GroupsPage.tsx', import.meta.url),
  'utf8',
);
const promptModal = readFileSync(
  new URL('../src/components/PromptModal.tsx', import.meta.url),
  'utf8',
);
const toastProvider = readFileSync(
  new URL('../src/components/Toast/ToastProvider.tsx', import.meta.url),
  'utf8',
);

test('new group sessions may use the backend default title while group names stay required', () => {
  assert.match(promptModal, /allowEmpty\?: boolean/);
  assert.match(promptModal, /allowEmpty \|\| Boolean\(value\.trim\(\)\)/);
  assert.match(groupsPage, /title=\{t\('groups\.newSession'[\s\S]*?allowEmpty/);
  assert.doesNotMatch(
    groupsPage,
    /title=\{t\('groups\.create'[\s\S]*?allowEmpty[\s\S]*?onConfirm=\{\(value\) => void createGroup/,
  );
});

test('an inaccessible group route is not used as a message or member fetch scope', () => {
  assert.match(groupsPage, /isFetchedAfterMount: groupsFetchedAfterMount/);
  assert.match(groupsPage, /isRefetchError: groupsRefetchError/);
  assert.match(groupsPage, /refetchOnMount: 'always'/);
  assert.match(groupsPage, /const groupsReady = groupsFetchedAfterMount && !groupsRefetchError/);
  assert.match(groupsPage, /const activeGroup = groupsReady \?/);
  assert.match(groupsPage, /queries: \(groupsReady \? groups : \[\]\)\.map/);
  assert.match(groupsPage, /enabled: Boolean\(activeGroup\)/);
  assert.match(groupsPage, /if \(!activeGroup \|\| !activeSession\)/);
  assert.match(groupsPage, /navigate\('\/groups', \{ replace: true \}\)/);
});

test('toast context methods keep stable identities across toast renders', () => {
  assert.match(toastProvider, /useMemo/);
  assert.match(toastProvider, /const value: ToastContextValue = useMemo\(/);
  assert.match(toastProvider, /\}\), \[show\]\);/);
});

test('group composer shows explicit cancellation only for tracked cancellable runs', () => {
  assert.match(groupsPage, /trackedRunIds/);
  assert.match(groupsPage, /groupApi\.runState/);
  assert.match(groupsPage, /groupApi\.cancelRun/);
  assert.match(groupsPage, /canCancel=\{activeRunIds\.length > 0\}/);
});
