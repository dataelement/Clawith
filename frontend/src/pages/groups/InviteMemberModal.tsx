import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { IconRobot, IconSearch, IconUser, IconX } from '@tabler/icons-react';
import { agentApi, fetchJson } from '../../services/api';
import { groupApi } from '../../services/groupApi';
import { useToast } from '../../components/Toast/ToastProvider';
import type { Agent, User } from '../../types';
import type { GroupMember, ParticipantType } from '../../types/group';

interface InviteMemberModalProps {
    groupId: string;
    members: GroupMember[];
    onClose: () => void;
    onInvited: () => void;
}

interface Candidate {
    type: ParticipantType;
    refId: string;
    name: string;
    hint?: string;
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

    const { data: agents = [] } = useQuery({
        queryKey: ['group-invite-agents'],
        queryFn: () => agentApi.list(),
    });

    // /org/users is tenant-scoped and open to any member, unlike the admin-only /users.
    const { data: users = [] } = useQuery({
        queryKey: ['group-invite-users'],
        queryFn: () => fetchJson<User[]>('/org/users'),
    });

    const alreadyIn = useMemo(
        () => new Set(members.map((member) => `${member.participant_type}:${member.participant_ref_id}`)),
        [members],
    );

    const candidates = useMemo<Candidate[]>(() => {
        const source: Candidate[] = tab === 'agent'
            ? agents.map((agent: Agent) => ({
                type: 'agent' as const,
                refId: agent.id,
                name: agent.name,
                hint: agent.role_description ?? undefined,
            }))
            : users.map((user: User) => ({
                type: 'user' as const,
                refId: user.id,
                name: user.display_name || user.email,
                hint: user.email,
            }));

        const needle = search.trim().toLowerCase();
        return source
            .filter((candidate) => !alreadyIn.has(`${candidate.type}:${candidate.refId}`))
            .filter((candidate) => !needle || candidate.name.toLowerCase().includes(needle));
    }, [tab, agents, users, alreadyIn, search]);

    const invite = async (candidate: Candidate) => {
        setInviting(candidate.refId);
        try {
            await groupApi.inviteMember(groupId, {
                participant_type: candidate.type,
                ref_id: candidate.refId,
            });
            toast.success(t('groups.inviteOk', '{{name}} 已入群', { name: candidate.name }));
            onInvited();
        } catch (error: any) {
            // The backend still requires `participant_id`, which nothing exposes to us. Until it
            // accepts (type, ref_id), every invite lands here. See the contract doc, gap 3.
            const message = error?.status === 422
                ? t('groups.inviteUnsupported', '后端邀请接口尚未支持按用户/智能体 ID 邀请，暂时无法加人')
                : error?.message ?? t('groups.inviteFailed', '邀请失败');
            toast.error(message);
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
                        <div key={`${candidate.type}:${candidate.refId}`} className="group-candidate">
                            <span className={`group-avatar sm ${candidate.type === 'agent' ? 'agent' : ''}`}>
                                {candidate.type === 'agent'
                                    ? <IconRobot size={14} stroke={1.6} />
                                    : <IconUser size={14} stroke={1.6} />}
                            </span>
                            <div className="group-candidate-body">
                                <div className="group-candidate-name">{candidate.name}</div>
                                {candidate.hint && (
                                    <div className="group-candidate-hint">{candidate.hint}</div>
                                )}
                            </div>
                            <button
                                type="button"
                                className="btn btn-sm"
                                disabled={inviting === candidate.refId}
                                onClick={() => void invite(candidate)}
                            >
                                {inviting === candidate.refId
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
