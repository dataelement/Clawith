import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconRobot, IconSearch, IconUser } from '@tabler/icons-react';

import { fetchAuth } from './utils/fetchAuth';

const PAGE_SIZE = 50;

type DirectoryMemberType = 'all' | 'human' | 'agent';

type DirectoryMember = {
    member_type: 'human' | 'agent';
    target_member_id?: string | null;
    platform_user_id?: string | null;
    target_agent_id?: string | null;
    display_name?: string | null;
    title?: string | null;
    role_description?: string | null;
    department?: { id?: string | null; name?: string | null } | null;
    provider?: {
        provider_id?: string | null;
        provider_type?: string | null;
        open_id?: string | null;
        external_id?: string | null;
    } | null;
    access_mode?: string | null;
    can_contact: boolean;
    contact_tools?: string[];
    unavailable_reason?: string | null;
};

type DirectoryResponse = {
    ok: boolean;
    source_agent_id: string;
    query: string;
    member_type: DirectoryMemberType;
    include_uncontactable: boolean;
    returned_count: number;
    limit: number;
    offset: number;
    has_more: boolean;
    members: DirectoryMember[];
};

type CustomHumanEntry = {
    user_id: string;
    member_id?: string | null;
    display_name?: string | null;
    email?: string | null;
    title?: string | null;
    department?: string | null;
    access_level: 'use' | 'manage';
    removable: boolean;
};

type CustomAgentEntry = {
    target_agent_id: string;
    display_name?: string | null;
    role_description?: string | null;
    access_mode?: string | null;
    status?: string | null;
};

type CustomHumanCandidate = Omit<CustomHumanEntry, 'access_level' | 'removable'>;
type CustomAgentCandidate = CustomAgentEntry;
type CustomTab = 'human' | 'agent';

