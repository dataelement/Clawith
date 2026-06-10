import type React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconMessageCircle } from '@tabler/icons-react';
import { messageApi } from '../services/api';

const ACTION_ICONS: Record<string, React.ReactNode> = {
    text: <IconMessageCircle size={16} stroke={1.8} />,
    notify: '·',
    consult: '?',
    task_delegate: '+',
};

export default function Messages() {
    const { t, i18n } = useTranslation();
    const queryClient = useQueryClient();
    const { data: messages = [], isLoading } = useQuery({
        queryKey: ['messages-inbox'],
        queryFn: () => messageApi.inbox(100),
        refetchInterval: 15000,
    });

    const markReadMutation = useMutation({
        mutationFn: (id: string) => messageApi.markRead(id),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['messages-inbox'] });
            queryClient.invalidateQueries({ queryKey: ['unread-count'] });
        },
    });

    const markAllReadMutation = useMutation({
        mutationFn: () => messageApi.markAllRead(),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['messages-inbox'] });
            queryClient.invalidateQueries({ queryKey: ['unread-count'] });
        },
    });

    const unreadCount = messages.filter((m: any) => !m.read_at).length;

    const formatTime = (iso: string) => {
        if (!iso) return '';
        const d = new Date(iso);
        const now = new Date();
        const diffMs = now.getTime() - d.getTime();
        if (diffMs < 60000) return t('messages.justNow');
        if (diffMs < 3600000) return t('messages.minutesAgo', { count: Math.floor(diffMs / 60000) });
        if (diffMs < 86400000) return t('messages.hoursAgo', { count: Math.floor(diffMs / 3600000) });
        return d.toLocaleDateString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    };

    return (
        <div style={{ maxWidth: '800px', margin: '0 auto', padding: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
                <h1 style={{ fontSize: '20px', fontWeight: 600, margin: 0 }}>{t('messages.title')}</h1>
                {unreadCount > 0 && (
                    <button
                        className="btn btn-ghost"
                        onClick={() => markAllReadMutation.mutate()}
                        style={{ fontSize: '13px', color: 'var(--accent)' }}
                    >
                        {t('messages.markAllRead', { count: unreadCount })}
                    </button>
                )}
            </div>

            {isLoading && (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.loading')}</div>
            )}

            {!isLoading && messages.length === 0 && (
                <div style={{
                    textAlign: 'center', padding: '60px 20px', color: 'var(--text-tertiary)',
                    background: 'var(--bg-secondary)', borderRadius: '12px',
                }}>
                    <div style={{ fontSize: '13px', marginBottom: '12px', color: 'var(--text-tertiary)' }}>{t('messages.empty')}</div>
                    <div>{t('messages.empty')}</div>
                </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {messages.map((msg: any) => (
                    <div
                        key={msg.id}
                        onClick={() => !msg.read_at && markReadMutation.mutate(msg.id)}
                        style={{
                            padding: '14px 16px',
                            borderRadius: '8px',
                            background: msg.read_at ? 'transparent' : 'rgba(224,238,238,0.06)',
                            cursor: msg.read_at ? 'default' : 'pointer',
                            borderLeft: msg.read_at ? '3px solid transparent' : '3px solid var(--accent)',
                            transition: 'background 0.15s',
                        }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <span style={{ fontSize: '14px' }}>{ACTION_ICONS[msg.msg_type] || '·'}</span>
                            <span style={{ fontWeight: 600, fontSize: '14px' }}>
                                {msg.sender_name}
                            </span>
                            <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                                → {msg.receiver_name}
                            </span>
                            <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                {formatTime(msg.created_at)}
                            </span>
                            {!msg.read_at && (
                                <span style={{
                                    width: '8px', height: '8px', borderRadius: '50%',
                                    background: 'var(--accent)', flexShrink: 0,
                                }} />
                            )}
                        </div>
                        <div style={{
                            fontSize: '13px', color: 'var(--text-secondary)',
                            lineHeight: '1.5', whiteSpace: 'pre-wrap',
                            maxHeight: '60px', overflow: 'hidden',
                        }}>
                            {msg.content}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
