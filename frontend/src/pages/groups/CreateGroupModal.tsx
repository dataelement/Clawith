import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { IconCheck, IconRobot, IconSearch, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import type { GroupMemberCandidate, ParticipantType } from '../../types/group';

interface CreateGroupModalProps {
    creating: boolean;
    onCreate: (name: string, memberParticipantIds: string[]) => void;
    onCancel: () => void;
}

export default function CreateGroupModal({ creating, onCreate, onCancel }: CreateGroupModalProps) {
    const { t } = useTranslation();
    const [name, setName] = useState('');
    const [tab, setTab] = useState<ParticipantType>('agent');
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<GroupMemberCandidate[]>([]);
    const nameRef = useRef<HTMLInputElement>(null);

    const { data: backendCandidates = [], isLoading } = useQuery({
        queryKey: ['tenant-member-candidates', tab],
        queryFn: () => groupApi.tenantMemberCandidates(tab),
    });

    const selectedIds = useMemo(
        () => new Set(selected.map((candidate) => candidate.participant_id)),
        [selected],
    );

    const candidates = useMemo(() => {
        const needle = search.trim().toLowerCase();
        if (!needle) return backendCandidates;
        return backendCandidates.filter((candidate) =>
            [candidate.display_name, candidate.role_description, candidate.title]
                .some((value) => value?.toLowerCase().includes(needle)),
        );
    }, [backendCandidates, search]);

    const toggle = (candidate: GroupMemberCandidate) => {
        setSelected((previous) =>
            previous.some((item) => item.participant_id === candidate.participant_id)
                ? previous.filter((item) => item.participant_id !== candidate.participant_id)
                : [...previous, candidate],
        );
    };

    const canConfirm = Boolean(name.trim()) && !creating;
    const confirm = () => {
        if (!canConfirm) return;
        onCreate(name.trim(), selected.map((candidate) => candidate.participant_id));
    };

    return (
        <div className="group-modal-backdrop" onClick={onCancel}>
            <div className="group-modal" onClick={(event) => event.stopPropagation()}>
                <div className="group-modal-header">
                    <h3>{t('groups.create', '创建群聊')}</h3>
                    <button type="button" className="group-icon-btn" onClick={onCancel}>
                        <IconX size={16} stroke={1.7} />
                    </button>
                </div>

                <div className="group-create-name">
                    <input
                        ref={nameRef}
                        autoFocus
                        className="input"
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        placeholder={t('groups.namePlaceholder', '群名称')}
                        onKeyDown={(event) => {
                            // Enter commits an IME candidate before it should submit the form.
                            if (event.nativeEvent.isComposing) return;
                            if (event.key === 'Enter') confirm();
                        }}
                    />
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
                            {isLoading
                                ? t('common.loading', '加载中...')
                                : t('groups.noCandidates', '没有可邀请的对象')}
                        </div>
                    )}
                    {candidates.map((candidate) => {
                        const picked = selectedIds.has(candidate.participant_id);
                        return (
                            <div
                                key={candidate.participant_id}
                                className={`group-candidate selectable ${picked ? 'picked' : ''}`}
                                onClick={() => toggle(candidate)}
                            >
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
                                <span className={`group-check ${picked ? 'on' : ''}`}>
                                    {picked && <IconCheck size={12} stroke={2.4} />}
                                </span>
                            </div>
                        );
                    })}
                </div>

                <div className="group-create-footer">
                    <span className="group-create-count">
                        {t('groups.selectedCount', '已选 {{count}} 位', { count: selected.length })}
                    </span>
                    <div className="group-create-actions">
                        <button type="button" className="btn btn-sm" onClick={onCancel}>
                            {t('common.cancel', '取消')}
                        </button>
                        <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            disabled={!canConfirm}
                            onClick={confirm}
                        >
                            {creating ? t('common.loading', '加载中...') : t('groups.create', '创建群聊')}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
