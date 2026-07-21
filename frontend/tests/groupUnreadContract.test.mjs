import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const groupsPage = readFileSync(
  new URL('../src/pages/groups/GroupsPage.tsx', import.meta.url),
  'utf8',
);
const messageStream = readFileSync(
  new URL('../src/pages/groups/MessageStream.tsx', import.meta.url),
  'utf8',
);
const groupRealtime = readFileSync(
  new URL('../src/hooks/useGroupRealtime.ts', import.meta.url),
  'utf8',
);

test('group sessions are marked read only after the latest message is visibly reached', () => {
  assert.doesNotMatch(groupsPage, /const lastMessageId[\s\S]{0,800}setTimeout/);
  assert.match(messageStream, /onLatestMessageSeen/);
  assert.match(messageStream, /document\.visibilityState !== 'visible'/);
  assert.match(messageStream, /document\.hasFocus\(\)/);
  assert.match(groupsPage, /markLatestMessageSeen/);
  assert.doesNotMatch(groupsPage, /session\.unread_count > 0 && session\.id !== sessionId/);
});

test('realtime group activity carries the committed message for exact cache increments', () => {
  assert.match(groupRealtime, /onGroupActivity\?: \(activity: GroupActivity\) => void/);
  assert.match(groupRealtime, /sessionId: payload\.session_id/);
  assert.match(groupRealtime, /message: payload\.message/);
  assert.match(groupsPage, /unread_count: session\.unread_count \+ 1/);
  assert.match(groupsPage, /participant_id !== me\?\.participant_id/);
});

test('a trailing authoritative refresh reconciles burst realtime updates', () => {
  assert.match(groupsPage, /groupActivityRefreshTimerRef/);
  assert.match(groupsPage, /invalidateQueries\(\{[\s\S]*?queryKey: \['group-sessions', activityGroupId\]/);
});

test('an older read response cannot clear a newer unseen realtime message', () => {
  assert.match(groupsPage, /latestRealtimeMessageBySessionRef/);
  assert.match(
    groupsPage,
    /const latestRealtimeMessageId = latestRealtimeMessageBySessionRef\.current[\s\S]*?\.get\(targetSessionId\)\?\.id/,
  );
  assert.match(
    groupsPage,
    /latestRealtimeMessageId === undefined[\s\S]*?readState\.last_read_message_id === latestRealtimeMessageId[\s\S]*?unread_count: 0/,
  );
});

test('active group runs render planning and named agent animations without tool details', () => {
  assert.match(messageStream, /isPlanning: boolean/);
  assert.match(messageStream, /runningAgents: Array<\{ id: string; name: string \}>/);
  assert.match(messageStream, /group-run-indicator/);
  assert.match(messageStream, /任务规划中/);
  assert.match(messageStream, /\{agent\.name\}/);
  assert.equal((messageStream.match(/className="group-badge-agent"/g) ?? []).length, 2);
  assert.doesNotMatch(messageStream, /\{\{name\}\}运行中/);
  assert.doesNotMatch(messageStream, /tool_call|toolName|toolResult/);
});

test('agent group messages pass structured mention names into markdown rendering', () => {
  const markdownRenderer = readFileSync(
    new URL('../src/components/MarkdownRenderer.tsx', import.meta.url),
    'utf8',
  );
  assert.match(messageStream, /mentionNames=\{message\.mentions/);
  assert.match(markdownRenderer, /mentionNames\?: readonly string\[\]/);
  assert.match(markdownRenderer, /class="group-mention-chip"/);
});