export default function AgentDirectory({
    agentId,
    accessMode,
    canManage = false,
}: {
    agentId: string;
    accessMode?: string | null;
    canManage?: boolean;
}) {
    const { t, i18n } = useTranslation();
    const queryClient = useQueryClient();
    const isChinese = i18n.language?.startsWith('zh');
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [memberType, setMemberType] = useState<DirectoryMemberType>('all');
    const [includeUnavailable, setIncludeUnavailable] = useState(false);
    const [offset, setOffset] = useState(0);
    const [loadedMembers, setLoadedMembers] = useState<DirectoryMember[]>([]);
    const showCustomMaintenance = accessMode === 'custom' && canManage;
    const [customTab, setCustomTab] = useState<CustomTab>('human');
    const [customSearch, setCustomSearch] = useState('');
    const [debouncedCustomSearch, setDebouncedCustomSearch] = useState('');
    const [candidateOffset, setCandidateOffset] = useState(0);
    const [loadedCandidates, setLoadedCandidates] = useState<Array<CustomHumanCandidate | CustomAgentCandidate>>([]);
    const [savingKey, setSavingKey] = useState<string | null>(null);

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search.trim()), 300);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedCustomSearch(customSearch.trim()), 300);
        return () => clearTimeout(timer);
    }, [customSearch]);

    useEffect(() => {
        setOffset(0);
        setLoadedMembers([]);
    }, [agentId, debouncedSearch, memberType, includeUnavailable]);

    useEffect(() => {
        setCandidateOffset(0);
        setLoadedCandidates([]);
    }, [agentId, customTab, debouncedCustomSearch]);

    const directoryQuery = useQuery({
        queryKey: ['agent-directory', agentId, debouncedSearch, memberType, includeUnavailable, offset],
        queryFn: () => {
            const params = new URLSearchParams({
                member_type: memberType,
                limit: String(PAGE_SIZE),
                offset: String(offset),
                include_uncontactable: includeUnavailable ? 'true' : 'false',
            });
            if (debouncedSearch) params.set('query', debouncedSearch);
            return fetchAuth<DirectoryResponse>(`/agents/${agentId}/directory?${params.toString()}`);
        },
        enabled: Boolean(agentId),
    });

    const customHumansQuery = useQuery({
        queryKey: ['agent-directory-custom-humans', agentId],
        queryFn: () => fetchAuth<{ members: CustomHumanEntry[] }>(`/agents/${agentId}/directory/custom/humans`),
        enabled: showCustomMaintenance,
    });

    const customAgentsQuery = useQuery({
        queryKey: ['agent-directory-custom-agents', agentId],
        queryFn: () => fetchAuth<{ agents: CustomAgentEntry[] }>(`/agents/${agentId}/directory/custom/agents`),
        enabled: showCustomMaintenance,
    });

    const customCandidatesQuery = useQuery({
        queryKey: ['agent-directory-custom-candidates', agentId, customTab, debouncedCustomSearch, candidateOffset],
        queryFn: () => {
            const params = new URLSearchParams({
                limit: String(PAGE_SIZE),
                offset: String(candidateOffset),
            });
            if (debouncedCustomSearch) params.set('query', debouncedCustomSearch);
            const path = customTab === 'human' ? 'human-candidates' : 'agent-candidates';
            return fetchAuth<{ candidates: Array<CustomHumanCandidate | CustomAgentCandidate>; limit: number; offset: number; has_more: boolean }>(
                `/agents/${agentId}/directory/custom/${path}?${params.toString()}`
            );
        },
        enabled: showCustomMaintenance,
    });

    useEffect(() => {
        const data = directoryQuery.data;
        if (!data) return;
        setLoadedMembers((current) => {
            if (data.offset === 0) return data.members;
            const seen = new Set(current.map((member) => `${member.member_type}:${primaryId(member)}`));
            const next = data.members.filter((member) => !seen.has(`${member.member_type}:${primaryId(member)}`));
            return [...current, ...next];
        });
    }, [directoryQuery.data]);

    useEffect(() => {
        const data = customCandidatesQuery.data;
        if (!data) return;
        setLoadedCandidates((current) => {
            if (data.offset === 0) return data.candidates;
            const idOf = (item: CustomHumanCandidate | CustomAgentCandidate) => (
                customTab === 'human' ? (item as CustomHumanCandidate).user_id : (item as CustomAgentCandidate).target_agent_id
            );
            const seen = new Set(current.map(idOf));
            return [...current, ...data.candidates.filter((item) => !seen.has(idOf(item)))];
        });
    }, [customCandidatesQuery.data, customTab]);

    const members = loadedMembers;
    const isInitialLoading = directoryQuery.isLoading && offset === 0 && members.length === 0;
    const isLoadingMore = directoryQuery.isFetching && offset > 0;
    const typeOptions: Array<{ value: DirectoryMemberType; label: string }> = [
        { value: 'all', label: t('agent.directory.all') },
        { value: 'human', label: t('agent.directory.people') },
        { value: 'agent', label: t('agent.directory.agents') },
    ];

    const providerLabel = (member: DirectoryMember) => {
        if (member.member_type === 'agent') return t('agent.directory.digitalEmployee');
        const providerType = (member.provider?.provider_type || '').trim();
        if (!providerType || providerType === 'web' || providerType === 'platform') {
            return t('agent.directory.clawithUser');
        }
        const providerLabels: Record<string, string> = {
            feishu: t('agent.directory.provider.feishu'),
            dingtalk: t('agent.directory.provider.dingtalk'),
            wecom: t('agent.directory.provider.wecom'),
            slack: t('agent.directory.provider.slack'),
            google_workspace: t('agent.directory.provider.googleWorkspace'),
            microsoft_teams: t('agent.directory.provider.microsoftTeams'),
        };
        return providerLabels[providerType] || t('agent.directory.provider.fallback', { provider: providerType });
    };

    const primaryId = (member: DirectoryMember) => (
        member.member_type === 'agent'
            ? member.target_agent_id
            : member.target_member_id
    ) || '';

    const secondaryText = (member: DirectoryMember) => {
        if (member.member_type === 'agent') {
            return member.role_description || member.access_mode || '';
        }
        const parts = [member.title, member.department?.name].filter(Boolean);
        return parts.join(' · ');
    };

    const renderContactTools = (member: DirectoryMember) => {
        const tools = member.contact_tools || [];
        if (!tools.length) return <span style={{ color: 'var(--text-tertiary)' }}>—</span>;
        return tools.map((tool) => (
            <span key={tool} className="badge" style={{ fontSize: '10px' }}>
                {tool}
            </span>
        ));
    };

    const refreshDirectory = () => {
        queryClient.invalidateQueries({ queryKey: ['agent-directory', agentId] });
        queryClient.invalidateQueries({ queryKey: ['agent-directory-custom-humans', agentId] });
        queryClient.invalidateQueries({ queryKey: ['agent-directory-custom-agents', agentId] });
        queryClient.invalidateQueries({ queryKey: ['agent-directory-custom-candidates', agentId] });
    };

    const addCustomCandidate = async (candidate: CustomHumanCandidate | CustomAgentCandidate) => {
        const key = customTab === 'human' ? (candidate as CustomHumanCandidate).user_id : (candidate as CustomAgentCandidate).target_agent_id;
        setSavingKey(`add:${key}`);
        try {
            if (customTab === 'human') {
                await fetchAuth(`/agents/${agentId}/directory/custom/humans`, {
                    method: 'POST',
                    body: JSON.stringify({ user_id: (candidate as CustomHumanCandidate).user_id }),
                });
            } else {
                await fetchAuth(`/agents/${agentId}/directory/custom/agents`, {
                    method: 'POST',
                    body: JSON.stringify({ target_agent_id: (candidate as CustomAgentCandidate).target_agent_id }),
                });
            }
            setLoadedCandidates((current) => current.filter((item) => (
                customTab === 'human'
                    ? (item as CustomHumanCandidate).user_id !== key
                    : (item as CustomAgentCandidate).target_agent_id !== key
            )));
            refreshDirectory();
        } finally {
            setSavingKey(null);
        }
    };

    const removeCustomHuman = async (entry: CustomHumanEntry) => {
        setSavingKey(`remove:${entry.user_id}`);
        try {
            await fetchAuth(`/agents/${agentId}/directory/custom/humans/${entry.user_id}`, { method: 'DELETE' });
            refreshDirectory();
        } finally {
            setSavingKey(null);
        }
    };

    const removeCustomAgent = async (entry: CustomAgentEntry) => {
        setSavingKey(`remove:${entry.target_agent_id}`);
        try {
            await fetchAuth(`/agents/${agentId}/directory/custom/agents/${entry.target_agent_id}`, { method: 'DELETE' });
            refreshDirectory();
        } finally {
            setSavingKey(null);
        }
    };

    const renderCustomMaintenance = () => {
        if (!showCustomMaintenance) return null;
        const humans = customHumansQuery.data?.members || [];
        const agents = customAgentsQuery.data?.agents || [];
        const entries = customTab === 'human' ? humans : agents;
        const candidatePlaceholder = customTab === 'human'
            ? (isChinese ? '搜索可加入的人类成员' : 'Search people to add')
            : (isChinese ? '搜索可加入的数字员工' : 'Search agents to add');

        return (
            <div style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '12px', marginBottom: '16px', background: 'var(--bg-elevated)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '10px' }}>
                    <div>
                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{isChinese ? 'Custom 通讯录维护' : 'Custom Directory'}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                            {isChinese ? '维护这个 Agent 可见、可联系的指定成员和数字员工。' : 'Maintain the selected people and agents visible to this agent.'}
                        </div>
                    </div>
                    <div style={{ display: 'inline-flex', border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', height: '32px' }}>
                        {[
                            { value: 'human' as const, label: t('agent.directory.people') },
                            { value: 'agent' as const, label: t('agent.directory.agents') },
                        ].map((option) => (
                            <button
                                key={option.value}
                                type="button"
                                onClick={() => setCustomTab(option.value)}
                                style={{
                                    border: 0,
                                    borderRight: option.value === 'human' ? '1px solid var(--border-subtle)' : 0,
                                    background: customTab === option.value ? 'var(--bg-tertiary)' : 'var(--bg-primary)',
                                    color: 'var(--text-primary)',
                                    padding: '0 12px',
                                    fontSize: '12px',
                                    cursor: 'pointer',
                                }}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '12px' }}>
                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px', fontWeight: 600 }}>
                            {isChinese ? '已加入' : 'Added'} ({entries.length})
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            {entries.length === 0 && (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', border: '1px dashed var(--border-subtle)', borderRadius: '8px', padding: '12px' }}>
                                    {isChinese ? '还没有显式加入对象。' : 'No explicit entries yet.'}
                                </div>
                            )}
                            {customTab === 'human' && humans.map((entry) => (
                                <div key={entry.user_id} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '10px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <IconUser size={16} stroke={1.7} style={{ color: 'rgb(16,185,129)', flexShrink: 0 }} />
                                    <div style={{ minWidth: 0, flex: 1 }}>
                                        <div style={{ fontWeight: 600, fontSize: '12px' }}>{entry.display_name || entry.email || entry.user_id}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {[entry.title, entry.department, entry.email].filter(Boolean).join(' · ') || entry.user_id}
                                        </div>
                                    </div>
                                    <span className="badge" style={{ fontSize: '10px' }}>{entry.access_level}</span>
                                    <button
                                        type="button"
                                        className="btn btn-ghost"
                                        disabled={!entry.removable || savingKey === `remove:${entry.user_id}`}
                                        title={!entry.removable ? (isChinese ? '请先在权限设置里降级管理权限' : 'Downgrade manager access in Permissions first') : undefined}
                                        onClick={() => removeCustomHuman(entry)}
                                        style={{ fontSize: '12px', color: entry.removable ? 'var(--error)' : 'var(--text-tertiary)' }}
                                    >
                                        {t('common.remove', 'Remove')}
                                    </button>
                                </div>
                            ))}
                            {customTab === 'agent' && agents.map((entry) => (
                                <div key={entry.target_agent_id} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '10px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                    <IconRobot size={16} stroke={1.7} style={{ color: 'rgb(79,70,229)', flexShrink: 0 }} />
                                    <div style={{ minWidth: 0, flex: 1 }}>
                                        <div style={{ fontWeight: 600, fontSize: '12px' }}>{entry.display_name || entry.target_agent_id}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {entry.role_description || entry.access_mode || entry.target_agent_id}
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        className="btn btn-ghost"
                                        disabled={savingKey === `remove:${entry.target_agent_id}`}
                                        onClick={() => removeCustomAgent(entry)}
                                        style={{ fontSize: '12px', color: 'var(--error)' }}
                                    >
                                        {t('common.remove', 'Remove')}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px', fontWeight: 600 }}>
                            {isChinese ? '添加对象' : 'Add entries'}
                        </div>
                        <label style={{ position: 'relative', display: 'block', marginBottom: '8px' }}>
                            <IconSearch size={15} stroke={1.7} style={{ position: 'absolute', left: '9px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                            <input
                                id={`agent-directory-custom-search-${customTab}`}
                                name="agent_directory_custom_search"
                                className="input"
                                value={customSearch}
                                onChange={(event) => setCustomSearch(event.target.value)}
                                placeholder={candidatePlaceholder}
                                aria-label={candidatePlaceholder}
                                style={{ width: '100%', paddingLeft: '32px', fontSize: '12px' }}
                            />
                        </label>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            {loadedCandidates.length === 0 && !customCandidatesQuery.isLoading && (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', border: '1px dashed var(--border-subtle)', borderRadius: '8px', padding: '12px' }}>
                                    {isChinese ? '没有可加入的候选对象。' : 'No candidates available.'}
                                </div>
                            )}
                            {loadedCandidates.map((candidate) => {
                                const isHuman = customTab === 'human';
                                const key = isHuman ? (candidate as CustomHumanCandidate).user_id : (candidate as CustomAgentCandidate).target_agent_id;
                                const title = isHuman ? (candidate as CustomHumanCandidate).display_name : (candidate as CustomAgentCandidate).display_name;
                                const desc = isHuman
                                    ? [(candidate as CustomHumanCandidate).title, (candidate as CustomHumanCandidate).department, (candidate as CustomHumanCandidate).email].filter(Boolean).join(' · ')
                                    : (candidate as CustomAgentCandidate).role_description || (candidate as CustomAgentCandidate).access_mode || '';
                                return (
                                    <div key={key} style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '10px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                                        {isHuman ? <IconUser size={16} stroke={1.7} style={{ color: 'rgb(16,185,129)', flexShrink: 0 }} /> : <IconRobot size={16} stroke={1.7} style={{ color: 'rgb(79,70,229)', flexShrink: 0 }} />}
                                        <div style={{ minWidth: 0, flex: 1 }}>
                                            <div style={{ fontWeight: 600, fontSize: '12px' }}>{title || key}</div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{desc || key}</div>
                                        </div>
                                        <button
                                            type="button"
                                            className="btn btn-secondary"
                                            disabled={savingKey === `add:${key}`}
                                            onClick={() => addCustomCandidate(candidate)}
                                            style={{ fontSize: '12px' }}
                                        >
                                            {isChinese ? '加入' : 'Add'}
                                        </button>
                                    </div>
                                );
                            })}
                            {customCandidatesQuery.data?.has_more && (
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    disabled={customCandidatesQuery.isFetching}
                                    onClick={() => setCandidateOffset((customCandidatesQuery.data?.offset || 0) + (customCandidatesQuery.data?.limit || PAGE_SIZE))}
                                    style={{ fontSize: '12px', alignSelf: 'center' }}
                                >
                                    {customCandidatesQuery.isFetching
                                        ? t('agent.directory.loadingMore', isChinese ? '加载中...' : 'Loading...')
                                        : t('agent.directory.loadMore', isChinese ? '加载更多' : 'Load more')}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        );
    };

    return (
        <div className="card">
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', marginBottom: '16px' }}>
                <div>
                    <h4 style={{ marginBottom: '4px' }}>{t('agent.directory.title')}</h4>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {t('agent.directory.subtitle')}
                    </div>
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                    {directoryQuery.data ? t('agent.directory.count', { count: directoryQuery.data.returned_count }) : ''}
                </div>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center', marginBottom: '16px' }}>
                <label style={{ position: 'relative', minWidth: '220px', flex: '1 1 260px' }}>
                    <IconSearch size={16} stroke={1.7} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                    <input
                        id="agent-directory-search"
                        name="agent_directory_search"
                        className="input"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder={t('agent.directory.searchPlaceholder')}
                        aria-label={t('agent.directory.searchPlaceholder')}
                        style={{ width: '100%', paddingLeft: '34px' }}
                    />
                </label>
                <div style={{ display: 'inline-flex', border: '1px solid var(--border-subtle)', borderRadius: '8px', overflow: 'hidden', height: '36px' }}>
                    {typeOptions.map((option) => (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => setMemberType(option.value)}
                            style={{
                                border: 0,
                                borderRight: option.value === 'agent' ? 0 : '1px solid var(--border-subtle)',
                                background: memberType === option.value ? 'var(--bg-tertiary)' : 'var(--bg-primary)',
                                color: 'var(--text-primary)',
                                padding: '0 12px',
                                fontSize: '12px',
                                cursor: 'pointer',
                                minWidth: '64px',
                            }}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                    <input
                        id="agent-directory-include-unavailable"
                        name="agent_directory_include_unavailable"
                        type="checkbox"
                        checked={includeUnavailable}
                        onChange={(event) => setIncludeUnavailable(event.target.checked)}
                    />
                    {t('agent.directory.showUnavailable')}
                </label>
            </div>

            {renderCustomMaintenance()}

            {isInitialLoading && (
                <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {t('agent.detail.loading')}
                </div>
            )}

            {directoryQuery.isError && (
                <div style={{ border: '1px solid rgba(239,68,68,0.25)', borderRadius: '8px', padding: '12px', color: 'var(--error)', fontSize: '13px' }}>
                    {String((directoryQuery.error as Error)?.message || t('common.error.loadFailed', 'Load failed'))}
                </div>
            )}

            {!isInitialLoading && !directoryQuery.isError && members.length === 0 && (
                <div style={{ border: '1px dashed var(--border-subtle)', borderRadius: '8px', padding: '24px', textAlign: 'center' }}>
                    <div style={{ fontWeight: 600, marginBottom: '6px' }}>{t('agent.directory.emptyTitle')}</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {t('agent.directory.emptyDesc')}
                    </div>
                </div>
            )}

            {!isInitialLoading && !directoryQuery.isError && members.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {members.map((member) => (
                        <div
                            key={`${member.member_type}:${primaryId(member)}`}
                            style={{
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '8px',
                                padding: '12px',
                                display: 'flex',
                                flexWrap: 'wrap',
                                gap: '12px',
                                alignItems: 'center',
                            }}
                        >
                            <div style={{
                                width: '36px',
                                height: '36px',
                                borderRadius: '50%',
                                background: member.member_type === 'agent' ? 'rgba(99,102,241,0.10)' : 'rgba(16,185,129,0.10)',
                                color: member.member_type === 'agent' ? 'rgb(79,70,229)' : 'rgb(16,185,129)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                flexShrink: 0,
                            }}>
                                {member.member_type === 'agent' ? <IconRobot size={18} stroke={1.7} /> : <IconUser size={18} stroke={1.7} />}
                            </div>
                            <div style={{ minWidth: '220px', flex: '1 1 260px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginBottom: '3px' }}>
                                    <span style={{ fontWeight: 600, fontSize: '13px' }}>{member.display_name || '—'}</span>
                                    <span className="badge" style={{ fontSize: '10px' }}>{providerLabel(member)}</span>
                                    <span
                                        className="badge"
                                        style={{
                                            fontSize: '10px',
                                            color: member.can_contact ? 'rgb(16,185,129)' : 'var(--warning)',
                                            background: member.can_contact ? 'rgba(16,185,129,0.10)' : 'rgba(245,158,11,0.12)',
                                        }}
                                    >
                                        {member.can_contact ? t('agent.directory.contactable') : t('agent.directory.unavailable')}
                                    </span>
                                </div>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {secondaryText(member) || (member.member_type === 'agent' ? member.access_mode : '') || '—'}
                                </div>
                                <div style={{ marginTop: '5px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    {renderContactTools(member)}
                                    {!member.can_contact && member.unavailable_reason && (
                                        <span>{member.unavailable_reason}</span>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                    {directoryQuery.data?.has_more && (
                        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: '4px' }}>
                            <button
                                type="button"
                                className="btn btn-secondary"
                                disabled={isLoadingMore}
                                onClick={() => setOffset((directoryQuery.data?.offset || 0) + (directoryQuery.data?.limit || PAGE_SIZE))}
                            >
                                {isLoadingMore
                                    ? t('agent.directory.loadingMore', isChinese ? '加载中...' : 'Loading...')
                                    : t('agent.directory.loadMore', isChinese ? '加载更多' : 'Load more')}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
