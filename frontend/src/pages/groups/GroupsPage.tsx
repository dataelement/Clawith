import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    IconChevronDown,
    IconChevronRight,
    IconLayoutSidebarLeftCollapse,
    IconLayoutSidebarLeftExpand,
    IconLayoutSidebarRightCollapse,
    IconLayoutSidebarRightExpand,
    IconMessage2,
    IconPencil,
    IconPlus,
    IconSettings,
    IconTrash,
} from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import {
    compareCursor,
    type GroupActivity,
    useGroupRealtime,
} from '../../hooks/useGroupRealtime';
import { useAuthStore } from '../../stores';
import { useToast } from '../../components/Toast/ToastProvider';
import { createRandomUUID } from '../../utils/randomUUID';
import PromptModal from '../../components/PromptModal';
import ConfirmModal from '../../components/ConfirmModal';
import MessageStream from './MessageStream';
import MessageComposer from './MessageComposer';
import GroupSidePanel from './GroupSidePanel';
import GroupSettingsModal from './GroupSettingsModal';
import InviteMemberModal from './InviteMemberModal';
import InlineEdit from './InlineEdit';
import type { GroupMessage, GroupSession } from '../../types/group';
import './groups.css';

// A session whose title is being edited in place — the only inline edit; creating a group or
// session uses a modal.
type RenameTarget = { groupId: string; sessionId: string; current: string };

const HISTORY_PAGE_SIZE = 30;

const readFlag = (key: string, fallback: boolean) => {
    const stored = localStorage.getItem(key);
    return stored === null ? fallback : stored === '1';
};

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
const dateFormat = new Intl.DateTimeFormat(undefined, { month: '2-digit', day: '2-digit' });

