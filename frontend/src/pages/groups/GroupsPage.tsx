import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    IconDotsVertical,
    IconLayoutSidebarLeftCollapse,
    IconLayoutSidebarLeftExpand,
    IconMessage2,
    IconPlus,
    IconUsers,
} from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import { compareCursor, useGroupRealtime } from '../../hooks/useGroupRealtime';
import { useAuthStore } from '../../stores';
import { useToast } from '../../components/Toast/ToastProvider';
import PromptModal from '../../components/PromptModal';
import ConfirmModal from '../../components/ConfirmModal';
import MessageStream from './MessageStream';
import MessageComposer from './MessageComposer';
import GroupSidePanel from './GroupSidePanel';
import InviteMemberModal from './InviteMemberModal';
import type { GroupMessage, GroupSession } from '../../types/group';
import './groups.css';

const HISTORY_PAGE_SIZE = 30;

const readFlag = (key: string, fallback: boolean) => {
    const stored = localStorage.getItem(key);
    return stored === null ? fallback : stored === '1';
};

const mergeMessages = (previous: GroupMessage[], incoming: GroupMessage[]): GroupMessage[] => {
    if (incoming.length === 0) return previous;
    const byId = new Map(previous.map((message) => [message.id, message]));
    for (const message of incoming) byId.set(message.id, message);
    return [...byId.values()].sort((a, b) => compareCursor(a.cursor, b.cursor));
};

