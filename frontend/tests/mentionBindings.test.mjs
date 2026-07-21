import assert from 'node:assert/strict';
import test from 'node:test';

import {
  liveMentionParticipantIds,
  liveMentionedAgentCount,
  mentionReplacementEnd,
  reconcileMentionBindings,
  replaceMentionBinding,
} from '../src/pages/groups/mentionBindings.ts';

const binding = (participantId, start, text = '@Ann', participantType = 'agent') => ({
  participantId,
  participantType,
  start,
  end: start + text.length,
  text,
});

test('same-name mentions keep the participant picked for each exact occurrence', () => {
  const previous = '@Ann @Ann';
  const bindings = [binding('agent-a', 0), binding('agent-b', 5)];

  const remaining = reconcileMentionBindings(previous, '@Ann', bindings, {
    start: 0,
    end: 5,
    inputType: 'deleteByCut',
  });

  assert.deepEqual(liveMentionParticipantIds('@Ann', remaining), ['agent-b']);
  assert.deepEqual(remaining.map(({ participantId, start }) => ({ participantId, start })), [
    { participantId: 'agent-b', start: 0 },
  ]);
});

test('editing Ann into Anna invalidates only the edited mention', () => {
  const previous = '@Ann @Anna';
  const bindings = [
    binding('agent-ann', 0),
    binding('agent-anna', 5, '@Anna'),
  ];

  const next = '@Annx @Anna';
  const remaining = reconcileMentionBindings(previous, next, bindings, {
    start: 4,
    end: 4,
    inputType: 'insertText',
  });

  assert.deepEqual(liveMentionParticipantIds(next, remaining), ['agent-anna']);
});

test('reselecting an existing same-name token replaces its old participant identity', () => {
  const value = '@Ann ';
  const previous = [binding('agent-a', 0)];
  const replacementEnd = mentionReplacementEnd(value, previous, 0, 2);

  const reselected = replaceMentionBinding(
    value,
    value,
    previous,
    0,
    replacementEnd,
    binding('agent-b', 0),
  );

  assert.deepEqual(liveMentionParticipantIds(value, reselected), ['agent-b']);
  assert.equal(reselected.length, 1);
  assert.equal(replacementEnd, 4);
});

test('punctuation ends a mention while name characters extend and invalidate it', () => {
  const selected = [binding('agent-ann', 0)];

  assert.deepEqual(liveMentionParticipantIds('@Ann, please', selected), ['agent-ann']);
  assert.deepEqual(liveMentionParticipantIds('@Anna please', selected), []);
  assert.deepEqual(liveMentionParticipantIds('@Ann王 please', selected), []);
});

test('edits around an intact mention move its range without changing participant identity', () => {
  const previous = 'ask @Ann now';
  const bindings = [binding('stable-agent-id', 4)];
  const next = 'please ask @Ann now';

  const remaining = reconcileMentionBindings(previous, next, bindings, {
    start: 0,
    end: 0,
    inputType: 'insertText',
  });

  assert.deepEqual(liveMentionParticipantIds(next, remaining), ['stable-agent-id']);
  assert.equal(remaining[0].start, 11);
});

test('agent renames do not alter an already selected stable participant id', () => {
  const value = '@OldName please help';
  const selectedBeforeRename = [binding('stable-agent-id', 0, '@OldName')];

  assert.deepEqual(liveMentionParticipantIds(value, selectedBeforeRename), ['stable-agent-id']);
});

test('handwritten, pasted, deleted, or reconstructed display names do not gain identity', () => {
  assert.deepEqual(liveMentionParticipantIds('@Ann', []), []);

  const selected = [binding('agent-ann', 0)];
  const afterCut = reconcileMentionBindings('@Ann says hi', ' says hi', selected, {
    start: 0,
    end: 4,
    inputType: 'deleteByCut',
  });
  const afterPaste = reconcileMentionBindings(' says hi', '@Ann says hi', afterCut, {
    start: 0,
    end: 0,
    inputType: 'insertFromPaste',
  });

  assert.deepEqual(liveMentionParticipantIds('@Ann says hi', afterPaste), []);
});

test('routing and planning count each participant once even when selected repeatedly', () => {
  const value = '@Ann @Ann';
  const bindings = [binding('agent-ann', 0), binding('agent-ann', 5)];

  assert.deepEqual(liveMentionParticipantIds(value, bindings), ['agent-ann']);
  assert.equal(liveMentionedAgentCount(value, bindings), 1);
});
