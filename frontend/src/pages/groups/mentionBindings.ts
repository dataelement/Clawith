import type { ParticipantType } from '../../types/group';

/**
 * Identity attached to one mention selected from the composer dropdown.
 *
 * The displayed text is only a snapshot. `participantId` is the routing identity, while the
 * offsets let the textarea keep that identity attached to the exact occurrence the user picked.
 */
export interface MentionBinding {
    participantId: string;
    participantType: ParticipantType;
    start: number;
    end: number;
    text: string;
}

/** The selection and input operation captured immediately before a textarea change. */
export interface MentionEditHint {
    start: number;
    end: number;
    inputType: string;
}

interface TextEdit {
    start: number;
    oldEnd: number;
    insertedLength: number;
}

const BACKWARD_DELETE_TYPES = new Set([
    'deleteContentBackward',
    'deleteWordBackward',
    'deleteSoftLineBackward',
    'deleteHardLineBackward',
]);

const FORWARD_DELETE_TYPES = new Set([
    'deleteContentForward',
    'deleteWordForward',
    'deleteSoftLineForward',
    'deleteHardLineForward',
]);

function editFromHint(
    previousValue: string,
    nextValue: string,
    hint: MentionEditHint,
): TextEdit | null {
    let start = hint.start;
    let oldEnd = hint.end;

    if (start < 0 || oldEnd < start || oldEnd > previousValue.length) return null;

    if (start === oldEnd && BACKWARD_DELETE_TYPES.has(hint.inputType)) {
        const deletedLength = previousValue.length - nextValue.length;
        if (deletedLength < 0) return null;
        start = Math.max(0, start - deletedLength);
    } else if (start === oldEnd && FORWARD_DELETE_TYPES.has(hint.inputType)) {
        const deletedLength = previousValue.length - nextValue.length;
        if (deletedLength < 0) return null;
        oldEnd = Math.min(previousValue.length, oldEnd + deletedLength);
    }

    const insertedLength = nextValue.length - (previousValue.length - (oldEnd - start));
    if (insertedLength < 0) return null;

    // A stale or browser-specific beforeinput hint must never move identity to a lookalike token.
    if (nextValue.slice(0, start) !== previousValue.slice(0, start)) return null;
    if (nextValue.slice(start + insertedLength) !== previousValue.slice(oldEnd)) return null;

    return { start, oldEnd, insertedLength };
}

function inferSingleEdit(previousValue: string, nextValue: string): TextEdit {
    let start = 0;
    const sharedLength = Math.min(previousValue.length, nextValue.length);
    while (start < sharedLength && previousValue[start] === nextValue[start]) start += 1;

    let oldEnd = previousValue.length;
    let nextEnd = nextValue.length;
    while (
        oldEnd > start
        && nextEnd > start
        && previousValue[oldEnd - 1] === nextValue[nextEnd - 1]
    ) {
        oldEnd -= 1;
        nextEnd -= 1;
    }

    return { start, oldEnd, insertedLength: nextEnd - start };
}

function isLiveBinding(value: string, binding: MentionBinding): boolean {
    if (binding.start < 0 || binding.end > value.length || binding.start >= binding.end) return false;
    if (value.slice(binding.start, binding.end) !== binding.text) return false;

    // Prevent `@Ann` from staying bound after it is edited into `@Anna` (or `x@Ann`). A trailing
    // punctuation mark is a valid boundary, so messages such as `@Ann, please` keep their identity.
    const before = value[binding.start - 1];
    const after = value[binding.end];
    const continuesName = after !== undefined && /[\p{L}\p{N}\p{M}_@]/u.test(after);
    return (before === undefined || /\s/.test(before)) && !continuesName;
}

/**
 * Move intact bindings across one textarea edit and discard every binding the edit touched.
 * Deleted, rewritten, cut-and-pasted, or otherwise reconstructed text never inherits identity.
 */
export function reconcileMentionBindings(
    previousValue: string,
    nextValue: string,
    bindings: MentionBinding[],
    hint?: MentionEditHint | null,
): MentionBinding[] {
    if (previousValue === nextValue) {
        return bindings.filter((binding) => isLiveBinding(nextValue, binding));
    }

    const edit = (hint && editFromHint(previousValue, nextValue, hint))
        || inferSingleEdit(previousValue, nextValue);
    const removedLength = edit.oldEnd - edit.start;
    const delta = edit.insertedLength - removedLength;

    return bindings.flatMap((binding) => {
        if (!isLiveBinding(previousValue, binding)) return [];

        let nextBinding = binding;
        if (removedLength === 0) {
            if (edit.start <= binding.start) {
                nextBinding = {
                    ...binding,
                    start: binding.start + delta,
                    end: binding.end + delta,
                };
            } else if (edit.start < binding.end) {
                return [];
            }
        } else if (edit.oldEnd <= binding.start) {
            nextBinding = {
                ...binding,
                start: binding.start + delta,
                end: binding.end + delta,
            };
        } else if (edit.start < binding.end) {
            return [];
        }

        return isLiveBinding(nextValue, nextBinding) ? [nextBinding] : [];
    });
}

/** Replace the identity attached to the query range with the newly selected dropdown member. */
export function replaceMentionBinding(
    previousValue: string,
    nextValue: string,
    bindings: MentionBinding[],
    replacementStart: number,
    replacementEnd: number,
    replacement: MentionBinding,
): MentionBinding[] {
    const untouched = bindings.filter((binding) => (
        binding.end <= replacementStart || binding.start >= replacementEnd
    ));
    return [
        ...reconcileMentionBindings(previousValue, nextValue, untouched),
        replacement,
    ];
}

/** Replace a whole bound token when the dropdown was reopened from a caret inside that token. */
export function mentionReplacementEnd(
    value: string,
    bindings: MentionBinding[],
    queryStart: number,
    queryEnd: number,
): number {
    const existing = bindings.find((binding) => (
        isLiveBinding(value, binding)
        && binding.start === queryStart
        && binding.end >= queryEnd
    ));
    return existing?.end ?? queryEnd;
}

export function liveMentionParticipantIds(
    value: string,
    bindings: MentionBinding[],
): string[] {
    const seen = new Set<string>();
    return bindings
        .filter((binding) => isLiveBinding(value, binding))
        .sort((a, b) => a.start - b.start)
        .flatMap((binding) => {
            if (seen.has(binding.participantId)) return [];
            seen.add(binding.participantId);
            return [binding.participantId];
        });
}

export function liveMentionedAgentCount(value: string, bindings: MentionBinding[]): number {
    const agentIds = new Set(
        bindings
            .filter((binding) => (
                binding.participantType === 'agent' && isLiveBinding(value, binding)
            ))
            .map((binding) => binding.participantId),
    );
    return agentIds.size;
}