export default function GroupsPage() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const toast = useToast();
    const queryClient = useQueryClient();
    const { groupId, sessionId } = useParams<{ groupId?: string; sessionId?: string }>();
    const currentUser = useAuthStore((state) => state.user);

    const [messages, setMessages] = useState<GroupMessage[]>([]);
    const [hasMore, setHasMore] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    // Each rail collapses on its own, and the side panel stays out of the way until asked for —
    // on a 1440px screen all three open at once leave the conversation only ~420px.
    const [groupsCollapsed, setGroupsCollapsed] = useState(
        () => readFlag('groups.groupsCollapsed', false),
    );
    const [sessionsCollapsed, setSessionsCollapsed] = useState(
        () => readFlag('groups.sessionsCollapsed', false),
    );
    const [showPanel, setShowPanel] = useState(() => readFlag('groups.showPanel', false));
    const [showInvite, setShowInvite] = useState(false);
    const [creatingGroup, setCreatingGroup] = useState(false);
    const [creatingSession, setCreatingSession] = useState(false);
    const [deletingSession, setDeletingSession] = useState<GroupSession | null>(null);

    const { data: groups = [], refetch: refetchGroups } = useQuery({
        queryKey: ['groups'],
        queryFn: () => groupApi.list(),
    });

    const { data: sessions = [], refetch: refetchSessions } = useQuery({
        queryKey: ['group-sessions', groupId],
        queryFn: () => groupApi.sessions(groupId!),
        enabled: Boolean(groupId),
    });

    const { data: members = [], refetch: refetchMembers } = useQuery({
        queryKey: ['group-members', groupId],
        queryFn: () => groupApi.members(groupId!),
        enabled: Boolean(groupId),
    });

    const activeGroup = groups.find((group) => group.id === groupId);
    const activeSession = sessions.find((session) => session.id === sessionId);

    const me = useMemo(
        () => members.find(
            (member) => member.participant_type === 'user'
                && member.participant_ref_id === currentUser?.id,
        ),
        [members, currentUser?.id],
    );
    const isManager = me?.role === 'manager';

    // Land on a group, then on a session, so the pane is never pointing at nothing.
    useEffect(() => {
        if (!groupId && groups.length > 0) {
            navigate(`/groups/${groups[0].id}`, { replace: true });
        }
    }, [groupId, groups, navigate]);

    useEffect(() => {
        if (!groupId || sessionId || sessions.length === 0) return;
        const landing = sessions.find((session) => session.is_primary) ?? sessions[0];
        navigate(`/groups/${groupId}/${landing.id}`, { replace: true });
    }, [groupId, sessionId, sessions, navigate]);

    // Load the newest page whenever the session changes.
    useEffect(() => {
        if (!groupId || !sessionId) {
            setMessages([]);
            setHasMore(false);
            return;
        }
        let cancelled = false;
        setMessages([]);
        setHasMore(false);
        void groupApi
            .messages(groupId, sessionId, { limit: HISTORY_PAGE_SIZE })
            .then((page) => {
                if (cancelled) return;
                // Merge rather than replace: a pushed message can land while this page is in flight.
                setMessages((previous) => mergeMessages(previous, page));
                setHasMore(page.length === HISTORY_PAGE_SIZE);
            })
            .catch(() => {
                if (!cancelled) toast.error(t('groups.loadFailed', '加载消息失败'));
            });
        return () => {
            cancelled = true;
        };
    }, [groupId, sessionId, toast, t]);

    const messagesRef = useRef(messages);
    messagesRef.current = messages;

    const getLastCursor = useCallback(() => {
        const list = messagesRef.current;
        return list.length > 0 ? list[list.length - 1].cursor : undefined;
    }, []);

    const receiveMessages = useCallback((incomingSessionId: string, incoming: GroupMessage[]) => {
        if (incomingSessionId !== sessionId) return;
        setMessages((previous) => mergeMessages(previous, incoming));
    }, [sessionId]);

    const onGroupActivity = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: ['group-sessions', groupId] });
    }, [queryClient, groupId]);

    const { status } = useGroupRealtime({
        groupId,
        sessionId,
        getLastCursor,
        onMessages: receiveMessages,
        onGroupActivity,
    });

    // Reading the newest message is what clears this session's unread badge.
    const lastMessageId = messages.length > 0 ? messages[messages.length - 1].id : undefined;
    useEffect(() => {
        if (!groupId || !sessionId || !lastMessageId) return;
        const timer = setTimeout(() => {
            void groupApi
                .markSessionRead(groupId, sessionId, lastMessageId)
                .then(() => refetchSessions())
                .catch(() => undefined);
        }, 400);
        return () => clearTimeout(timer);
    }, [groupId, sessionId, lastMessageId, refetchSessions]);

    const persistToggle = (
        key: string,
        setter: React.Dispatch<React.SetStateAction<boolean>>,
    ) => () => setter((current) => {
        localStorage.setItem(key, current ? '0' : '1');
        return !current;
    });

    const toggleGroups = persistToggle('groups.groupsCollapsed', setGroupsCollapsed);
    const toggleSessions = persistToggle('groups.sessionsCollapsed', setSessionsCollapsed);
    const togglePanel = persistToggle('groups.showPanel', setShowPanel);

    const loadMore = async () => {
        if (!groupId || !sessionId || loadingMore || messages.length === 0) return;
        setLoadingMore(true);
        try {
            const older = await groupApi.messages(groupId, sessionId, {
                limit: HISTORY_PAGE_SIZE,
                before: messages[0].cursor,
            });
            setMessages((previous) => mergeMessages(previous, older));
            setHasMore(older.length === HISTORY_PAGE_SIZE);
        } catch {
            toast.error(t('groups.loadFailed', '加载消息失败'));
        } finally {
            setLoadingMore(false);
        }
    };

    const sendMessage = async (content: string, mentionParticipantIds: string[]) => {
        if (!groupId || !sessionId) return;
        try {
            const intake = await groupApi.sendMessage(groupId, sessionId, {
                content,
                mentions: mentionParticipantIds.map((participant_id) => ({ participant_id })),
                message_id: crypto.randomUUID(),
            });
            setMessages((previous) => mergeMessages(previous, [intake.message]));

            // Planning can fail before any agent starts — say so instead of leaving a silent gap.
            if (intake.error_code) {
                toast.warning(t('groups.dispatchWarning', '智能体唤醒未完成：{{code}}', {
                    code: intake.error_code,
                }));
            }
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.sendFailed', '发送失败'));
            throw error;
        }
    };

    const createGroup = async (name: string) => {
        setCreatingGroup(false);
        if (!name.trim()) return;
        try {
            const group = await groupApi.create({ name: name.trim() });
            await refetchGroups();
            navigate(`/groups/${group.id}`);
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.createFailed', '建群失败'));
        }
    };

    const createSession = async (title: string) => {
        setCreatingSession(false);
        if (!groupId) return;
        try {
            const session = await groupApi.createSession(
                groupId,
                title.trim() ? { title: title.trim() } : {},
            );
            await refetchSessions();
            navigate(`/groups/${groupId}/${session.id}`);
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.createSessionFailed', '创建会话失败'));
        }
    };

    const deleteSession = async () => {
        if (!groupId || !deletingSession) return;
        try {
            await groupApi.deleteSession(groupId, deletingSession.id);
            const remaining = await refetchSessions();
            if (deletingSession.id === sessionId) {
                const next = remaining.data?.[0];
                navigate(next ? `/groups/${groupId}/${next.id}` : `/groups/${groupId}`, { replace: true });
            }
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.deleteSessionFailed', '删除会话失败'));
        } finally {
            setDeletingSession(null);
        }
    };

    const totalUnread = sessions.reduce((sum, session) => sum + session.unread_count, 0);

    return (
        <div className="groups-page">
            <div className={`group-column groups ${groupsCollapsed ? 'collapsed' : ''}`}>
                {groupsCollapsed ? (
                    <button
                        type="button"
                        className="group-rail-stub"
                        title={t('groups.expandGroups', '展开群聊栏')}
                        onClick={toggleGroups}
                    >
                        <IconLayoutSidebarLeftExpand size={16} stroke={1.7} />
                        <IconUsers size={15} stroke={1.6} />
                    </button>
                ) : (
                    <>
                        <div className="group-column-header">
                            <span>{t('groups.title', '群聊')}</span>
                            <div className="group-column-actions">
                                <button
                                    type="button"
                                    className="group-icon-btn"
                                    title={t('groups.create', '创建群聊')}
                                    onClick={() => setCreatingGroup(true)}
                                >
                                    <IconPlus size={15} stroke={1.8} />
                                </button>
                                <button
                                    type="button"
                                    className="group-icon-btn"
                                    title={t('groups.collapseGroups', '折叠群聊栏')}
                                    onClick={toggleGroups}
                                >
                                    <IconLayoutSidebarLeftCollapse size={15} stroke={1.7} />
                                </button>
                            </div>
                        </div>
                        <div className="group-column-body">
                            {groups.length === 0 && (
                                <div className="group-empty-hint">
                                    {t('groups.noGroups', '还没有群聊。创建一个，把人和智能体拉进来。')}
                                </div>
                            )}
                            {groups.map((group) => (
                                <button
                                    key={group.id}
                                    type="button"
                                    className={`group-row ${group.id === groupId ? 'active' : ''}`}
                                    onClick={() => navigate(`/groups/${group.id}`)}
                                >
                                    <span className="group-row-avatar">
                                        <IconUsers size={15} stroke={1.6} />
                                    </span>
                                    <span className="group-row-name">{group.name}</span>
                                </button>
                            ))}
                        </div>
                    </>
                )}
            </div>

            {groupId && (
                <div className={`group-column sessions ${sessionsCollapsed ? 'collapsed' : ''}`}>
                    {sessionsCollapsed ? (
                        <button
                            type="button"
                            className="group-rail-stub"
                            title={t('groups.expandSessions', '展开会话栏')}
                            onClick={toggleSessions}
                        >
                            <IconLayoutSidebarLeftExpand size={16} stroke={1.7} />
                            <span className="group-rail-stub-icon">
                                <IconMessage2 size={15} stroke={1.6} />
                                {totalUnread > 0 && <span className="group-rail-dot" />}
                            </span>
                        </button>
                    ) : (
                        <>
                            <div className="group-column-header">
                                <span>{t('groups.sessions', '会话')}</span>
                                <div className="group-column-actions">
                                    <button
                                        type="button"
                                        className="group-icon-btn"
                                        title={t('groups.newSession', '新建会话')}
                                        onClick={() => setCreatingSession(true)}
                                    >
                                        <IconPlus size={15} stroke={1.8} />
                                    </button>
                                    <button
                                        type="button"
                                        className="group-icon-btn"
                                        title={t('groups.collapseSessions', '折叠会话栏')}
                                        onClick={toggleSessions}
                                    >
                                        <IconLayoutSidebarLeftCollapse size={15} stroke={1.7} />
                                    </button>
                                </div>
                            </div>
                            <div className="group-column-body">
                                {sessions.length === 0 && (
                                    <div className="group-empty-hint">
                                        {t('groups.noSessions', '这个群还没有会话，新建一个开始协作。')}
                                    </div>
                                )}
                                {sessions.map((session) => (
                                    <div
                                        key={session.id}
                                        className={`group-row session ${session.id === sessionId ? 'active' : ''}`}
                                    >
                                        <button
                                            type="button"
                                            className="group-row-main"
                                            onClick={() => navigate(`/groups/${groupId}/${session.id}`)}
                                        >
                                            <IconMessage2 size={14} stroke={1.6} />
                                            <span className="group-row-name">{session.title}</span>
                                            {session.unread_count > 0 && session.id !== sessionId && (
                                                <span className="group-unread">{session.unread_count}</span>
                                            )}
                                        </button>
                                        {isManager && (
                                            <button
                                                type="button"
                                                className="group-icon-btn subtle"
                                                title={t('groups.deleteSession', '删除会话')}
                                                onClick={() => setDeletingSession(session)}
                                            >
                                                <IconDotsVertical size={14} stroke={1.7} />
                                            </button>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </>
                    )}
                </div>
            )}

            <div className="group-main">
                {activeGroup && activeSession ? (
                    <>
                        <header className="group-main-header">
                            <div className="group-main-heading">
                                <div className="group-main-title">{activeSession.title}</div>
                                <div className="group-main-subtitle">
                                    {activeGroup.name}
                                    {status === 'polling' && ` · ${t('groups.polling', '轮询中')}`}
                                    {status === 'offline' && ` · ${t('groups.offline', '连接断开')}`}
                                </div>
                            </div>
                            <button
                                type="button"
                                className={`group-icon-btn ${showPanel ? 'active' : ''}`}
                                title={t('groups.members', '成员')}
                                onClick={togglePanel}
                            >
                                <IconUsers size={16} stroke={1.7} />
                            </button>
                        </header>

                        <MessageStream
                            sessionId={activeSession.id}
                            messages={messages}
                            members={members}
                            myParticipantId={me?.participant_id}
                            hasMore={hasMore}
                            loadingMore={loadingMore}
                            onLoadMore={() => void loadMore()}
                        />

                        <MessageComposer members={members} onSend={sendMessage} />
                    </>
                ) : (
                    <div className="group-main-empty">
                        {groups.length === 0
                            ? t('groups.noGroups', '还没有群聊。创建一个，把人和智能体拉进来。')
                            : t('groups.pickSession', '选择或新建一个会话开始协作。')}
                    </div>
                )}
            </div>

            {showPanel && activeGroup && (
                <GroupSidePanel
                    groupId={activeGroup.id}
                    members={members}
                    isManager={isManager}
                    onInvite={() => setShowInvite(true)}
                    onMembersChanged={() => void refetchMembers()}
                    onClose={() => setShowPanel(false)}
                />
            )}

            {showInvite && activeGroup && (
                <InviteMemberModal
                    groupId={activeGroup.id}
                    members={members}
                    onClose={() => setShowInvite(false)}
                    onInvited={() => void refetchMembers()}
                />
            )}

            <PromptModal
                open={creatingGroup}
                title={t('groups.create', '创建群聊')}
                placeholder={t('groups.namePlaceholder', '群名称')}
                onConfirm={(value) => void createGroup(value)}
                onCancel={() => setCreatingGroup(false)}
            />

            <PromptModal
                open={creatingSession}
                title={t('groups.newSession', '新建会话')}
                placeholder={t('groups.sessionTitlePlaceholder', '会话名称，可留空')}
                onConfirm={(value) => void createSession(value)}
                onCancel={() => setCreatingSession(false)}
            />

            <ConfirmModal
                open={Boolean(deletingSession)}
                title={t('groups.deleteSession', '删除会话')}
                message={t('groups.deleteSessionConfirm', '删除后该会话的消息不再可见，且无法恢复。')}
                danger
                onConfirm={() => void deleteSession()}
                onCancel={() => setDeletingSession(null)}
            />
        </div>
    );
}
