import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

export default function InvitationCodes() {
    const { t } = useTranslation();
    const [codes, setCodes] = useState<any[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [search, setSearch] = useState('');
    const pageSize = 20;
    const [batchCount, setBatchCount] = useState(5);
    const [maxUses, setMaxUses] = useState(1);
    const [creating, setCreating] = useState(false);
    const [toast, setToast] = useState('');

    const token = localStorage.getItem('token');
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const loadCodes = useCallback(async (p?: number, q?: string) => {
        const currentPage = p ?? page;
        const currentSearch = q ?? search;
        const params = new URLSearchParams({
            page: String(currentPage),
            page_size: String(pageSize),
        });
        if (currentSearch) params.set('search', currentSearch);
        const res = await fetch(`/api/enterprise/invitation-codes?${params}`, { headers });
        const data = await res.json();
        setCodes(data.items || []);
        setTotal(data.total || 0);
    }, [page, search]);

    useEffect(() => { loadCodes(page, search); }, [page, search]);

    const totalPages = Math.max(1, Math.ceil(total / pageSize));

    const handleSearch = (value: string) => {
        setSearch(value);
        setPage(1);
    };

    const createBatch = async () => {
        setCreating(true);
        await fetch('/api/enterprise/invitation-codes', {
            method: 'POST', headers, body: JSON.stringify({ count: batchCount, max_uses: maxUses }),
        });
        setPage(1);
        setSearch('');
        await loadCodes(1, '');
        setCreating(false);
        setToast(t('enterprise.invites.createBtn', 'Created!'));
        setTimeout(() => setToast(''), 2000);
    };

    const deactivate = async (id: string) => {
        await fetch(`/api/enterprise/invitation-codes/${id}`, { method: 'DELETE', headers });
        await loadCodes();
    };

    const exportCsv = () => {
        const token = localStorage.getItem('token');
        const a = document.createElement('a');
        fetch('/api/enterprise/invitation-codes/export', {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
            .then(r => r.blob())
            .then(blob => {
                a.href = URL.createObjectURL(blob);
                a.download = 'invitation_codes.csv';
                a.click();
                URL.revokeObjectURL(a.href);
            });
    };

    return (
        <div className="content-area" style={{ maxWidth: '900px', margin: '0 auto', padding: '32px 24px' }}>
            {toast && (
                <div style={{
                    position: 'fixed', top: '20px', right: '20px', padding: '10px 20px',
                    borderRadius: '8px', background: 'var(--success)', color: '#fff',
                    fontSize: '13px', zIndex: 9999,
                }}>{toast}</div>
            )}

            <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '4px' }}>
                {t('enterprise.invites.pageTitle', 'Invitation Codes')}
            </h2>
            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '24px' }}>
                {t('enterprise.invites.pageDesc', 'Manage invitation codes for platform registration.')}
            </p>

            {/* Batch Create */}
            <div className="card" style={{ padding: '16px', marginBottom: '16px' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    {t('enterprise.invites.createTitle', 'Create Invitation Codes')}
                </div>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-end' }}>
                    <div style={{ flex: 1 }}>
                        <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                            {t('enterprise.invites.count', 'Number of Codes')}
                        </label>
                        <input className="form-input" type="number" min={1} max={100}
                            value={batchCount} onChange={e => setBatchCount(Number(e.target.value))} />
                    </div>
                    <div style={{ flex: 1 }}>
                        <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                            {t('enterprise.invites.maxUses', 'Max Uses per Code')}
                        </label>
                        <input className="form-input" type="number" min={1}
                            value={maxUses} onChange={e => setMaxUses(Number(e.target.value))} />
                    </div>
                    <button className="btn btn-primary" onClick={createBatch} disabled={creating}
                        style={{ height: '34px', whiteSpace: 'nowrap', flexShrink: 0 }}>
                        {creating ? t('common.loading') : t('enterprise.invites.createBtn', 'Generate')}
                    </button>
                </div>
            </div>

            {/* Search + Codes Table */}
            <div className="card" style={{ padding: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('enterprise.invites.listTitle', 'All Invitation Codes')} ({total})
                    </div>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <input
                            className="form-input"
                            placeholder={t('common.search', 'Search') + '...'}
                            value={search}
                            onChange={e => handleSearch(e.target.value)}
                            style={{ width: '200px', height: '30px', fontSize: '12px' }}
                        />
                        <button className="btn btn-secondary" onClick={exportCsv}
                            style={{ height: '30px', padding: '0 12px', fontSize: '11px', whiteSpace: 'nowrap' }}>
                            {t('enterprise.invites.exportCsv')}
                        </button>
                    </div>
                </div>

                {/* Table header */}
                <div style={{
                    display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 100px',
                    gap: '12px', padding: '8px 12px', fontSize: '11px', fontWeight: 600,
                    color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em',
                    borderBottom: '1px solid var(--border-subtle)',
                }}>
                    <div>{t('enterprise.invites.code', 'Code')}</div>
                    <div>{t('enterprise.invites.usage', 'Usage')}</div>
                    <div>{t('enterprise.invites.status', 'Status')}</div>
                    <div>{t('enterprise.invites.created', 'Created')}</div>
                    <div></div>
                </div>

                {codes.length === 0 && (
                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                        {t('common.noData')}
                    </div>
                )}

                {codes.map((c: any) => (
                    <div key={c.id} style={{
                        display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 100px',
                        gap: '12px', padding: '10px 12px', alignItems: 'center',
                        borderBottom: '1px solid var(--border-subtle)', fontSize: '13px',
                    }}>
                        <div style={{ fontFamily: 'monospace', fontWeight: 500, letterSpacing: '0.1em' }}>{c.code}</div>
                        <div>
                            <span style={{ fontWeight: 500 }}>{c.used_count}</span>
                            <span style={{ color: 'var(--text-tertiary)' }}> / {c.max_uses}</span>
                        </div>
                        <div>
                            {!c.is_active ? (
                                <span className="badge" style={{ background: 'var(--text-tertiary)', color: '#fff', fontSize: '10px' }}>
                                    {t('enterprise.invites.deactivated', 'Disabled')}
                                </span>
                            ) : c.used_count >= c.max_uses ? (
                                <span className="badge" style={{ background: 'var(--warning)', color: '#fff', fontSize: '10px' }}>
                                    {t('enterprise.invites.exhausted', 'Exhausted')}
                                </span>
                            ) : (
                                <span className="badge badge-success" style={{ fontSize: '10px' }}>
                                    {t('enterprise.invites.active', 'Active')}
                                </span>
                            )}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                            {c.created_at ? new Date(c.created_at).toLocaleDateString() : '-'}
                        </div>
                        <div>
                            {c.is_active && c.used_count < c.max_uses && (
                                <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: '10px' }}
                                    onClick={() => deactivate(c.id)}>
                                    {t('enterprise.invites.disable', 'Disable')}
                                </button>
                            )}
                        </div>
                    </div>
                ))}

                {/* Pagination */}
                {totalPages > 1 && (
                    <div style={{
                        display: 'flex', justifyContent: 'center', alignItems: 'center',
                        gap: '8px', padding: '16px 0 4px', fontSize: '13px',
                    }}>
                        <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '12px' }}
                            disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                            ←
                        </button>
                        <span style={{ color: 'var(--text-secondary)' }}>
                            {page} / {totalPages}
                        </span>
                        <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '12px' }}
                            disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>
                            →
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}

