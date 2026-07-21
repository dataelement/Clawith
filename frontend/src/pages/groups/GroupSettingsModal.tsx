import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconRobot, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import { useToast } from '../../components/Toast/ToastProvider';
import ConfirmModal from '../../components/ConfirmModal';
import type { Group, GroupMember } from '../../types/group';

interface GroupSettingsModalProps {
    group: Group;
    members: GroupMember[];
    isManager: boolean;
    onClose: () => void;
    onInvite: () => void;
    onUpdated: () => void;
    onMembersChanged: () => void;
    onRequestDissolve: () => void;
}

/**
 * Group-level settings. Capabilities are shown by what the viewer may do, never surfaced-then-denied:
 * every human member may rename the group and edit its description; only a manager sees remove-member
 * and dissolve. Transferring ownership is intentionally not offered here.
 */
export default function GroupSettingsModal({
    group,
    members,
    isManager,
    onClose,
    onInvite,
    onUpdated,
    onMembersChanged,
    onRequestDissolve,
}: GroupSettingsModalProps) {
    const { t } = useTranslation();
    const toast = useToast();
    const [name, setName] = useState(group.name);
    const [description, setDescription] = useState(group.description ?? '');
    const [saving, setSaving] = useState(false);
    const [removing, setRemoving] = useState<GroupMember | null>(null);

    const dirty = name.trim() !== group.name || description !== (group.description ?? '');

    const people = members.filter((member) => member.participant_type === 'user');
    const agents = members.filter((member) => member.participant_type === 'agent');

    const saveProfile = async () => {
        if (!name.trim() || saving) return;
        setSaving(true);
        try {
            await groupApi.update(group.id, { name: name.trim(), description });
            toast.success(t('groups.settingsSaved', '已保存'));
            onUpdated();
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.settingsSaveFailed', '保存失败'));
        } finally {
            setSaving(false);
        }
    };

    const removeMember = async () => {
        if (!removing) return;
        try {
            await groupApi.removeMember(group.id, removing.id);
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
                    className="group-icon-btn subtle danger"
                    title={t('groups.remove', '移出群聊')}
                    onClick={() => setRemoving(member)}
                >
                    <IconX size={14} stroke={1.7} />
                </button>
            )}
        </div>
    );

    return (
        <div
            className="group-modal-backdrop"
            // Only a click on the backdrop itself closes — not one bubbling up from a child such as
            // the nested remove-member confirm dialog, which would otherwise dismiss this modal too.
            onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
        >
            <div className="group-modal" onClick={(event) => event.stopPropagation()}>
                <div className="group-modal-header">
                    <h3>{t('groups.settings', '群设置')}</h3>
                    <button type="button" className="group-icon-btn" onClick={onClose}>
                        <IconX size={16} stroke={1.7} />
                    </button>
                </div>

                <div className="group-modal-body">
                    <div className="group-settings-section">
                        <label className="group-settings-label" htmlFor="group-name-input">
                            {t('groups.groupName', '群名称')}
                        </label>
                        <input
                            id="group-name-input"
                            className="group-settings-input"
                            value={name}
                            maxLength={100}
                            onChange={(event) => setName(event.target.value)}
                            placeholder={t('groups.namePlaceholder', '群名称')}
                        />

                        <label className="group-settings-label" htmlFor="group-desc-input">
                            {t('groups.groupDescription', '群介绍')}
                        </label>
                        <textarea
                            id="group-desc-input"
                            className="group-settings-textarea"
                            value={description}
                            rows={3}
                            onChange={(event) => setDescription(event.target.value)}
                            placeholder={t('groups.descriptionPlaceholder', '这个群是做什么的...')}
                        />

                        <div className="group-panel-actions">
                            <button
                                type="button"
                                className="btn btn-sm"
                                disabled={!dirty || !name.trim() || saving}
                                onClick={() => void saveProfile()}
                            >
                                {saving ? t('common.loading', '加载中...') : t('common.save', '保存')}
                            </button>
                        </div>
                    </div>

                    <div className="group-settings-section">
                        <div className="group-settings-section-head">
                            <span className="group-settings-label">
                                {t('groups.members', '成员')} · {members.length}
                            </span>
                            <button type="button" className="btn btn-sm" onClick={onInvite}>
                                <IconPlus size={13} stroke={1.8} />
                                {t('groups.inviteTitle', '邀请成员')}
                            </button>
                        </div>

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
                    </div>

                    {isManager && (
                        <div className="group-settings-section">
                            <span className="group-settings-label">
                                {t('groups.dangerZone', '危险操作')}
                            </span>
                            <button
                                type="button"
                                className="group-delete-group-btn"
                                onClick={onRequestDissolve}
                            >
                                {t('groups.deleteGroup', '解散群聊')}
                            </button>
                        </div>
                    )}
                </div>
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
        </div>
    );
}
