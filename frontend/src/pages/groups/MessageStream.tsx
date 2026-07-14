import { Fragment, useEffect, useLayoutEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconBrain, IconRobot } from '@tabler/icons-react';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import type {
    GroupMember,
    GroupMessage,
    GroupRuntimeActivity,
} from '../../types/group';

interface MessageStreamProps {
    sessionId: string;
    messages: GroupMessage[];
    runtimeActivities: GroupRuntimeActivity[];
    members: GroupMember[];
    myParticipantId?: string;
    hasMore: boolean;
    loadingMore: boolean;
    onLoadMore: () => void;
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
    runtimeActivities,
    members,
    myParticipantId,
    hasMore,
    loadingMore,
    onLoadMore,
}: MessageStreamProps) {
    const { t } = useTranslation();
    const scrollRef = useRef<HTMLDivElement>(null);
    const bottomRef = useRef<HTMLDivElement>(null);
    const pinnedToBottomRef = useRef(true);
    const previousHeightRef = useRef(0);
    const previousCountRef = useRef(0);

    const memberByParticipant = new Map(members.map((member) => [member.participant_id, member]));
    const memberByAgent = new Map(
        members
            .filter((member) => member.participant_type === 'agent')
            .map((member) => [member.participant_ref_id, member]),
    );

    const onScroll = () => {
        const node = scrollRef.current;
        if (!node) return;
        const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
        pinnedToBottomRef.current = distanceFromBottom < 80;
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
    }, [messages, runtimeActivities]);

    const planningActivities = runtimeActivities.filter(
        (activity) => activity.status === 'planning',
    );
    const compactingActivities = runtimeActivities.filter(
        (activity) => activity.status === 'compacting',
    );
    const agentActivities = runtimeActivities.filter(
        (activity) => ['working', 'waiting'].includes(activity.status) && activity.agent_id,
    );
    const visibleMessages = messages.filter(
        (message) => message.role === 'system' || Boolean(message.content?.trim()),
    );

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

            {visibleMessages.length === 0 && (
                <div className="group-stream-empty">
                    {t('groups.noMessages', '还没有消息。发一条，或 @ 一个智能体开始协作。')}
                </div>
            )}

            {visibleMessages.map((message) => {
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
                // participant_id is the durable author identity. sender_name is only a fallback
                // for historical messages whose member is no longer present in the group.
                const name = member?.display_name
                    ?? message.sender_name
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
                                    ? <MarkdownRenderer content={message.content} />
                                    : <span className="group-message-text">
                                        {renderContentWithMentions(message.content, message.mentions)}
                                    </span>}
                            </div>
                        </div>
                    </div>
                );
            })}

            {runtimeActivities.length > 0 && (
                <div className="group-runtime-activities" aria-live="polite">
                    {planningActivities.map((activity) => (
                        <div
                            key={activity.run_id}
                            className="group-runtime-activity planning"
                            role="status"
                        >
                            <div className="group-runtime-avatar-stack" aria-hidden="true">
                                {activity.candidate_agent_ids.slice(0, 3).map((agentId) => (
                                    <span key={agentId} className="group-runtime-avatar">
                                        <IconRobot size={14} stroke={1.7} />
                                        <span className="group-runtime-dot planning" />
                                    </span>
                                ))}
                            </div>
                            <span>{t('groups.planningActivity', '正在规划任务与分工')}</span>
                            <span className="group-runtime-ellipsis" aria-hidden="true">
                                <i /><i /><i />
                            </span>
                        </div>
                    ))}

                    {compactingActivities.map((activity) => {
                        const member = activity.agent_id
                            ? memberByAgent.get(activity.agent_id)
                            : undefined;
                        return (
                            <div
                                key={activity.run_id}
                                className="group-runtime-activity compacting"
                                role="status"
                            >
                                <span className="group-runtime-avatar" aria-hidden="true">
                                    <IconBrain size={14} stroke={1.7} />
                                    <span className="group-runtime-dot compacting" />
                                </span>
                                <span>
                                    {member
                                        ? t('groups.agentCompacting', '{{name}} 正在压缩上下文', {
                                            name: member.display_name,
                                        })
                                        : t('groups.compactingActivity', '正在压缩群聊上下文')}
                                </span>
                                <span className="group-runtime-ellipsis" aria-hidden="true">
                                    <i /><i /><i />
                                </span>
                            </div>
                        );
                    })}

                    {agentActivities.map((activity) => {
                        const member = memberByAgent.get(activity.agent_id!);
                        const name = member?.display_name ?? t('groups.unknownAgent', '智能体');
                        return (
                            <div key={activity.run_id} className="group-runtime-activity">
                                <span className="group-runtime-avatar" aria-hidden="true">
                                    <IconRobot size={14} stroke={1.7} />
                                    <span className={`group-runtime-dot ${activity.status}`} />
                                </span>
                                <span>
                                    {activity.status === 'waiting'
                                        ? t('groups.agentWaiting', '{{name}} 等待补充信息', { name })
                                        : t('groups.agentWorking', '{{name}} 正在处理', { name })}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}

            <div ref={bottomRef} />
        </div>
    );
}
