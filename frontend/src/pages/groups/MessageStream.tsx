import { Fragment, useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconRobot } from '@tabler/icons-react';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import type { GroupMember, GroupMessage } from '../../types/group';

interface MessageStreamProps {
    sessionId: string;
    messages: GroupMessage[];
    members: GroupMember[];
    myParticipantId?: string;
    hasMore: boolean;
    loadingMore: boolean;
    isPlanning: boolean;
    runningAgents: Array<{ id: string; name: string }>;
    onLoadMore: () => void;
    onLatestMessageSeen: (messageId: string) => void;
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });

/**
 * Highlight the `@name` spans this message actually mentioned. Names are matched against the
 * message's own mention list rather than any `@word` in the text, so a hand-typed `@someone`
 * stays plain — which mirrors the backend, where only structured mention tokens wake an agent.
 */
function renderContentWithMentions(content: string, mentions: GroupMessage['mentions']) {
    const names = mentions
        .map((mention) => mention.display_name)
        .filter((name): name is string => Boolean(name))
        .sort((a, b) => b.length - a.length); // Longest first: "@Ann Lee" must beat "@Ann".

    if (names.length === 0) return content;

    const escaped = names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const pattern = new RegExp(`@(${escaped.join('|')})`, 'g');
    const parts = content.split(pattern);

    // String.split with one capture group alternates: text, captured name, text, ...
    return parts.map((part, index) =>
        index % 2 === 1
            ? <span key={index} className="group-mention-chip">@{part}</span>
            : <Fragment key={index}>{part}</Fragment>,
    );
}

