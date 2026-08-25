import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const groupsPage = readFileSync(
  new URL('../src/pages/groups/GroupsPage.tsx', import.meta.url),
  'utf8',
);

test('group workspace reconciliation is offered only for actionable workspace candidates', () => {
  assert.match(
    groupsPage,
    /pending\.can_reconcile && pending\.workspace_resolution/,
  );
  assert.match(groupsPage, /run\.correlation_id/);
  assert.match(
    groupsPage,
    /tool-executions\/\$\{pending\.execution_id\}\/reconcile/,
  );
});

test('group workspace reconciliation uses the approved copy and decisions', () => {
  assert.match(groupsPage, /文件内容有变化/);
  assert.match(
    groupsPage,
    /Agent 处理后的文件与工作区中的源文件不同。请选择要保留哪一个。/,
  );
  assert.match(groupsPage, /保留源文件/);
  assert.match(groupsPage, /使用 Agent 的结果/);
  assert.match(groupsPage, /'not_applied'/);
  assert.match(groupsPage, /'applied'/);
});
