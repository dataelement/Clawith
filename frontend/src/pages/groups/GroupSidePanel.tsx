import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconCheck, IconCircleDot, IconPlus, IconRobot, IconSettings, IconSparkles, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import GroupTextFileEditor from './GroupTextFileEditor';
import GroupWorkspaceTab from './GroupWorkspaceTab';
import GroupMemoryTab from './GroupMemoryTab';
import type { GroupMember, ProjectGroupDecision, ProjectGroupTask } from '../../types/group';

type PanelTab = 'members' | 'tasks' | 'decisions' | 'announcement' | 'workspace' | 'memory';

const PANEL_WIDTH_KEY = 'groups.panelWidth';
// Default sized by measurement, not taste: a long realistic member name — 12 CJK chars plus the
// "Manager" badge — measures ~221px, and the row chrome (16*2 body padding + 24 avatar + 8 gap)
// adds 64, so ~285px fits it on one line; 300 leaves a little slack. Min/max bound the drag.
const PANEL_DEFAULT_WIDTH = 300;
const PANEL_MIN_WIDTH = 240;
const PANEL_MAX_WIDTH = 520;

const clampWidth = (value: number) =>
    Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, value));

interface GroupSidePanelProps {
    groupId: string;
    groupName: string;
    members: GroupMember[];
    onInvite: () => void;
    onOpenSettings: () => void;
    onClose: () => void;
}

/**
 * The group-level side panel: a fixed header naming the group (so it reads as group-scoped and does
 * not change when the session switches), then tabs for members, announcement, files and memory. It
 * is view-and-invite only — renaming, removing members and dissolving live in the settings modal
 * behind the gear.
 */
