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
const groupUnread = readFileSync(
  new URL('../src/hooks/useGroupUnread.ts', import.meta.url),
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

test('name prompts do not submit while an IME is still composing text', () => {
  assert.match(promptModal, /e\.nativeEvent\.isComposing/);
  assert.match(
    promptModal,
    /if \(e\.nativeEvent\.isComposing\) return;[\s\S]*?if \(e\.key === 'Enter'\) \{[\s\S]*?e\.preventDefault\(\);[\s\S]*?confirm\(\);/,
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
  assert.match(groupsPage, /groupId: activeGroup\?\.id/);
  assert.match(groupsPage, /sessionId: activeSession\?\.id/);
  assert.match(groupUnread, /const groupsReady = isFetchedAfterMount && !isRefetchError/);
  assert.match(groupUnread, /queries: \(groupsReady \? groups : \[\]\)\.map/);
  assert.match(groupsPage, /navigate\('\/groups', \{ replace: true \}\)/);
});

test('session metadata refresh does not clear and reload the visible message stream', () => {
  assert.match(groupsPage, /const activeGroupId = activeGroup\?\.id/);
  assert.match(groupsPage, /const activeSessionId = activeSession\?\.id/);
  assert.match(
    groupsPage,
    /groupApi[\s\S]*?\.messages\(activeGroupId, activeSessionId,[\s\S]*?\}, \[activeGroupId, activeSessionId, toast, t\]\);/,
  );
  assert.doesNotMatch(
    groupsPage,
    /\}, \[activeGroup, activeSession, groupId, sessionId, toast, t\]\);/,
  );
});

test('toast context methods keep stable identities across toast renders', () => {
  assert.match(toastProvider, /useMemo/);
  assert.match(toastProvider, /const value: ToastContextValue = useMemo\(/);
  assert.match(toastProvider, /\}\), \[show\]\);/);
});

test('group composer and stream use session-wide active runs', () => {
  assert.match(groupsPage, /groupApi\.activeRuns/);
  assert.match(groupsPage, /\['group-active-runs', groupId, sessionId\]/);
  assert.match(groupsPage, /groupApi\.cancelRun/);
  assert.match(groupsPage, /canCancel=\{activeRunIds\.length > 0\}/);
  assert.match(groupsPage, /run\.system_role === 'group_planning'/);
  assert.match(groupsPage, /member\.participant_ref_id/);
  assert.match(groupsPage, /name: member\.display_name/);
  assert.match(groupsPage, /isPlanning=\{isPlanning\}/);
  assert.match(groupsPage, /runningAgents=\{runningAgents\}/);
});
