import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconPlayerStop, IconRobot, IconSend, IconUser } from '@tabler/icons-react';
import type { GroupMember } from '../../types/group';
import {
    liveMentionParticipantIds,
    liveMentionedAgentCount,
    mentionReplacementEnd,
    reconcileMentionBindings,
    replaceMentionBinding,
    type MentionBinding,
    type MentionEditHint,
} from './mentionBindings';

interface MessageComposerProps {
    members: GroupMember[];
    disabled?: boolean;
    canCancel?: boolean;
    cancelling?: boolean;
    onCancel?: () => void;
    onSend: (content: string, mentionParticipantIds: string[]) => Promise<void>;
}

/** Where the caret sits inside an in-progress `@query`, if it does. */
interface MentionQuery {
    start: number;
    text: string;
}

function findMentionQuery(value: string, caret: number): MentionQuery | null {
    const at = value.lastIndexOf('@', caret - 1);
    if (at === -1) return null;
    // `@` only opens a mention at a word boundary, so emails and code don't trigger it.
    if (at > 0 && !/\s/.test(value[at - 1])) return null;
    const text = value.slice(at + 1, caret);
    if (/\s/.test(text)) return null;
    return { start: at, text };
}

export default function MessageComposer({
    members,
    disabled,
    canCancel = false,
    cancelling = false,
    onCancel,
    onSend,
}: MessageComposerProps) {
    const { t } = useTranslation();
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [value, setValue] = useState('');
    const [query, setQuery] = useState<MentionQuery | null>(null);
    const [highlighted, setHighlighted] = useState(0);
    const [sending, setSending] = useState(false);
    const pendingEditRef = useRef<MentionEditHint | null>(null);

    /**
     * One stable participant identity per mention occurrence picked from the dropdown. Text ranges
     * move with edits around them and are discarded if their exact token is edited or deleted.
     * The backend never resolves a participant from display text, so hand-typed names stay plain.
     */
    const [mentionBindings, setMentionBindings] = useState<MentionBinding[]>([]);

    const candidates = useMemo(() => {
        if (!query) return [];
        const needle = query.text.toLowerCase();
        return members
            .filter((member) => member.display_name.toLowerCase().includes(needle))
            .slice(0, 8);
    }, [members, query]);

    useEffect(() => setHighlighted(0), [query?.text]);

    // Auto-grow the textarea to fit its content (capped by max-height in CSS). Runs for typing,
    // mention insertion and the post-send clear alike, since they all flow through `value`.
    useEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = `${el.scrollHeight}px`;
    }, [value]);

    const syncQuery = (nextValue: string, caret: number) => {
        setQuery(findMentionQuery(nextValue, caret));
    };

    const applyMention = (member: GroupMember) => {
        if (!query) return;
        const queryEnd = query.start + 1 + query.text.length;
        const replacementEnd = mentionReplacementEnd(
            value,
            mentionBindings,
            query.start,
            queryEnd,
        );
        const before = value.slice(0, query.start);
        const after = value.slice(replacementEnd);
        const inserted = `@${member.display_name} `;
        const next = `${before}${inserted}${after}`;
        const token = inserted.slice(0, -1);

        setValue(next);
        setMentionBindings((current) => replaceMentionBinding(
            value,
            next,
            current,
            query.start,
            replacementEnd,
            {
                participantId: member.participant_id,
                participantType: member.participant_type,
                start: before.length,
                end: before.length + token.length,
                text: token,
            },
        ));
        setQuery(null);

        const caret = before.length + inserted.length;
        requestAnimationFrame(() => {
            textareaRef.current?.focus();
            textareaRef.current?.setSelectionRange(caret, caret);
        });
    };

    const submit = async () => {
        const content = value.trim();
        if (!content || sending || disabled) return;
        const mentionParticipantIds = liveMentionParticipantIds(value, mentionBindings);

        setSending(true);
        try {
            await onSend(content, mentionParticipantIds);
            setValue('');
            setMentionBindings([]);
            setQuery(null);
        } finally {
            setSending(false);
        }
    };

    const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // While an IME is composing (typing pinyin, etc.), Enter commits the candidate — it must
        // reach the input method, not send the raw text or pick a mention. Bail before any handling.
        if (event.nativeEvent.isComposing || event.keyCode === 229) return;

        if (query && candidates.length > 0) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                setHighlighted((i) => (i + 1) % candidates.length);
                return;
            }
            if (event.key === 'ArrowUp') {
                event.preventDefault();
                setHighlighted((i) => (i - 1 + candidates.length) % candidates.length);
                return;
            }
            if (event.key === 'Enter' || event.key === 'Tab') {
                event.preventDefault();
                applyMention(candidates[highlighted]);
                return;
            }
            if (event.key === 'Escape') {
                event.preventDefault();
                setQuery(null);
                return;
            }
        }

        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            void submit();
        }
    };

    const agentCount = liveMentionedAgentCount(value, mentionBindings);

    return (
        <div className="group-composer">
            {query && candidates.length > 0 && (
                <div className="group-mention-popup">
                    {candidates.map((member, index) => (
                        <button
                            key={member.participant_id}
                            type="button"
                            className={`group-mention-option ${index === highlighted ? 'active' : ''}`}
                            onMouseEnter={() => setHighlighted(index)}
                            onMouseDown={(event) => {
                                event.preventDefault();
                                applyMention(member);
                            }}
                        >
                            <span className="group-mention-icon">
                                {member.participant_type === 'agent'
                                    ? <IconRobot size={14} stroke={1.6} />
                                    : <IconUser size={14} stroke={1.6} />}
                            </span>
                            <span className="group-mention-name">{member.display_name}</span>
                            {member.role_description && (
                                <span className="group-mention-hint">{member.role_description}</span>
                            )}
                        </button>
                    ))}
                </div>
            )}

            <div className="chat-composer">
                <div className="chat-composer-input-block">
                    <textarea
                        ref={textareaRef}
                        className="chat-input"
                        rows={1}
                        value={value}
                        disabled={disabled}
                        placeholder={t('groups.composerPlaceholder', '发送消息，@ 唤醒智能体')}
                        onBeforeInput={(event) => {
                            const target = event.currentTarget;
                            pendingEditRef.current = {
                                start: target.selectionStart ?? 0,
                                end: target.selectionEnd ?? 0,
                                inputType: (event.nativeEvent as InputEvent).inputType || '',
                            };
                        }}
                        onChange={(event) => {
                            const nextValue = event.target.value;
                            setMentionBindings((current) => reconcileMentionBindings(
                                value,
                                nextValue,
                                current,
                                pendingEditRef.current,
                            ));
                            pendingEditRef.current = null;
                            setValue(nextValue);
                            syncQuery(nextValue, event.target.selectionStart ?? 0);
                        }}
                        onKeyUp={(event) => {
                            const target = event.target as HTMLTextAreaElement;
                            syncQuery(target.value, target.selectionStart ?? 0);
                        }}
                        onClick={(event) => {
                            const target = event.target as HTMLTextAreaElement;
                            syncQuery(target.value, target.selectionStart ?? 0);
                        }}
                        onKeyDown={onKeyDown}
                    />
                </div>
                <div className="chat-composer-toolbar">
                    <span className="group-composer-hint">
                        {agentCount > 1
                            ? t('groups.planningHint', '@ 了多个智能体，系统会先做任务规划再分工执行')
                            : t('groups.sendHint', 'Enter 发送，Shift + Enter 换行')}
                    </span>
                    <div style={{ flex: 1 }} />
                    {canCancel && (
                        <button
                            type="button"
                            className="btn btn-secondary chat-composer-send"
                            disabled={cancelling}
                            onClick={onCancel}
                            title={t('groups.cancelRun', '停止运行')}
                        >
                            <IconPlayerStop size={16} stroke={1.75} />
                        </button>
                    )}
                    <button
                        type="button"
                        className="btn btn-primary chat-composer-send"
                        disabled={disabled || sending || !value.trim()}
                        onClick={() => void submit()}
                        title={t('groups.send', '发送')}
                    >
                        <IconSend size={16} stroke={1.75} />
                    </button>
                </div>
            </div>
        </div>
    );
}