export default function GroupSidePanel({
    groupId,
    groupName,
    members,
    onInvite,
    onOpenSettings,
    onClose,
}: GroupSidePanelProps) {
    const { t } = useTranslation();
    const [tab, setTab] = useState<PanelTab>('members');
    const [tasks, setTasks] = useState<ProjectGroupTask[] | null>(null);
    const [tasksError, setTasksError] = useState(false);
    const [startingTasks, setStartingTasks] = useState(false);
    const [decisions, setDecisions] = useState<ProjectGroupDecision[] | null>(null);
    const [decisionsError, setDecisionsError] = useState(false);
    const [decisionDrafts, setDecisionDrafts] = useState<Record<string, string>>({});
    const [generatingDecisionId, setGeneratingDecisionId] = useState<string | null>(null);
    const [replyingDecisionId, setReplyingDecisionId] = useState<string | null>(null);
    const [decisionReplyErrors, setDecisionReplyErrors] = useState<Record<string, string>>({});

    const reloadProjectTasks = () => {
        setTasksError(false);
        return groupApi.projectTasks(groupId)
            .then(setTasks)
            .catch(() => {
                setTasks([]);
                setTasksError(true);
            });
    };

    const reloadProjectDecisions = () => {
        setDecisionsError(false);
        return groupApi.projectDecisions(groupId)
            .then(setDecisions)
            .catch(() => {
                setDecisions([]);
                setDecisionsError(true);
            });
    };

    useEffect(() => {
        if (tab !== 'tasks') return;
        void reloadProjectTasks();
    }, [groupId, tab]);

    useEffect(() => {
        if (tab !== 'decisions') return;
        void reloadProjectDecisions();
    }, [groupId, tab]);

    // The panel's left edge is a resize handle; the width is remembered across sessions.
    const [width, setWidth] = useState(() => {
        const stored = Number(localStorage.getItem(PANEL_WIDTH_KEY));
        return Number.isFinite(stored) && stored > 0 ? clampWidth(stored) : PANEL_DEFAULT_WIDTH;
    });
    useEffect(() => {
        localStorage.setItem(PANEL_WIDTH_KEY, String(width));
    }, [width]);

    const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
    const onResizeStart = (event: React.PointerEvent<HTMLDivElement>) => {
        dragRef.current = { startX: event.clientX, startWidth: width };
        event.currentTarget.setPointerCapture(event.pointerId);
        document.body.style.userSelect = 'none';
    };
    const onResizeMove = (event: React.PointerEvent<HTMLDivElement>) => {
        if (!dragRef.current) return;
        // The panel is on the right, so dragging its left edge leftward widens it.
        const delta = dragRef.current.startX - event.clientX;
        setWidth(clampWidth(dragRef.current.startWidth + delta));
    };
    const onResizeEnd = (event: React.PointerEvent<HTMLDivElement>) => {
        dragRef.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        document.body.style.userSelect = '';
    };

    const people = members.filter((member) => member.participant_type === 'user');
    const agents = members.filter((member) => member.participant_type === 'agent');

    const clearDecisionError = (decisionId: string) => {
        setDecisionReplyErrors((current) => {
            if (!current[decisionId]) return current;
            const next = { ...current };
            delete next[decisionId];
            return next;
        });
    };

    const generateDecisionDraft = (decision: ProjectGroupDecision) => {
        if (generatingDecisionId || replyingDecisionId) return;
        setGeneratingDecisionId(decision.id);
        clearDecisionError(decision.id);
        void groupApi.generateProjectDecisionDraft(
            groupId,
            decision.id,
            (decisionDrafts[decision.id] || '').trim(),
        )
            .then(({ draft }) => {
                setDecisionDrafts((current) => ({ ...current, [decision.id]: draft }));
            })
            .catch((error: unknown) => {
                setDecisionReplyErrors((current) => ({
                    ...current,
                    [decision.id]: error instanceof Error
                        ? error.message
                        : t('groups.decisionDraftFailed', 'AI 建议生成失败，请重试。'),
                }));
            })
            .finally(() => setGeneratingDecisionId(null));
    };

    const submitDecisionModification = (decision: ProjectGroupDecision) => {
        const instruction = (decisionDrafts[decision.id] || '').trim();
        if (!instruction || replyingDecisionId || generatingDecisionId) return;
        setReplyingDecisionId(decision.id);
        clearDecisionError(decision.id);
        void groupApi.replyProjectDecision(groupId, decision.id, instruction, 'modification')
            .then(() => {
                setDecisionDrafts((current) => {
                    const next = { ...current };
                    delete next[decision.id];
                    return next;
                });
                return reloadProjectDecisions();
            })
            .catch((error: unknown) => {
                setDecisionReplyErrors((current) => ({
                    ...current,
                    [decision.id]: error instanceof Error
                        ? error.message
                        : t('groups.decisionReplyFailed', 'AI 修改指令提交失败，请重试。'),
                }));
            })
            .finally(() => setReplyingDecisionId(null));
    };

    const renderMember = (member: GroupMember) => (
        <div key={member.id} className="group-member-row">
            <span className={`group-avatar sm ${member.participant_type === 'agent' ? 'agent' : ''}`}>
                {member.participant_type === 'agent'
                    ? <IconRobot size={14} stroke={1.6} />
                    : member.display_name.slice(0, 1).toUpperCase()}
            </span>
            <div className="group-member-body">
                <div className="group-member-name">
                    {member.display_name}
                    {member.role === 'manager' && (
                        <span className="group-badge-manager">{t('groups.manager', '群管理')}</span>
                    )}
                    {member.role === 'owner' && (
                        <span className="group-badge-manager">{t('groups.owner', '项目群主')}</span>
                    )}
                    {member.is_deleted && (
                        <span className="group-badge-deleted">{t('groups.deletedBadge', '已删除')}</span>
                    )}
                </div>
                {(member.role_description || member.title) && (
                    <div className="group-member-hint">{member.role_description || member.title}</div>
                )}
            </div>
        </div>
    );

    const TABS: { key: PanelTab; label: string }[] = [
        { key: 'members', label: `${t('groups.members', '成员')} · ${members.length}` },
        { key: 'tasks', label: t('groups.tasks', '任务') },
        { key: 'decisions', label: t('groups.decisions', '待决') },
        { key: 'announcement', label: t('groups.announcement', '群公告') },
        { key: 'workspace', label: t('groups.workspace', '文件') },
        { key: 'memory', label: t('groups.memory', '记忆') },
    ];

    return (
        <aside className="group-side-panel" style={{ width, flexBasis: width }}>
            <div
                className="group-panel-resizer"
                onPointerDown={onResizeStart}
                onPointerMove={onResizeMove}
                onPointerUp={onResizeEnd}
                onPointerCancel={onResizeEnd}
                title={t('groups.dragToResize', '拖动调整宽度')}
            />
            <div className="group-panel-topbar">
                <span className="group-panel-groupname" title={groupName}>{groupName}</span>
                <div className="group-column-actions">
                    <button
                        type="button"
                        className="group-icon-btn"
                        title={t('groups.settings', '群设置')}
                        onClick={onOpenSettings}
                    >
                        <IconSettings size={16} stroke={1.7} />
                    </button>
                    <button type="button" className="group-icon-btn" onClick={onClose}>
                        <IconX size={16} stroke={1.7} />
                    </button>
                </div>
            </div>

            <div className="group-panel-header">
                <div className="group-tabs scrollable">
                    {TABS.map(({ key, label }) => (
                        <button
                            key={key}
                            type="button"
                            className={`group-tab ${tab === key ? 'active' : ''}`}
                            onClick={() => setTab(key)}
                        >
                            {label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="group-panel-body">
                {tab === 'members' && (
                    <>
                        <button type="button" className="group-invite-btn" onClick={onInvite}>
                            <IconPlus size={14} stroke={1.8} />
                            {t('groups.inviteTitle', '邀请成员')}
                        </button>

                        {agents.length > 0 && (
                            <>
                                <div className="group-panel-label">
                                    <IconRobot size={12} stroke={1.7} />
                                    {t('groups.tabAgents', '智能体')} · {agents.length}
                                </div>
                                {agents.map(renderMember)}
                            </>
                        )}

                        <div className="group-panel-label">
                            <IconUser size={12} stroke={1.7} />
                            {t('groups.tabPeople', '成员')} · {people.length}
                        </div>
                        {people.map(renderMember)}
                    </>
                )}

                {tab === 'tasks' && (
                    <div className="project-panel-list">
                        {tasks === null && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {tasksError && <div className="group-member-hint">{t('groups.noProjectTasks', '此群尚未创建项目任务。')}</div>}
                        {tasks?.map((task) => (
                            <div key={task.id} className="group-member-row" style={{ alignItems: 'flex-start' }}>
                                <span className="group-avatar sm agent"><IconRobot size={14} stroke={1.6} /></span>
                                <div className="group-member-body">
                                    <div className="group-member-name">{task.title}</div>
                                    <div className="group-member-hint">{task.agent_name} · {task.status === 'blocked' ? '被依赖阻塞' : task.status}</div>
                                    {task.dependency_task_ids.length > 0 && (
                                        <div className="group-member-hint">{t('groups.taskDependencies', '依赖')} {task.dependency_task_ids.length} 项任务</div>
                                    )}
                                </div>
                            </div>
                        ))}
                        {tasks?.length === 0 && !tasksError && (
                            <>
                                <div className="group-member-hint">{t('groups.noProjectTasks', '此群尚未创建项目任务。')}</div>
                                <button
                                    type="button"
                                    className="group-invite-btn"
                                    disabled={startingTasks}
                                    onClick={() => {
                                        setStartingTasks(true);
                                        void groupApi.startProjectTasks(groupId)
                                            .then(reloadProjectTasks)
                                            .finally(() => setStartingTasks(false));
                                    }}
                                >
                                    <IconPlus size={14} stroke={1.8} />
                                    {startingTasks ? t('groups.startingTasks', '正在启动...') : t('groups.startProjectTasks', '启动任务流')}
                                </button>
                            </>
                        )}
                    </div>
                )}

                {tab === 'decisions' && (
                    <div className="project-panel-list">
                        {decisions === null && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {decisionsError && <div className="group-member-hint">{t('groups.noProjectDecisions', '此群没有待你决策的事项。')}</div>}
                        {decisions?.map((decision) => {
                            const draft = decisionDrafts[decision.id] || '';
                            const isGenerating = generatingDecisionId === decision.id;
                            const isReplying = replyingDecisionId === decision.id;
                            const replyError = decisionReplyErrors[decision.id];
                            return (
                                <article key={decision.id} className="project-decision-card">
                                    <div className="project-decision-kicker">
                                        <IconCircleDot size={13} stroke={1.8} />
                                        <span>{decision.requesting_agent_name || t('groups.projectLeader', '项目群主')} {t('groups.decisionRequested', '请求决策')}</span>
                                    </div>
                                    <h4>{decision.title}</h4>
                                    <p>{decision.context}</p>
                                    <div className="project-decision-ai-hint">
                                        <IconSparkles size={13} stroke={1.8} />
                                        {t('groups.decisionAiHint', 'AI 会生成建议内容；审阅后发送给群主执行。')}
                                    </div>
                                    <div className="project-decision-input-wrap">
                                        <textarea
                                            className="project-decision-input"
                                            value={draft}
                                            disabled={isGenerating || isReplying}
                                            aria-label={t('groups.decisionAiInputLabel', '给群主的修改指令')}
                                            placeholder={t('groups.decisionAiPlaceholder', '例如：将测试预算改为 300 美元，优先验证美国市场，并暂停其他渠道。')}
                                            onChange={(event) => {
                                                setDecisionDrafts((current) => ({
                                                    ...current,
                                                    [decision.id]: event.target.value,
                                                }));
                                                clearDecisionError(decision.id);
                                            }}
                                            onKeyDown={(event) => {
                                                if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                                                    event.preventDefault();
                                                    submitDecisionModification(decision);
                                                }
                                            }}
                                        />
                                        <button
                                            type="button"
                                            className="project-decision-ai-action"
                                            disabled={isGenerating || isReplying}
                                            title={t('groups.generateDecisionDraft', 'AI 生成建议')}
                                            aria-label={t('groups.generateDecisionDraft', 'AI 生成建议')}
                                            onClick={() => generateDecisionDraft(decision)}
                                        >
                                            <IconSparkles size={13} />
                                            {isGenerating ? t('groups.generatingDecisionDraft', '生成中…') : 'AI'}
                                        </button>
                                    </div>
                                    {replyError && <div className="project-decision-error">{replyError}</div>}
                                    <button
                                        type="button"
                                        className="project-decision-submit"
                                        disabled={!draft.trim() || isGenerating || isReplying}
                                        onClick={() => submitDecisionModification(decision)}
                                    >
                                        <IconCheck size={14} stroke={1.8} />
                                        {t('groups.sendToProjectLeader', '发送给群主')}
                                    </button>
                                    {isGenerating && <div className="project-decision-sending"><IconSparkles size={13} /> {t('groups.generatingDecisionDraft', '正在生成建议…')}</div>}
                                    {isReplying && <div className="project-decision-sending"><IconCheck size={13} /> {t('groups.sendingDecision', '正在提交…')}</div>}
                                </article>
                            );
                        })}
                        {decisions?.length === 0 && !decisionsError && (
                            <div className="project-decision-empty">
                                <IconCheck size={18} stroke={1.8} />
                                <span>{t('groups.noProjectDecisions', '此群没有待你决策的事项。')}</span>
                            </div>
                        )}
                    </div>
                )}

                {tab === 'announcement' && (
                    <GroupTextFileEditor
                        queryKey={['group-announcement', groupId]}
                        note={t('groups.announcementNote', '群公告会注入被 @ 智能体的上下文，用于约定群目标和协作规则。')}
                        placeholder={t('groups.announcementPlaceholder', '写下群目标、协作规则和对智能体的要求...')}
                        load={() => groupApi.announcement(groupId)}
                        save={(content, token) => groupApi.saveAnnouncement(groupId, content, token)}
                    />
                )}

                {tab === 'workspace' && <GroupWorkspaceTab groupId={groupId} />}

                {tab === 'memory' && <GroupMemoryTab groupId={groupId} members={members} />}
            </div>
        </aside>
    );
}
