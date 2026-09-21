import assert from 'node:assert/strict';
import test from 'node:test';
import { loadDirectHistoryTurn } from '../src/services/directHistoryPagination.ts';

function row(index, role) {
  return { id: String(index), role, cursor: String(index), created_at: String(index) };
}

function pagedFetcher(allRows, pageSize) {
  return async (before) => {
    const end = Number(before);
    return allRows.slice(Math.max(0, end - pageSize), end);
  };
}

test('ordinary Direct history loads one backend page', async () => {
  const rows = Array.from({ length: 60 }, (_, index) => row(index, index % 2 ? 'assistant' : 'user'));
  const page = await loadDirectHistoryTurn({
    before: '60',
    pageSize: 20,
    completeToolTurn: false,
    fetchPage: pagedFetcher(rows, 20),
  });

  assert.equal(page.requestCount, 1);
  assert.equal(page.rows.length, 20);
  assert.equal(page.oldestCursor, '40');
  assert.equal(page.hasMore, true);
});

test('one history gesture completes a 170-call folded Tool turn before publishing rows', async () => {
  const allRows = [
    row(0, 'user'),
    ...Array.from({ length: 170 }, (_, index) => row(index + 1, 'tool_call')),
    row(171, 'assistant'),
    row(172, 'assistant'),
  ];
  const initialRows = allRows.slice(-20);
  const page = await loadDirectHistoryTurn({
    before: initialRows[0].cursor,
    pageSize: 20,
    completeToolTurn: initialRows[0].role === 'tool_call',
    fetchPage: pagedFetcher(allRows, 20),
  });

  assert.equal(page.requestCount, 8);
  assert.equal(page.rows.length, 153);
  assert.equal(page.rows[0].role, 'user');
  assert.equal(page.rows.at(-1).cursor, '152');
  assert.equal(page.oldestCursor, '0');
  assert.equal(page.hasMore, false);
  assert.deepEqual(
    [...page.rows, ...initialRows].map((item) => item.cursor),
    allRows.map((item) => item.cursor),
  );
});

test('a completed Tool turn does not leak partial rows from the preceding turn', async () => {
  const allRows = [
    row(0, 'user'),
    ...Array.from({ length: 5 }, (_, index) => row(index + 1, 'tool_call')),
    row(6, 'assistant'),
    row(7, 'user'),
    ...Array.from({ length: 33 }, (_, index) => row(index + 8, 'tool_call')),
  ];
  const page = await loadDirectHistoryTurn({
    before: '41',
    pageSize: 20,
    completeToolTurn: true,
    fetchPage: pagedFetcher(allRows, 20),
  });

  assert.equal(page.rows[0].cursor, '7');
  assert.equal(page.rows[0].role, 'user');
  assert.equal(page.rows.some((item) => Number(item.cursor) < 7), false);
  assert.equal(page.oldestCursor, '7');
  assert.equal(page.hasMore, true);
});

test('Tool turn continuation fails closed when a backend cursor stalls', async () => {
  await assert.rejects(
    loadDirectHistoryTurn({
      before: 'same',
      pageSize: 1,
      completeToolTurn: true,
      fetchPage: async () => [{ role: 'tool_call', cursor: 'same' }],
    }),
    /cursor did not advance/,
  );
});

test('maximum-size Tool turn preserves order and does not mutate fetched pages', async () => {
  const allRows = Array.from({ length: 5000 }, (_, index) =>
    Object.freeze(row(index, index === 0 ? 'user' : 'tool_call')));
  const page = await loadDirectHistoryTurn({
    before: '5000',
    pageSize: 20,
    completeToolTurn: true,
    fetchPage: async (before) => {
      const end = Number(before);
      return Object.freeze(allRows.slice(Math.max(0, end - 20), end));
    },
  });

  assert.equal(page.requestCount, 250);
  assert.deepEqual(page.rows, allRows);
  assert.equal(page.oldestCursor, '0');
  assert.equal(page.hasMore, true);
});

for (const length of [20, 23]) {
  test(`Tool history with ${length} rows ends on an empty or short page`, async () => {
    const allRows = Array.from({ length }, (_, index) => row(index, 'tool_call'));
    const page = await loadDirectHistoryTurn({
      before: String(length),
      pageSize: 10,
      completeToolTurn: true,
      fetchPage: pagedFetcher(allRows, 10),
    });

    assert.deepEqual(page.rows, allRows);
    assert.equal(page.requestCount, 3);
    assert.equal(page.oldestCursor, '0');
    assert.equal(page.hasMore, false);
  });
}

test('an incomplete Tool turn does not publish rows when its request budget is exhausted', async () => {
  let requests = 0;
  await assert.rejects(loadDirectHistoryTurn({
    before: '21',
    pageSize: 10,
    completeToolTurn: true,
    maxContinuationPages: 2,
    fetchPage: async (before) => {
      requests += 1;
      const end = Number(before);
      return Array.from({ length: 10 }, (_, index) => row(end - 10 + index, 'tool_call'));
    },
  }), /exceeded 2 continuation pages/);
  assert.equal(requests, 2);
});
