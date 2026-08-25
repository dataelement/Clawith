import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const agentDetail = readFileSync(
  new URL('../src/pages/agent-detail/AgentDetailPage.tsx', import.meta.url),
  'utf8',
);
const styles = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
const historyScroll = readFileSync(
  new URL('../src/hooks/useHistoryPaginationScroll.ts', import.meta.url),
  'utf8',
);

test('direct chat keeps document scrolling separate from history pagination', () => {
  assert.doesNotMatch(agentDetail, /height:\s*'calc\(100vh - 100px\)'/);
  assert.equal(
    agentDetail.match(/className="agent-chat-message-scroll"/g)?.length,
    2,
    'read-only and writable histories must share the bounded scroll container',
  );
  assert.match(
    styles,
    /\.agent-chat-message-scroll\s*\{[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;[^}]*overscroll-behavior-y:\s*contain;/s,
  );
  assert.doesNotMatch(agentDetail, /IntersectionObserver/);
  assert.doesNotMatch(agentDetail, /agent-chat-history-sentinel/);
  assert.equal((agentDetail.match(/useOlderHistoryGesture\(/g) ?? []).length, 2);
  assert.equal((agentDetail.match(/usePrependScrollAnchor\(/g) ?? []).length, 2);
  assert.equal((agentDetail.match(/loadDirectHistoryTurn(?:<any>)?\(/g) ?? []).length, 2);
  assert.equal((agentDetail.match(/completeToolTurn: (?:historyMsgs|chatMessages)\[0\]\?\.role === 'tool_call'/g) ?? []).length, 2);
  assert.match(agentDetail, /currentAgentIdRef\.current !== targetAgentId/);
  assert.match(agentDetail, /activeSessionIdRef\.current !== String\(sess\.id\)/);
  assert.match(historyScroll, /event\.deltaY >= 0/);
  assert.match(historyScroll, /currentY - startY <= 6/);
  assert.match(historyScroll, /\['ArrowUp', 'PageUp', 'Home'\]/);
  assert.match(historyScroll, /requestInFlightRef/);
  assert.match(historyScroll, /wheelGestureLatchedRef/);
  assert.match(historyScroll, /touchPageRequestedRef/);
  assert.match(historyScroll, /event\.repeat/);
  assert.match(historyScroll, /anchor\.element\.scrollTop = anchor\.scrollTop/);
  assert.match(agentDetail, /if \(chatPrependAnchor\.isPrependingRef\.current\) return;/);
  assert.match(styles, /\.agent-chat-message-scroll\s*\{[^}]*overflow-anchor:\s*none;/s);
});