export default function MessageStream({
    sessionId,
    messages,
    members,
    myParticipantId,
    hasMore,
    loadingMore,
    isPlanning,
    runningAgents,
    onLoadMore,
    onLatestMessageSeen,
}: MessageStreamProps) {
    const { t } = useTranslation();
    const scrollRef = useRef<HTMLDivElement>(null);
    const bottomRef = useRef<HTMLDivElement>(null);
    const pinnedToBottomRef = useRef(true);
    const previousHeightRef = useRef(0);
    const previousCountRef = useRef(0);
    const latestMessageId = messages.length > 0 ? messages[messages.length - 1].id : undefined;

    const reportLatestMessageSeen = useCallback(() => {
        const stream = scrollRef.current;
        const bottom = bottomRef.current;
        if (!stream || !bottom || !latestMessageId) return;
        if (document.visibilityState !== 'visible' || !document.hasFocus()) return;

        // Reaching the bottom sentinel proves the newest rendered message is in the visible stream.
        const streamRect = stream.getBoundingClientRect();
        const bottomRect = bottom.getBoundingClientRect();
        if (bottomRect.top < streamRect.top || bottomRect.bottom > streamRect.bottom + 1) return;
        onLatestMessageSeen(latestMessageId);
    }, [latestMessageId, onLatestMessageSeen]);

    const memberByParticipant = new Map(members.map((member) => [member.participant_id, member]));

    const onScroll = () => {
        const node = scrollRef.current;
        if (!node) return;
        const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
        pinnedToBottomRef.current = distanceFromBottom < 80;
        reportLatestMessageSeen();
        if (node.scrollTop < 60 && hasMore && !loadingMore) {
            previousHeightRef.current = node.scrollHeight;
            onLoadMore();
        }
    };

    useLayoutEffect(() => {
        const node = scrollRef.current;
        if (!node) return;

        const grewAtTop = messages.length > previousCountRef.current && previousHeightRef.current > 0;
        if (grewAtTop) {
            // Older messages prepended: hold the reading position instead of jumping.
            node.scrollTop += node.scrollHeight - previousHeightRef.current;
            previousHeightRef.current = 0;
        } else if (pinnedToBottomRef.current) {
            bottomRef.current?.scrollIntoView({ block: 'end' });
        }
        previousCountRef.current = messages.length;
        reportLatestMessageSeen();
    }, [messages, isPlanning, runningAgents, reportLatestMessageSeen]);

    // A background tab or unfocused window is not a read. Re-check when the user returns.
    useEffect(() => {
        const checkVisibility = () => reportLatestMessageSeen();
        document.addEventListener('visibilitychange', checkVisibility);
        window.addEventListener('focus', checkVisibility);
        checkVisibility();
        return () => {
            document.removeEventListener('visibilitychange', checkVisibility);
            window.removeEventListener('focus', checkVisibility);
        };
    }, [reportLatestMessageSeen]);

    // A different session starts a different scroll history.
    useEffect(() => {
        pinnedToBottomRef.current = true;
        previousCountRef.current = 0;
        previousHeightRef.current = 0;
    }, [sessionId]);

    return (
        <div className="group-stream" ref={scrollRef} onScroll={onScroll}>
            {hasMore && (
                <div className="group-stream-more">
                    <button type="button" className="btn btn-ghost" onClick={onLoadMore} disabled={loadingMore}>
                        {loadingMore
                            ? t('common.loading', '加载中...')
                            : t('groups.loadMore', '加载更早的消息')}
                    </button>
                </div>
            )}

            {messages.length === 0 && (
                <div className="group-stream-empty">
                    {t('groups.noMessages', '还没有消息。发一条，或 @ 一个智能体开始协作。')}
                </div>
            )}

            {messages.map((message) => {
                if (message.role === 'system') {
                    return (
                        <div key={message.id} className="group-message-system">
                            {message.content}
                        </div>
                    );
                }

                const member = message.participant_id
                    ? memberByParticipant.get(message.participant_id)
                    : undefined;
                const isAgent = message.role === 'assistant';
                const isMine = Boolean(myParticipantId) && message.participant_id === myParticipantId;
                const name = message.sender_name
                    ?? member?.display_name
                    ?? t('groups.unknownSender', '未知成员');

                return (
                    <div key={message.id} className={`group-message ${isMine ? 'mine' : ''}`}>
                        <div className={`group-avatar ${isAgent ? 'agent' : ''}`}>
                            {isAgent ? <IconRobot size={15} stroke={1.6} /> : name.slice(0, 1).toUpperCase()}
                        </div>
                        <div className="group-message-body">
                            <div className="group-message-meta">
                                <span className="group-message-sender">{name}</span>
                                {isAgent && (
                                    <span className="group-badge-agent">
                                        {t('groups.agentBadge', '智能体')}
                                    </span>
                                )}
                                <span className="group-message-time">
                                    {timeFormat.format(new Date(message.created_at))}
                                </span>
                            </div>
                            <div className="group-message-bubble">
                                {isAgent
                                    ? <MarkdownRenderer
                                        content={message.content}
                                        mentionNames={message.mentions
                                            .map((mention) => mention.display_name)
                                            .filter((name): name is string => Boolean(name))}
                                    />
                                    : <span className="group-message-text">
                                        {renderContentWithMentions(message.content, message.mentions)}
                                    </span>}
                            </div>
                        </div>
                    </div>
                );
            })}

            {isPlanning && (
                <div className="group-message group-run-indicator" role="status" aria-live="polite">
                    <div className="group-avatar agent">
                        <IconRobot size={15} stroke={1.6} />
                    </div>
                    <div className="group-message-body">
                        <div className="group-message-meta">
                            <span className="group-message-sender">
                                {t('groups.taskPlanning', '任务规划中')}
                            </span>
                        </div>
                        <div className="group-message-bubble group-run-indicator-bubble">
                            <span /><span /><span />
                        </div>
                    </div>
                </div>
            )}

            {runningAgents.map((agent) => (
                <div
                    className="group-message group-run-indicator"
                    key={agent.id}
                    role="status"
                    aria-live="polite"
                >
                    <div className="group-avatar agent">
                        <IconRobot size={15} stroke={1.6} />
                    </div>
                    <div className="group-message-body">
                        <div className="group-message-meta">
                            <span className="group-message-sender">
                                {agent.name}
                            </span>
                            <span className="group-badge-agent">
                                {t('groups.agentBadge', '智能体')}
                            </span>
                        </div>
                        <div className="group-message-bubble group-run-indicator-bubble">
                            <span /><span /><span />
                        </div>
                    </div>
                </div>
            ))}

            <div ref={bottomRef} />
        </div>
    );
}
