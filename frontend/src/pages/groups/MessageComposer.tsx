import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconRobot, IconSend, IconUser } from '@tabler/icons-react';
import type { GroupMember } from '../../types/group';

interface MessageComposerProps {
    members: GroupMember[];
    disabled?: boolean;
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

export default function MessageComposer({ members, disabled, onSend }: MessageComposerProps) {
    const { t } = useTranslation();
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [value, setValue] = useState('');
    const [query, setQuery] = useState<MentionQuery | null>(null);
    const [highlighted, setHighlighted] = useState(0);
    const [sending, setSending] = useState(false);

    /**
     * Mentions the user actually picked from the dropdown. The backend resolves mentions from this
     * list, not by parsing the text, so a name typed by hand never wakes an agent. On send we keep
     * only the ones whose `@name` survived further editing.
     */
    const [picked, setPicked] = useState<GroupMember[]>([]);

    const candidates = useMemo(() => {
        if (!query) return [];
        const needle = query.text.toLowerCase();
        return members
            .filter((member) => member.display_name.toLowerCase().includes(needle))
            .slice(0, 8);
    }, [members, query]);

    useEffect(() => setHighlighted(0), [query?.text]);

    const syncQuery = (nextValue: string, caret: number) => {
        setQuery(findMentionQuery(nextValue, caret));
    };

    const applyMention = (member: GroupMember) => {
        if (!query) return;
        const before = value.slice(0, query.start);
        const after = value.slice(query.start + 1 + query.text.length);
        const inserted = `@${member.display_name} `;
        const next = `${before}${inserted}${after}`;

        setValue(next);
        setPicked((current) =>
            current.some((m) => m.participant_id === member.participant_id)
                ? current
                : [...current, member],
        );
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

        // A mention the user deleted from the text should not wake its agent.
        const live = picked.filter((member) => content.includes(`@${member.display_name}`));

        setSending(true);
        try {
            await onSend(content, live.map((member) => member.participant_id));
            setValue('');
            setPicked([]);
            setQuery(null);
        } finally {
            setSending(false);
        }
    };

    const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
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

    const agentCount = picked.filter(
        (member) => member.participant_type === 'agent' && value.includes(`@${member.display_name}`),
    ).length;

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

            <div className="group-composer-box">
                <textarea
                    ref={textareaRef}
                    className="group-composer-input"
                    rows={2}
                    value={value}
                    disabled={disabled}
                    placeholder={t('groups.composerPlaceholder', '发送消息，@ 唤醒智能体')}
                    onChange={(event) => {
                        setValue(event.target.value);
                        syncQuery(event.target.value, event.target.selectionStart ?? 0);
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
                <button
                    type="button"
                    className="group-composer-send"
                    disabled={disabled || sending || !value.trim()}
                    onClick={() => void submit()}
                    title={t('groups.send', '发送')}
                >
                    <IconSend size={16} stroke={1.7} />
                </button>
            </div>

            <div className="group-composer-footer">
                {agentCount > 1
                    ? t('groups.planningHint', '@ 了多个智能体，系统会先做任务规划再分工执行')
                    : t('groups.sendHint', 'Enter 发送，Shift + Enter 换行')}
            </div>
        </div>
    );
}
