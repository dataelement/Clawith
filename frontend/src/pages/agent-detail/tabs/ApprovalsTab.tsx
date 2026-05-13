import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { fetchAuth } from '../utils/fetchAuth';

export default function ApprovalsTab({ agentId }: { agentId: string }) {
    const { i18n } = useTranslation();
    const queryClient = useQueryClient();
    const isChinese = i18n.language?.startsWith('zh');
    const { data: approvals = [], refetch: refetchApprovals } = useQuery({
        queryKey: ['agent-approvals', agentId],
        queryFn: () => fetchAuth<any[]>(`/agents/${agentId}/approvals`),
        enabled: !!agentId,
        refetchInterval: 15000,
    });

    const resolveMut = useMutation({
        mutationFn: async ({ approvalId, action }: { approvalId: string; action: string }) => {
            const token = localStorage.getItem('token');
            return fetch(`/api/agents/${agentId}/approvals/${approvalId}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                body: JSON.stringify({ action }),
            });
        },
        onSuccess: () => {
            refetchApprovals();
            queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
        },
    });

    const pending = approvals.filter((approval: any) => approval.status === 'pending');
    const resolved = approvals.filter((approval: any) => approval.status !== 'pending');
    const statusStyle = (status: string) => ({
        padding: '2px 8px',
        borderRadius: '4px',
        fontSize: '11px',
        fontWeight: 600,
        background: status === 'approved'
            ? 'rgba(0,180,120,0.12)'
            : status === 'rejected'
                ? 'rgba(255,80,80,0.12)'
                : 'rgba(255,180,0,0.12)',
        color: status === 'approved'
            ? 'var(--success)'
            : status === 'rejected'
                ? 'var(--error)'
                : 'var(--warning)',
    });

    return (
        <div style={{ padding: '20px 24px' }}>
            {pending.length > 0 && (
                <>
                    <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--warning)' }}>
                        {isChinese ? `${pending.length} 个待审批` : `${pending.length} Pending`}
                    </h4>
                    {pending.map((approval: any) => (
                        <div
                            key={approval.id}
                            style={{
                                padding: '14px 16px',
                                marginBottom: '8px',
                                borderRadius: '8px',
                                background: 'var(--bg-secondary)',
                                border: '1px solid var(--border-subtle)',
                            }}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                <span style={statusStyle(approval.status)}>{approval.status}</span>
                                <span style={{ fontSize: '13px', fontWeight: 500 }}>{approval.action_type}</span>
                                <span style={{ flex: 1 }} />
                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    {approval.created_at ? new Date(approval.created_at).toLocaleString() : ''}
                                </span>
                            </div>
                            {approval.details && (
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: '1.5', maxHeight: '80px', overflow: 'hidden' }}>
                                    {typeof approval.details === 'string' ? approval.details : JSON.stringify(approval.details, null, 2)}
                                </div>
                            )}
                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                <button
                                    className="btn btn-primary"
                                    style={{ padding: '6px 16px', fontSize: '12px' }}
                                    onClick={() => resolveMut.mutate({ approvalId: approval.id, action: 'approve' })}
                                    disabled={resolveMut.isPending}
                                >
                                    {isChinese ? '批准' : 'Approve'}
                                </button>
                                <button
                                    className="btn btn-danger"
                                    style={{ padding: '6px 16px', fontSize: '12px' }}
                                    onClick={() => resolveMut.mutate({ approvalId: approval.id, action: 'reject' })}
                                    disabled={resolveMut.isPending}
                                >
                                    {isChinese ? '拒绝' : 'Reject'}
                                </button>
                            </div>
                        </div>
                    ))}
                    <div style={{ borderTop: '1px solid var(--border-subtle)', margin: '16px 0' }} />
                </>
            )}

            <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                {isChinese ? '审批历史' : 'History'}
            </h4>
            {resolved.length === 0 && pending.length === 0 && (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                    {isChinese ? '暂无审批记录' : 'No approval records'}
                </div>
            )}
            {resolved.map((approval: any) => (
                <div
                    key={approval.id}
                    style={{
                        padding: '12px 16px',
                        marginBottom: '6px',
                        borderRadius: '8px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-subtle)',
                        opacity: 0.7,
                    }}
                >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={statusStyle(approval.status)}>{approval.status}</span>
                        <span style={{ fontSize: '12px' }}>{approval.action_type}</span>
                        <span style={{ flex: 1 }} />
                        <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                            {approval.resolved_at ? new Date(approval.resolved_at).toLocaleString() : ''}
                        </span>
                    </div>
                </div>
            ))}
        </div>
    );
}
