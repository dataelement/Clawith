import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { IconRobot, IconSearch, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import { useToast } from '../../components/Toast/ToastProvider';
import type { GroupMember, GroupMemberCandidate, ParticipantType } from '../../types/group';

interface InviteMemberModalProps {
    groupId: string;
    members: GroupMember[];
    onClose: () => void;
    onInvited: () => void;
}

export default function InviteMemberModal({
    groupId,
    members,
    onClose,
    onInvited,
}: InviteMemberModalProps) {
    const { t } = useTranslation();
    const toast = useToast();
    const [tab, setTab] = useState<ParticipantType>('agent');
    const [search, setSearch] = useState('');
    const [inviting, setInviting] = useState<string | null>(null);

    const { data: backendCandidates = [] } = useQuery({
        queryKey: ['group-member-candidates', groupId, tab],
        queryFn: () => groupApi.memberCandidates(groupId, tab),
    });

    const alreadyIn = useMemo(
        () => new Set(members.map((member) => member.participant_id)),
        [members],
    );

    const candidates = useMemo<GroupMemberCandidate[]>(() => {
        const needle = search.trim().toLowerCase();
        return backendCandidates
            .filter((candidate) => !alreadyIn.has(candidate.participant_id))
            .filter((candidate) => {
                if (!needle) return true;
                return [candidate.display_name, candidate.role_description, candidate.title]
                    .some((value) => value?.toLowerCase().includes(needle));
            });
    }, [backendCandidates, alreadyIn, search]);

    const invite = async (candidate: GroupMemberCandidate) => {
        setInviting(candidate.participant_id);
        try {
            await groupApi.inviteMember(groupId, {
                participant_id: candidate.participant_id,
            });
            toast.success(t('groups.inviteOk', '{{name}} 已入群', { name: candidate.display_name }));
            onInvited();
        } catch (error: any) {
            toast.error(error?.message ?? t('groups.inviteFailed', '邀请失败'));
        } finally {
            setInviting(null);
        }
    };

    return (
        <div className="group-modal-backdrop" onClick={onClose}>
            <div className="group-modal" onClick={(event) => event.stopPropagation()}>
                <div className="group-modal-header">
                    <h3>{t('groups.inviteTitle', '邀请成员')}</h3>
                    <button type="button" className="group-icon-btn" onClick={onClose}>
                        <IconX size={16} stroke={1.7} />
                    </button>
                </div>

                <div className="group-tabs">
                    <button
                        type="button"
                        className={`group-tab ${tab === 'agent' ? 'active' : ''}`}
                        onClick={() => setTab('agent')}
                    >
                        {t('groups.tabAgents', '智能体')}
                    </button>
                    <button
                        type="button"
                        className={`group-tab ${tab === 'user' ? 'active' : ''}`}
                        onClick={() => setTab('user')}
                    >
                        {t('groups.tabPeople', '成员')}
                    </button>
                </div>

                <div className="group-search">
                    <IconSearch size={14} stroke={1.6} />
                    <input
                        className="group-search-input"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder={t('groups.searchPlaceholder', '搜索名称')}
                    />
                </div>

                <div className="group-candidate-list">
                    {candidates.length === 0 && (
                        <div className="group-empty-hint">
                            {t('groups.noCandidates', '没有可邀请的对象')}
                        </div>
                    )}
                    {candidates.map((candidate) => (
                        <div key={candidate.participant_id} className="group-candidate">
                            <span className={`group-avatar sm ${candidate.participant_type === 'agent' ? 'agent' : ''}`}>
                                {candidate.participant_type === 'agent'
                                    ? <IconRobot size={14} stroke={1.6} />
                                    : <IconUser size={14} stroke={1.6} />}
                            </span>
                            <div className="group-candidate-body">
                                <div className="group-candidate-name">{candidate.display_name}</div>
                                {(candidate.role_description || candidate.title) && (
                                    <div className="group-candidate-hint">
                                        {candidate.role_description || candidate.title}
                                    </div>
                                )}
                            </div>
                            <button
                                type="button"
                                className="btn btn-sm"
                                disabled={inviting === candidate.participant_id}
                                onClick={() => void invite(candidate)}
                            >
                                {inviting === candidate.participant_id
                                    ? t('common.loading', '加载中...')
                                    : t('groups.invite', '邀请')}
                            </button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
