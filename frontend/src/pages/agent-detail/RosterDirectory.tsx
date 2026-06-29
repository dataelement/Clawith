import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconRobot, IconSearch, IconUser } from '@tabler/icons-react';

import { fetchAuth } from './utils/fetchAuth';

type RosterMemberType = 'all' | 'human' | 'agent';

type RosterMember = {
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

type RosterResponse = {
    ok: boolean;
    source_agent_id: string;
    query: string;
    member_type: RosterMemberType;
    include_uncontactable: boolean;
    returned_count: number;
    limit: number;
    offset: number;
    has_more: boolean;
    members: RosterMember[];
};

export default function RosterDirectory({ agentId }: { agentId: string }) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [memberType, setMemberType] = useState<RosterMemberType>('all');
    const [includeUnavailable, setIncludeUnavailable] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search.trim()), 300);
        return () => clearTimeout(timer);
    }, [search]);

    const rosterQuery = useQuery({
        queryKey: ['agent-roster', agentId, debouncedSearch, memberType, includeUnavailable],
        queryFn: () => {
            const params = new URLSearchParams({
                member_type: memberType,
                limit: '50',
                include_uncontactable: includeUnavailable ? 'true' : 'false',
            });
            if (debouncedSearch) params.set('query', debouncedSearch);
            return fetchAuth<RosterResponse>(`/agents/${agentId}/roster?${params.toString()}`);
        },
        enabled: Boolean(agentId),
    });

    const members = rosterQuery.data?.members || [];
    const typeOptions: Array<{ value: RosterMemberType; label: string }> = [
        { value: 'all', label: t('agent.directory.all') },
        { value: 'human', label: t('agent.directory.people') },
        { value: 'agent', label: t('agent.directory.agents') },
    ];

    const providerLabel = (member: RosterMember) => {
        if (member.member_type === 'agent') return t('agent.directory.digitalEmployee');
        const providerType = (member.provider?.provider_type || '').trim();
        if (!providerType || providerType === 'web' || providerType === 'platform') {
            return t('agent.directory.platformUser');
        }
        return providerType;
    };

    const primaryId = (member: RosterMember) => (
        member.member_type === 'agent'
            ? member.target_agent_id
            : member.target_member_id
    ) || '';

    const secondaryText = (member: RosterMember) => {
        if (member.member_type === 'agent') {
            return member.role_description || member.access_mode || '';
        }
        const parts = [member.title, member.department?.name].filter(Boolean);
        return parts.join(' · ');
    };

    const renderContactTools = (member: RosterMember) => {
        const tools = member.contact_tools || [];
        if (!tools.length) return <span style={{ color: 'var(--text-tertiary)' }}>—</span>;
        return tools.map((tool) => (
            <span key={tool} className="badge" style={{ fontSize: '10px' }}>
                {tool}
            </span>
        ));
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
                    {rosterQuery.data ? t('agent.directory.count', { count: rosterQuery.data.returned_count }) : ''}
                </div>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center', marginBottom: '16px' }}>
                <label style={{ position: 'relative', minWidth: '220px', flex: '1 1 260px' }}>
                    <IconSearch size={16} stroke={1.7} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                    <input
                        className="input"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder={t('agent.directory.searchPlaceholder')}
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
                        type="checkbox"
                        checked={includeUnavailable}
                        onChange={(event) => setIncludeUnavailable(event.target.checked)}
                    />
                    {t('agent.directory.showUnavailable')}
                </label>
            </div>

            {rosterQuery.isLoading && (
                <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {t('agent.detail.loading')}
                </div>
            )}

            {rosterQuery.isError && (
                <div style={{ border: '1px solid rgba(239,68,68,0.25)', borderRadius: '8px', padding: '12px', color: 'var(--error)', fontSize: '13px' }}>
                    {String((rosterQuery.error as Error)?.message || t('common.error.loadFailed', 'Load failed'))}
                </div>
            )}

            {!rosterQuery.isLoading && !rosterQuery.isError && members.length === 0 && (
                <div style={{ border: '1px dashed var(--border-subtle)', borderRadius: '8px', padding: '24px', textAlign: 'center' }}>
                    <div style={{ fontWeight: 600, marginBottom: '6px' }}>{t('agent.directory.emptyTitle')}</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                        {t('agent.directory.emptyDesc')}
                    </div>
                </div>
            )}

            {!rosterQuery.isLoading && !rosterQuery.isError && members.length > 0 && (
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
                                    {secondaryText(member) || (member.member_type === 'agent' ? member.access_mode : member.platform_user_id) || '—'}
                                </div>
                                <div style={{ marginTop: '5px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    {renderContactTools(member)}
                                    {!member.can_contact && member.unavailable_reason && (
                                        <span>{member.unavailable_reason}</span>
                                    )}
                                </div>
                            </div>
                            <div style={{ flex: '0 1 260px', minWidth: '180px', marginLeft: 'auto', textAlign: 'right', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                <div style={{ fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={primaryId(member)}>
                                    {member.member_type === 'agent' ? t('agent.directory.targetAgentId') : t('agent.directory.targetMemberId')}: {primaryId(member)}
                                </div>
                                {member.member_type === 'human' && member.platform_user_id && (
                                    <div style={{ fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={member.platform_user_id}>
                                        {t('agent.directory.platformUserId')}: {member.platform_user_id}
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                    {rosterQuery.data?.has_more && (
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center', paddingTop: '4px' }}>
                            {isChinese ? '还有更多结果，请搜索缩小范围。' : 'More results are available. Refine the search to narrow the list.'}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