// Compact "last activity" for a group row: clock for today, month-day otherwise. Locale-driven so
// it needs no translation string of its own.
const formatLastActivity = (iso: string | null | undefined): string => {
    if (!iso) return '';
    const then = new Date(iso);
    const now = new Date();
    const sameDay = then.getFullYear() === now.getFullYear()
        && then.getMonth() === now.getMonth()
        && then.getDate() === now.getDate();
    return sameDay ? timeFormat.format(then) : dateFormat.format(then);
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
    const [cancellingRuns, setCancellingRuns] = useState(false);
    // One nav rail now: a tree of groups with their sessions nested underneath. It collapses to a
    // stub, and the side panel stays out of the way until asked for.
    const [groupsCollapsed, setGroupsCollapsed] = useState(
        () => readFlag('groups.groupsCollapsed', false),
    );
    // Which groups are expanded in the tree. The active group is expanded on navigation (effect
    // below); others start collapsed.
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());
    const [showPanel, setShowPanel] = useState(() => readFlag('groups.showPanel', false));
    const [showInvite, setShowInvite] = useState(false);
    const [creatingGroup, setCreatingGroup] = useState(false);
    // The group a "new session" prompt targets, or null when closed.
    const [creatingSession, setCreatingSession] = useState<string | null>(null);
    // The session whose title is being renamed inline, or null.
    const [renaming, setRenaming] = useState<RenameTarget | null>(null);
    const [deletingSession, setDeletingSession] = useState<GroupSession | null>(null);
    const [deletingGroup, setDeletingGroup] = useState(false);
    const [showSettings, setShowSettings] = useState(false);
    const groupActivityRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const appliedRealtimeMessageIdsRef = useRef<Set<string>>(new Set());
    const latestRealtimeMessageBySessionRef = useRef<Map<string, GroupMessage>>(new Map());
    const readRequestsRef = useRef<Set<string>>(new Set());
    const lastReadMessageBySessionRef = useRef<Map<string, string>>(new Map());

    const {
        data: groups = [],
        isFetchedAfterMount: groupsFetchedAfterMount,
        isRefetchError: groupsRefetchError,
        refetch: refetchGroups,
    } = useQuery({
        queryKey: ['groups'],
        queryFn: () => groupApi.list(),
        refetchOnMount: 'always',
    });
    // A cached group list is only a rendering hint, never an authorization fact. Wait for this
    // mount's list response before using a URL group ID as the scope for any child request.
    const groupsReady = groupsFetchedAfterMount && !groupsRefetchError;

    const activeGroup = groupsReady ? groups.find((group) => group.id === groupId) : undefined;

    const { data: sessions = [], refetch: refetchSessions } = useQuery({
        queryKey: ['group-sessions', groupId],
        queryFn: () => groupApi.sessions(groupId!),
        enabled: Boolean(activeGroup),
    });

    const { data: members = [], refetch: refetchMembers } = useQuery({
        queryKey: ['group-members', groupId],
        queryFn: () => groupApi.members(groupId!),
        enabled: Boolean(activeGroup),
    });

    // The tree shows every group's sessions and a per-group unread + last-activity roll-up, but the
    // list endpoint carries neither — so pull each group's sessions and aggregate on the client. The
    // active group's entry shares the ['group-sessions', groupId] key with the query above, so it is
    // fetched once and both observers read the same cache.
    const groupSessionQueries = useQueries({
        queries: (groupsReady ? groups : []).map((group) => ({
            queryKey: ['group-sessions', group.id],
            queryFn: () => groupApi.sessions(group.id),
        })),
    });

    const sessionsByGroup = useMemo(() => {
        const map: Record<string, GroupSession[]> = {};
        groups.forEach((group, index) => {
            map[group.id] = groupSessionQueries[index]?.data ?? [];
        });
        return map;
    }, [groups, groupSessionQueries]);

    const groupRollups = useMemo(() => {
        const map: Record<string, { unread: number; lastActivity: string | null }> = {};
        for (const group of groups) {
            const groupSessions = sessionsByGroup[group.id] ?? [];
            const unread = groupSessions.reduce((sum, session) => sum + session.unread_count, 0);
            const lastActivity = groupSessions.reduce<string | null>((latest, session) => {
                if (!session.last_message_at) return latest;
                return !latest || session.last_message_at > latest ? session.last_message_at : latest;
            }, null);
            map[group.id] = { unread, lastActivity };
        }
        return map;
    }, [groups, sessionsByGroup]);

    const activeSession = sessions.find((session) => session.id === sessionId);
    const activeGroupId = activeGroup?.id;
    const activeSessionId = activeSession?.id;

    const { data: activeRunStates = [], refetch: refetchActiveRuns } = useQuery({
        queryKey: ['group-active-runs', groupId, sessionId],
        queryFn: () => groupApi.activeRuns(groupId!, sessionId!),
        enabled: Boolean(activeGroup && activeSession),
        retry: false,
        refetchInterval: (query) => (
            query.state.data?.some((run) => run.can_cancel) ? 1000 : false
        ),
    });
    const activeRunIds = activeRunStates
        .filter((run) => run.can_cancel)
        .map((run) => run.run_id);
    const isPlanning = activeRunStates.some(
        (run) => run.can_cancel && run.system_role === 'group_planning',
    );
    const runningAgents = useMemo(() => {
        const membersByAgentId = new Map(
            members
                .filter((member) => member.participant_type === 'agent')
                .map((member) => [member.participant_ref_id, member]),
        );
        const seen = new Set<string>();
        return activeRunStates.flatMap((run) => {
            if (!run.can_cancel || !run.agent_id || seen.has(run.agent_id)) return [];
            const member = membersByAgentId.get(run.agent_id);
            if (!member) return [];
            seen.add(run.agent_id);
            return [{ id: run.agent_id, name: member.display_name }];
        });
    }, [activeRunStates, members]);

    const me = useMemo(
        () => members.find(
            (member) => member.participant_type === 'user'
                && member.participant_ref_id === currentUser?.id,
        ),
        [members, currentUser?.id],
    );
    const isManager = me?.role === 'manager';

    // Header facts line: the group's makeup, which does not change as the session switches.
    const memberCounts = useMemo(() => ({
        agents: members.filter((member) => member.participant_type === 'agent').length,
        people: members.filter((member) => member.participant_type === 'user').length,
    }), [members]);

    // Land on a group, then on a session, so the pane is never pointing at nothing.
    useEffect(() => {
        if (groupsReady && !groupId && groups.length > 0) {
            navigate(`/groups/${groups[0].id}`, { replace: true });
        }
    }, [groupId, groups, groupsReady, navigate]);

    useEffect(() => {
        if (groupsReady && groupId && !activeGroup) {
            navigate('/groups', { replace: true });
        }
    }, [activeGroup, groupId, groupsReady, navigate]);

    useEffect(() => {
        if (!groupId || sessionId || sessions.length === 0) return;
        const landing = sessions.find((session) => session.is_primary) ?? sessions[0];
        navigate(`/groups/${groupId}/${landing.id}`, { replace: true });
    }, [groupId, sessionId, sessions, navigate]);

    // The active group is always expanded in the tree; the user can still collapse it by hand.
    useEffect(() => {
        if (!groupId) return;
        setExpandedGroups((current) => {
            if (current.has(groupId)) return current;
            const next = new Set(current);
            next.add(groupId);
            return next;
        });
    }, [groupId]);

    // Load the newest page whenever the session changes.
    useEffect(() => {
        if (!activeGroupId || !activeSessionId) {
            setMessages([]);
            setHasMore(false);
            return;
        }
        let cancelled = false;
        setMessages([]);
        setHasMore(false);
        void groupApi
            .messages(activeGroupId, activeSessionId, { limit: HISTORY_PAGE_SIZE })
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
    }, [activeGroupId, activeSessionId, toast, t]);

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

    const onGroupActivity = useCallback((activity: GroupActivity) => {
        const activityGroupId = activeGroup?.id;
        if (!activityGroupId) return;

        if (!activity.sessionId || activity.sessionId === sessionId) {
            void queryClient.invalidateQueries({
                queryKey: ['group-active-runs', activityGroupId, sessionId],
                exact: true,
            });
        }

        const { message, sessionId: activitySessionId } = activity;
        const isUnreadForMe = Boolean(
            message
            && activitySessionId
            && me
            && message.participant_id !== me?.participant_id,
        );
        if (isUnreadForMe && message && activitySessionId) {
            const previousLatest = latestRealtimeMessageBySessionRef.current.get(activitySessionId);
            if (!previousLatest || compareCursor(message.cursor, previousLatest.cursor) > 0) {
                latestRealtimeMessageBySessionRef.current.set(activitySessionId, message);
            }
        }
        if (
            isUnreadForMe
            && message
            && activitySessionId
            && !appliedRealtimeMessageIdsRef.current.has(message.id)
        ) {
            appliedRealtimeMessageIdsRef.current.add(message.id);
            if (appliedRealtimeMessageIdsRef.current.size > 1000) {
                const oldestMessageId = appliedRealtimeMessageIdsRef.current.values().next().value;
                if (oldestMessageId) appliedRealtimeMessageIdsRef.current.delete(oldestMessageId);
            }
            queryClient.setQueryData<GroupSession[]>(
                ['group-sessions', activityGroupId],
                (current) => current?.map((session) => (
                    session.id === activitySessionId
                        ? {
                            ...session,
                            unread_count: session.unread_count + 1,
                            last_message_at: message.created_at,
                        }
                        : session
                )),
            );
        }

        // Coalesce a burst, then replace optimistic counts with the backend's member+session truth.
        if (groupActivityRefreshTimerRef.current) {
            clearTimeout(groupActivityRefreshTimerRef.current);
        }
        groupActivityRefreshTimerRef.current = setTimeout(() => {
            groupActivityRefreshTimerRef.current = null;
            void queryClient.invalidateQueries({
                queryKey: ['group-sessions', activityGroupId],
                exact: true,
            });
        }, 150);
    }, [activeGroup?.id, me, queryClient, sessionId]);

    useEffect(() => {
        appliedRealtimeMessageIdsRef.current.clear();
        latestRealtimeMessageBySessionRef.current.clear();
        return () => {
            if (groupActivityRefreshTimerRef.current) {
                clearTimeout(groupActivityRefreshTimerRef.current);
                groupActivityRefreshTimerRef.current = null;
            }
        };
    }, [groupId]);

    // Called for its transport side effects; the header no longer surfaces connection status.
    useGroupRealtime({
        groupId: activeGroup?.id,
        sessionId: activeSession?.id,
        getLastCursor,
        onMessages: receiveMessages,
        onGroupActivity,
    });

    const markLatestMessageSeen = useCallback((messageId: string) => {
        if (!activeGroup || !activeSession) return;
        const targetGroupId = activeGroup.id;
        const targetSessionId = activeSession.id;
        const requestKey = `${targetSessionId}:${messageId}`;
        if (
            readRequestsRef.current.has(requestKey)
            || lastReadMessageBySessionRef.current.get(targetSessionId) === messageId
        ) return;

        readRequestsRef.current.add(requestKey);
        void groupApi.markSessionRead(targetGroupId, targetSessionId, messageId)
            .then(async (readState) => {
                lastReadMessageBySessionRef.current.set(
                    targetSessionId,
                    readState.last_read_message_id,
                );
                // Prevent an older in-flight sessions response from restoring the cleared badge.
                await queryClient.cancelQueries({
                    queryKey: ['group-sessions', targetGroupId],
                    exact: true,
                });
                const latestRealtimeMessageId = latestRealtimeMessageBySessionRef.current
                    .get(targetSessionId)?.id;
                if (
                    latestRealtimeMessageId === undefined
                    || readState.last_read_message_id === latestRealtimeMessageId
                ) {
                    queryClient.setQueryData<GroupSession[]>(
                        ['group-sessions', targetGroupId],
                        (current) => current?.map((session) => (
                            session.id === targetSessionId
                                ? { ...session, unread_count: 0 }
                                : session
                        )),
                    );
                    if (readState.last_read_message_id === latestRealtimeMessageId) {
                        latestRealtimeMessageBySessionRef.current.delete(targetSessionId);
                    }
                }
                await queryClient.invalidateQueries({
                    queryKey: ['group-sessions', targetGroupId],
                    exact: true,
                });
            })
            .catch(() => undefined)
            .finally(() => readRequestsRef.current.delete(requestKey));
    }, [activeGroup, activeSession, queryClient]);

    const persistToggle = (
        key: string,
        setter: React.Dispatch<React.SetStateAction<boolean>>,
    ) => () => setter((current) => {
        localStorage.setItem(key, current ? '0' : '1');
        return !current;
    });

    const toggleGroups = persistToggle('groups.groupsCollapsed', setGroupsCollapsed);
    const togglePanel = persistToggle('groups.showPanel', setShowPanel);

    const toggleGroupExpand = (id: string) => setExpandedGroups((current) => {
        const next = new Set(current);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
    });

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
                message_id: createRandomUUID(),
            });
            setMessages((previous) => mergeMessages(previous, [intake.message]));
            if (intake.run_ids.length > 0) void refetchActiveRuns();

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

    const cancelActiveRuns = async () => {
        if (!groupId || !sessionId || activeRunIds.length === 0 || cancellingRuns) return;
        setCancellingRuns(true);
        try {
            await Promise.all(
                activeRunIds.map((runId) => groupApi.cancelRun(groupId, sessionId, runId)),
            );
            await refetchActiveRuns();
            toast.info(t('groups.cancelAccepted', '已请求停止当前运行'));
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.cancelFailed', '停止运行失败'));
        } finally {
            setCancellingRuns(false);
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
        const targetGroupId = creatingSession;
        setCreatingSession(null);
        if (!targetGroupId) return;
        try {
            const session = await groupApi.createSession(
                targetGroupId,
                title.trim() ? { title: title.trim() } : {},
            );
            await queryClient.invalidateQueries({ queryKey: ['group-sessions', targetGroupId] });
            setExpandedGroups((current) => new Set(current).add(targetGroupId));
            navigate(`/groups/${targetGroupId}/${session.id}`);
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.createSessionFailed', '创建会话失败'));
        }
    };

    // Inline rename — an empty or unchanged value keeps the current title.
    const commitRename = async (value: string) => {
        const target = renaming;
        setRenaming(null);
        if (!target || !value || value === target.current) return;
        try {
            await groupApi.renameSession(target.groupId, target.sessionId, value);
            await queryClient.invalidateQueries({ queryKey: ['group-sessions', target.groupId] });
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.renameSessionFailed', '重命名失败'));
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

    const deleteGroup = async () => {
        if (!groupId) return;
        try {
            await groupApi.remove(groupId);
            setDeletingGroup(false);
            setShowPanel(false);
            const remaining = await refetchGroups();
            const next = remaining.data?.find((group) => group.id !== groupId);
            navigate(next ? `/groups/${next.id}` : '/groups', { replace: true });
            toast.success(t('groups.deleteGroupDone', '群聊已删除'));
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.deleteGroupFailed', '删除群聊失败'));
        }
    };

    const totalUnread = groups.reduce(
        (sum, group) => sum + (groupRollups[group.id]?.unread ?? 0),
        0,
    );

    return (
        <div className="groups-page">
            <div className={`group-column tree ${groupsCollapsed ? 'collapsed' : ''}`}>
                {groupsCollapsed ? (
                    <button
                        type="button"
                        className="group-rail-stub"
                        title={t('groups.expandGroups', '展开群聊栏')}
                        onClick={toggleGroups}
                    >
                        <span className="group-rail-stub-icon">
                            <IconLayoutSidebarLeftExpand size={16} stroke={1.7} />
                            {totalUnread > 0 && <span className="group-rail-dot" />}
                        </span>
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
                            {groups.map((group) => {
                                const expanded = expandedGroups.has(group.id);
                                const groupSessions = sessionsByGroup[group.id] ?? [];
                                const rollup = groupRollups[group.id];
                                const isActiveGroup = group.id === groupId;
                                return (
                                    <div className="group-node" key={group.id}>
                                        <div className={`group-row group ${isActiveGroup ? 'active' : ''}`}>
                                            <button
                                                type="button"
                                                className="group-row-caret"
                                                title={expanded
                                                    ? t('groups.collapseGroup', '折叠')
                                                    : t('groups.expandGroup', '展开')}
                                                onClick={() => toggleGroupExpand(group.id)}
                                            >
                                                {expanded
                                                    ? <IconChevronDown size={14} stroke={1.8} />
                                                    : <IconChevronRight size={14} stroke={1.8} />}
                                            </button>
                                            <button
                                                type="button"
                                                className="group-row-main"
                                                onClick={() => navigate(`/groups/${group.id}`)}
                                            >
                                                <span className="group-row-name">{group.name}</span>
                                                {rollup && rollup.unread > 0 && (
                                                    <span className="group-unread">{rollup.unread}</span>
                                                )}
                                                {rollup?.lastActivity && (
                                                    <span className="group-row-time">
                                                        {formatLastActivity(rollup.lastActivity)}
                                                    </span>
                                                )}
                                            </button>
                                            <button
                                                type="button"
                                                className="group-icon-btn subtle"
                                                title={t('groups.newSession', '新建会话')}
                                                onClick={() => setCreatingSession(group.id)}
                                            >
                                                <IconPlus size={14} stroke={1.8} />
                                            </button>
                                            <button
                                                type="button"
                                                className="group-icon-btn subtle"
                                                title={t('groups.settings', '群设置')}
                                                onClick={() => {
                                                    if (!isActiveGroup) navigate(`/groups/${group.id}`);
                                                    setShowSettings(true);
                                                }}
                                            >
                                                <IconSettings size={14} stroke={1.8} />
                                            </button>
                                        </div>

                                        {expanded && (
                                            <div className="group-node-children">
                                                {groupSessions.length === 0 ? (
                                                    <button
                                                        type="button"
                                                        className="group-session-empty"
                                                        onClick={() => setCreatingSession(group.id)}
                                                    >
                                                        <IconPlus size={13} stroke={1.8} />
                                                        {t('groups.newSession', '新建会话')}
                                                    </button>
                                                ) : (
                                                    groupSessions.map((session) => {
                                                        const isRenaming = renaming?.sessionId === session.id;
                                                        return (
                                                            <div
                                                                key={session.id}
                                                                className={`group-row session ${session.id === sessionId ? 'active' : ''}`}
                                                            >
                                                                {isRenaming ? (
                                                                    <span className="group-row-main">
                                                                        <IconMessage2 size={14} stroke={1.6} />
                                                                        <InlineEdit
                                                                            className="group-inline-input"
                                                                            initialValue={session.title}
                                                                            placeholder={t('groups.sessionNamePlaceholder', '会话名称')}
                                                                            onCommit={commitRename}
                                                                            onCancel={() => setRenaming(null)}
                                                                        />
                                                                    </span>
                                                                ) : (
                                                                    <button
                                                                        type="button"
                                                                        className="group-row-main"
                                                                        onClick={() => navigate(`/groups/${group.id}/${session.id}`)}
                                                                    >
                                                                        <IconMessage2 size={14} stroke={1.6} />
                                                                        <span className="group-row-name">{session.title}</span>
                                                                        {session.unread_count > 0 && (
                                                                            <span className="group-unread">{session.unread_count}</span>
                                                                        )}
                                                                    </button>
                                                                )}
                                                                {/* Any member can rename a session; only managers can delete it. */}
                                                                {isActiveGroup && me && !isRenaming && (
                                                                    <button
                                                                        type="button"
                                                                        className="group-icon-btn subtle"
                                                                        title={t('groups.renameSession', '重命名会话')}
                                                                        onClick={() => setRenaming({
                                                                            groupId: group.id,
                                                                            sessionId: session.id,
                                                                            current: session.title,
                                                                        })}
                                                                    >
                                                                        <IconPencil size={13} stroke={1.7} />
                                                                    </button>
                                                                )}
                                                                {isManager && isActiveGroup && !isRenaming && (
                                                                    <button
                                                                        type="button"
                                                                        className="group-icon-btn subtle danger"
                                                                        title={t('groups.deleteSession', '删除会话')}
                                                                        onClick={() => setDeletingSession(session)}
                                                                    >
                                                                        <IconTrash size={14} stroke={1.7} />
                                                                    </button>
                                                                )}
                                                            </div>
                                                        );
                                                    })
                                                )}
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </>
                )}
            </div>

            <div className="group-main">
                {activeGroup && activeSession ? (
                    <>
                        <header className="group-main-header">
                            <div className="group-main-heading">
                                <div className="group-main-title">{activeSession.title}</div>
                                <div className="group-main-subtitle">
                                    {t('groups.agentCount', '{{count}} 个智能体', { count: memberCounts.agents })}
                                    {' · '}
                                    {t('groups.memberCount', '{{count}} 位成员', { count: memberCounts.people })}
                                </div>
                            </div>
                            <button
                                type="button"
                                className={`group-icon-btn ${showPanel ? 'active' : ''}`}
                                title={showPanel
                                    ? t('groups.hidePanel', '收起面板')
                                    : t('groups.showPanel', '展开面板')}
                                onClick={togglePanel}
                            >
                                {showPanel
                                    ? <IconLayoutSidebarRightCollapse size={16} stroke={1.7} />
                                    : <IconLayoutSidebarRightExpand size={16} stroke={1.7} />}
                            </button>
                        </header>

                        <MessageStream
                            sessionId={activeSession.id}
                            messages={messages}
                            members={members}
                            myParticipantId={me?.participant_id}
                            hasMore={hasMore}
                            loadingMore={loadingMore}
                            isPlanning={isPlanning}
                            runningAgents={runningAgents}
                            onLoadMore={() => void loadMore()}
                            onLatestMessageSeen={markLatestMessageSeen}
                        />

                        <MessageComposer
                            members={members}
                            canCancel={activeRunIds.length > 0}
                            cancelling={cancellingRuns}
                            onCancel={() => void cancelActiveRuns()}
                            onSend={sendMessage}
                        />
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
                    groupName={activeGroup.name}
                    members={members}
                    onInvite={() => setShowInvite(true)}
                    onOpenSettings={() => setShowSettings(true)}
                    onClose={() => setShowPanel(false)}
                />
            )}

            {showSettings && activeGroup && (
                <GroupSettingsModal
                    group={activeGroup}
                    members={members}
                    isManager={isManager}
                    onClose={() => setShowSettings(false)}
                    onInvite={() => setShowInvite(true)}
                    onUpdated={() => void refetchGroups()}
                    onMembersChanged={() => void refetchMembers()}
                    onRequestDissolve={() => {
                        setShowSettings(false);
                        setDeletingGroup(true);
                    }}
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
                open={Boolean(creatingSession)}
                title={t('groups.newSession', '新建会话')}
                placeholder={t('groups.sessionTitlePlaceholder', '会话名称，可留空')}
                allowEmpty
                onConfirm={(value) => void createSession(value)}
                onCancel={() => setCreatingSession(null)}
            />

            <ConfirmModal
                open={Boolean(deletingSession)}
                title={t('groups.deleteSession', '删除会话')}
                message={t('groups.deleteSessionConfirm', '删除后该会话的消息不再可见，且无法恢复。')}
                danger
                onConfirm={() => void deleteSession()}
                onCancel={() => setDeletingSession(null)}
            />

            <ConfirmModal
                open={deletingGroup}
                title={t('groups.deleteGroup', '删除群聊')}
                message={t('groups.deleteGroupConfirm', '删除后该群的所有会话、消息和文件都将无法恢复。')}
                danger
                onConfirm={() => void deleteGroup()}
                onCancel={() => setDeletingGroup(false)}
            />
        </div>
    );
}
