import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconRobot, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import { useToast } from '../../components/Toast/ToastProvider';
import ConfirmModal from '../../components/ConfirmModal';
import GroupTextFileEditor from './GroupTextFileEditor';
import GroupWorkspaceTab from './GroupWorkspaceTab';
import GroupMemoryTab from './GroupMemoryTab';
import type { GroupMember } from '../../types/group';

type PanelTab = 'members' | 'announcement' | 'workspace' | 'memory';

interface GroupSidePanelProps {
    groupId: string;
    members: GroupMember[];
    isManager: boolean;
    onInvite: () => void;
    onMembersChanged: () => void;
    onClose: () => void;
}

export default function GroupSidePanel({
    groupId,
    members,
    isManager,
    onInvite,
    onMembersChanged,
    onClose,
}: GroupSidePanelProps) {
    const { t } = useTranslation();
    const toast = useToast();
    const [tab, setTab] = useState<PanelTab>('members');
    const [removing, setRemoving] = useState<GroupMember | null>(null);

    const people = members.filter((member) => member.participant_type === 'user');
    const agents = members.filter((member) => member.participant_type === 'agent');

    const removeMember = async () => {
        if (!removing) return;
        try {
            await groupApi.removeMember(groupId, removing.id);
            toast.success(t('groups.removed', '已移出 {{name}}', { name: removing.display_name }));
            onMembersChanged();
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.removeFailed', '移出成员失败'));
        } finally {
            setRemoving(null);
        }
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
                </div>
                {(member.role_description || member.title) && (
                    <div className="group-member-hint">{member.role_description || member.title}</div>
                )}
            </div>
            {isManager && member.role !== 'manager' && (
                <button
                    type="button"
                    className="group-icon-btn subtle"
                    title={t('groups.remove', '移出群聊')}
                    onClick={() => setRemoving(member)}
                >
                    <IconX size={14} stroke={1.7} />
                </button>
            )}
        </div>
    );

    const TABS: { key: PanelTab; label: string }[] = [
        { key: 'members', label: `${t('groups.members', '成员')} · ${members.length}` },
        { key: 'announcement', label: t('groups.announcement', '群公告') },
        { key: 'workspace', label: t('groups.workspace', '文件') },
        { key: 'memory', label: t('groups.memory', '记忆') },
    ];

    return (
        <aside className="group-side-panel">
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
                <button type="button" className="group-icon-btn" onClick={onClose}>
                    <IconX size={16} stroke={1.7} />
                </button>
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

            <ConfirmModal
                open={Boolean(removing)}
                title={t('groups.remove', '移出群聊')}
                message={t('groups.removeConfirm', '确定将 {{name}} 移出群聊？', {
                    name: removing?.display_name ?? '',
                })}
                danger
                onConfirm={() => void removeMember()}
                onCancel={() => setRemoving(null)}
            />
        </aside>
    );
}
