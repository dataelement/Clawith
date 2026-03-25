import { useState, useEffect, useCallback, useRef, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { crmApi } from '../services/api';

const inp: React.CSSProperties = {
    width: '100%', padding: '8px 12px', borderRadius: 6,
    border: '1px solid var(--border)', background: 'var(--bg-secondary)',
    color: 'var(--text-primary)', fontSize: 13,
};
const btn: React.CSSProperties = {
    padding: '6px 14px', borderRadius: 6, border: 'none',
    background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 500,
};
const btnSec: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)' };

export default function CRMDashboard() {
    const { t } = useTranslation();

    const STAGES = [
        { key: 'lead', label: t('crm.stages.lead', 'Lead'), color: '#94a3b8' },
        { key: 'contacted', label: t('crm.stages.contacted', 'Contacted'), color: '#60a5fa' },
        { key: 'qualified', label: t('crm.stages.qualified', 'Qualified'), color: '#a78bfa' },
        { key: 'proposal', label: t('crm.stages.proposal', 'Proposal'), color: '#f59e0b' },
        { key: 'negotiation', label: t('crm.stages.negotiation', 'Negotiation'), color: '#fb923c' },
        { key: 'won', label: t('crm.stages.won', 'Won'), color: '#10b981' },
        { key: 'lost', label: t('crm.stages.lost', 'Lost'), color: '#ef4444' },
    ];

    const [view, setView] = useState<'pipeline' | 'contacts'>('pipeline');
    const [deals, setDeals] = useState<any[]>([]);
    const [contacts, setContacts] = useState<any[]>([]);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [loading, setLoading] = useState(false);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [detail, setDetail] = useState<any>(null);
    const [showAdd, setShowAdd] = useState(false);
    const [showDeal, setShowDeal] = useState<string | null>(null);
    const [editContact, setEditContact] = useState<any>(null);
    const [stats, setStats] = useState<any>(null);
    const [dragId, setDragId] = useState<string | null>(null);
    const [dragOver, setDragOver] = useState<string | null>(null);
    const [form, setForm] = useState({ name: '', company: '', email: '', phone: '', country: '', industry: '', source: '', notes: '' });
    const [dealForm, setDealForm] = useState({ title: '', value: '', currency: 'USD', stage: 'lead', notes: '' });

    const fetchDeals = useCallback(async () => { try { setDeals(await crmApi.listDeals()); } catch {} }, []);
    const fetchContacts = useCallback(async () => {
        setLoading(true);
        try {
            const res = await crmApi.listContacts({ search: search || undefined });
            setContacts(res.items || []); setTotal(res.total || 0);
        } catch {}
        setLoading(false);
    }, [search]);
    const fetchStats = useCallback(async () => { try { setStats(await crmApi.stats()); } catch {} }, []);

    useEffect(() => { fetchDeals(); fetchContacts(); fetchStats(); }, []);
    useEffect(() => { const tm = setTimeout(fetchContacts, 300); return () => clearTimeout(tm); }, [search]);

    // -- Drag & Drop --
    const onDragStart = (e: DragEvent, dealId: string) => { setDragId(dealId); e.dataTransfer.effectAllowed = 'move'; };
    const onDragOver = (e: DragEvent, stageKey: string) => { e.preventDefault(); setDragOver(stageKey); };
    const onDragLeave = () => setDragOver(null);
    const onDrop = async (e: DragEvent, stageKey: string) => {
        e.preventDefault(); setDragOver(null);
        if (!dragId) return;
        const deal = deals.find(d => d.id === dragId);
        if (deal && deal.stage !== stageKey) {
            setDeals(prev => prev.map(d => d.id === dragId ? { ...d, stage: stageKey } : d));
            try { await crmApi.updateDeal(dragId, { stage: stageKey }); fetchStats(); } catch { fetchDeals(); }
        }
        setDragId(null);
    };

    // -- Batch --
    const toggleSelect = (id: string) => setSelected(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
    const selectAll = () => { if (selected.size === contacts.length) setSelected(new Set()); else setSelected(new Set(contacts.map(c => c.id))); };
    const batchDelete = async () => {
        if (!selected.size || !confirm(t('crm.deleteConfirm', 'Delete {{count}} contacts?', { count: selected.size }))) return;
        await crmApi.batchDeleteContacts([...selected]); setSelected(new Set()); fetchContacts(); fetchDeals(); fetchStats();
    };

    // -- CRUD --
    const addContact = async () => {
        if (!form.name.trim()) return;
        await crmApi.createContact(form);
        setForm({ name: '', company: '', email: '', phone: '', country: '', industry: '', source: '', notes: '' });
        setShowAdd(false); fetchContacts(); fetchStats();
    };
    const saveEdit = async () => {
        if (!editContact) return;
        await crmApi.updateContact(editContact.id, editContact);
        setEditContact(null); fetchContacts(); if (detail?.id === editContact.id) openDetail(editContact.id);
    };
    const deleteContact = async (id: string) => {
        if (!confirm(t('crm.deleteContactConfirm', 'Delete this contact and all deals?'))) return;
        await crmApi.deleteContact(id); setDetail(null); fetchContacts(); fetchDeals(); fetchStats();
    };
    const openDetail = async (id: string) => { try { setDetail(await crmApi.getContact(id)); } catch {} };
    const addDeal = async () => {
        if (!showDeal || !dealForm.title) return;
        await crmApi.createDeal({ contact_id: showDeal, title: dealForm.title, value: dealForm.value ? +dealForm.value : null, currency: dealForm.currency, stage: dealForm.stage, notes: dealForm.notes });
        setDealForm({ title: '', value: '', currency: 'USD', stage: 'lead', notes: '' });
        setShowDeal(null); fetchDeals(); fetchStats(); if (detail) openDetail(detail.id);
    };
    const deleteDeal = async (id: string) => { if (confirm(t('crm.deleteDealConfirm', 'Delete deal?'))) { await crmApi.deleteDeal(id); fetchDeals(); fetchStats(); if (detail) openDetail(detail.id); } };

    const stageColor = (s: string) => STAGES.find(st => st.key === s)?.color || '#888';

    const fieldLabels: Record<string, string> = {
        name: t('crm.fields.name', 'Name'),
        company: t('crm.fields.company', 'Company'),
        email: t('crm.fields.email', 'Email'),
        phone: t('crm.fields.phone', 'Phone'),
        country: t('crm.fields.country', 'Country'),
        industry: t('crm.fields.industry', 'Industry'),
        source: t('crm.fields.source', 'Source'),
        notes: t('crm.fields.notes', 'Notes'),
    };

    const tableHeaders = [
        t('crm.fields.name', 'Name'),
        t('crm.fields.company', 'Company'),
        t('crm.fields.email', 'Email'),
        t('crm.fields.phone', 'Phone'),
        t('crm.fields.country', 'Country'),
        t('crm.fields.source', 'Source'),
        t('crm.fields.deals', 'Deals'),
        '',
    ];

    return (
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* Header */}
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
                <h3 style={{ margin: 0, fontSize: 16 }}>{t('crm.title', 'CRM')}</h3>
                {stats && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('crm.stats', '{{contacts}} contacts / {{deals}} deals', { contacts: stats.contacts, deals: stats.deals })}</span>}
                <div style={{ flex: 1 }} />
                <div style={{ display: 'flex', gap: 4, background: 'var(--bg-secondary)', borderRadius: 8, padding: 2 }}>
                    {(['pipeline', 'contacts'] as const).map(v => (
                        <button key={v} onClick={() => setView(v)} style={{
                            padding: '5px 14px', borderRadius: 6, border: 'none', fontSize: 13,
                            background: view === v ? 'var(--accent)' : 'transparent',
                            color: view === v ? '#fff' : 'var(--text-secondary)', cursor: 'pointer',
                        }}>{v === 'pipeline' ? t('crm.pipeline', 'Pipeline') : t('crm.contacts', 'Contacts')}</button>
                    ))}
                </div>
                <button onClick={() => setShowAdd(true)} style={btn}>{t('crm.addContact', '+ Contact')}</button>
            </div>

            <div style={{ flex: 1, overflow: 'auto' }}>
                {/* -- Pipeline View -- */}
                {view === 'pipeline' && (
                    <div style={{ display: 'flex', gap: 8, padding: 12, height: '100%', overflowX: 'auto' }}>
                        {STAGES.map(stage => {
                            const sd = deals.filter(d => d.stage === stage.key);
                            const tv = sd.reduce((s, d) => s + (d.value || 0), 0);
                            const isOver = dragOver === stage.key;
                            return (
                                <div key={stage.key}
                                    onDragOver={e => onDragOver(e, stage.key)}
                                    onDragLeave={onDragLeave}
                                    onDrop={e => onDrop(e, stage.key)}
                                    style={{
                                        minWidth: 210, flex: 1, display: 'flex', flexDirection: 'column',
                                        background: isOver ? `${stage.color}10` : 'var(--bg-secondary)',
                                        borderRadius: 8, border: isOver ? `2px dashed ${stage.color}` : '2px solid transparent',
                                        transition: 'all 0.15s',
                                    }}>
                                    <div style={{ padding: '10px 12px', borderBottom: `2px solid ${stage.color}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                        <span style={{ fontSize: 13, fontWeight: 600, color: stage.color }}>{stage.label}</span>
                                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{sd.length}{tv > 0 ? ` \u00b7 $${(tv/1000).toFixed(0)}k` : ''}</span>
                                    </div>
                                    <div style={{ flex: 1, padding: 6, display: 'flex', flexDirection: 'column', gap: 5, overflowY: 'auto' }}>
                                        {sd.map(deal => (
                                            <div key={deal.id} draggable
                                                onDragStart={e => onDragStart(e, deal.id)}
                                                style={{
                                                    padding: '10px 12px', borderRadius: 6, cursor: 'grab',
                                                    background: 'var(--bg-primary)', border: '1px solid var(--border)',
                                                    opacity: dragId === deal.id ? 0.4 : 1, transition: 'opacity 0.15s',
                                                }}>
                                                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 3 }}>{deal.title}</div>
                                                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                                                    {deal.contact_name}{deal.contact_company ? ` \u00b7 ${deal.contact_company}` : ''}
                                                </div>
                                                {deal.value != null && deal.value > 0 && (
                                                    <div style={{ fontSize: 12, fontWeight: 600, marginTop: 4 }}>{deal.currency} {deal.value.toLocaleString()}</div>
                                                )}
                                                <div style={{ marginTop: 6, display: 'flex', gap: 4 }}>
                                                    <button onClick={() => deleteDeal(deal.id)} style={{ padding: '2px 6px', borderRadius: 4, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-tertiary)', fontSize: 10, cursor: 'pointer' }}>{t('crm.del', 'Del')}</button>
                                                </div>
                                            </div>
                                        ))}
                                        {sd.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>{t('crm.dropHere', 'Drop here')}</div>}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}

                {/* -- Contacts View -- */}
                {view === 'contacts' && (
                    <div style={{ padding: '12px 20px' }}>
                        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                            <input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('crm.searchPlaceholder', 'Search name, company, email...')}
                                style={{ ...inp, maxWidth: 350 }} />
                            {selected.size > 0 && (
                                <button onClick={batchDelete} style={{ ...btn, background: '#ef4444' }}>
                                    {t('crm.deleteSelected', 'Delete {{count}}', { count: selected.size })}
                                </button>
                            )}
                        </div>
                        {loading ? <p style={{ color: 'var(--text-tertiary)' }}>{t('crm.loading', 'Loading...')}</p> : contacts.length === 0 ? (
                            <p style={{ color: 'var(--text-tertiary)', textAlign: 'center', padding: 40 }}>{t('crm.noContacts', 'No contacts yet')}</p>
                        ) : (
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                <thead>
                                    <tr style={{ borderBottom: '2px solid var(--border)' }}>
                                        <th style={{ width: 32, padding: '8px 4px' }}>
                                            <input type="checkbox" checked={selected.size === contacts.length && contacts.length > 0} onChange={selectAll} />
                                        </th>
                                        {tableHeaders.map(h => (
                                            <th key={h} style={{ textAlign: 'left', padding: '8px 8px', color: 'var(--text-secondary)', fontWeight: 500, fontSize: 12 }}>{h}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {contacts.map(c => (
                                        <tr key={c.id} style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                                            onClick={() => openDetail(c.id)}>
                                            <td style={{ padding: '8px 4px' }} onClick={e => e.stopPropagation()}>
                                                <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggleSelect(c.id)} />
                                            </td>
                                            <td style={{ padding: 8 }}><strong>{c.name}</strong></td>
                                            <td style={{ padding: 8, color: 'var(--text-secondary)' }}>{c.company || '-'}</td>
                                            <td style={{ padding: 8, color: 'var(--text-secondary)' }}>{c.email || '-'}</td>
                                            <td style={{ padding: 8, color: 'var(--text-secondary)' }}>{c.phone || '-'}</td>
                                            <td style={{ padding: 8, color: 'var(--text-secondary)' }}>{c.country || '-'}</td>
                                            <td style={{ padding: 8, color: 'var(--text-secondary)' }}>{c.source || '-'}</td>
                                            <td style={{ padding: 8 }}>
                                                {(c.deals?.length || 0) > 0 ? <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: '#10b98120', color: '#10b981' }}>{c.deals.length}</span> : '-'}
                                            </td>
                                            <td style={{ padding: 8 }} onClick={e => e.stopPropagation()}>
                                                <button onClick={() => { setEditContact({ ...c }); }} style={{ ...btnSec, padding: '3px 8px', fontSize: 11 }}>{t('crm.edit', 'Edit')}</button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                        {total > contacts.length && <p style={{ textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12, marginTop: 12 }}>{t('crm.showing', 'Showing {{shown}} of {{total}}', { shown: contacts.length, total })}</p>}
                    </div>
                )}
            </div>

            {/* -- Contact Detail Panel -- */}
            {detail && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', justifyContent: 'flex-end' }}
                    onClick={() => setDetail(null)}>
                    <div style={{ width: 480, height: '100%', background: 'var(--bg-primary)', overflow: 'auto', padding: 24, boxShadow: '-4px 0 20px rgba(0,0,0,0.2)' }}
                        onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                            <h3 style={{ margin: 0 }}>{detail.name}</h3>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button onClick={() => { setEditContact({ ...detail }); setDetail(null); }} style={{ ...btnSec, padding: '4px 10px', fontSize: 12 }}>{t('crm.edit', 'Edit')}</button>
                                <button onClick={() => deleteContact(detail.id)} style={{ ...btnSec, padding: '4px 10px', fontSize: 12, color: '#ef4444', borderColor: '#ef4444' }}>{t('crm.delete', 'Delete')}</button>
                                <button onClick={() => setDetail(null)} style={{ ...btnSec, padding: '4px 10px', fontSize: 14 }}>x</button>
                            </div>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 13, marginBottom: 20 }}>
                            {[
                                [t('crm.fields.company', 'Company'), detail.company],
                                [t('crm.fields.email', 'Email'), detail.email],
                                [t('crm.fields.phone', 'Phone'), detail.phone],
                                [t('crm.fields.country', 'Country'), detail.country],
                                [t('crm.fields.industry', 'Industry'), detail.industry],
                                [t('crm.fields.source', 'Source'), detail.source],
                            ].map(([l, v]) => (
                                <div key={l as string}><span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>{l as string}</span><div>{(v as string) || '-'}</div></div>
                            ))}
                        </div>
                        {detail.tags?.length > 0 && <div style={{ marginBottom: 16, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                            {detail.tags.map((tg: string) => <span key={tg} style={{ padding: '2px 8px', borderRadius: 4, background: 'var(--bg-secondary)', fontSize: 11 }}>{tg}</span>)}
                        </div>}
                        {detail.notes && <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-secondary)', borderRadius: 6, fontSize: 13 }}>{detail.notes}</div>}

                        {/* Deals */}
                        <div style={{ marginBottom: 20 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                <h4 style={{ margin: 0, fontSize: 14 }}>{t('crm.deals', 'Deals')}</h4>
                                <button onClick={() => setShowDeal(detail.id)} style={{ ...btn, padding: '3px 10px', fontSize: 11 }}>{t('crm.addDeal', '+ Deal')}</button>
                            </div>
                            {(detail.deals || []).map((d: any) => (
                                <div key={d.id} style={{ padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <div>
                                        <div style={{ fontSize: 13, fontWeight: 500 }}>{d.title}</div>
                                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{d.currency} {d.value?.toLocaleString() || 0}</div>
                                    </div>
                                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: stageColor(d.stage) + '20', color: stageColor(d.stage) }}>{d.stage}</span>
                                        <button onClick={() => deleteDeal(d.id)} style={{ ...btnSec, padding: '2px 6px', fontSize: 10 }}>x</button>
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Activities */}
                        <h4 style={{ margin: '0 0 8px', fontSize: 14 }}>{t('crm.activity', 'Activity')}</h4>
                        {(detail.activities || []).map((a: any) => (
                            <div key={a.id} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                                <span style={{ color: 'var(--text-tertiary)', marginRight: 8 }}>{a.created_at?.slice(0, 10)}</span>
                                <span style={{ padding: '1px 6px', borderRadius: 3, background: 'var(--bg-secondary)', fontSize: 10, marginRight: 6 }}>{a.type}</span>
                                {a.summary}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* -- Add Contact Modal -- */}
            {showAdd && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    onClick={() => setShowAdd(false)}>
                    <div style={{ background: 'var(--bg-primary)', borderRadius: 12, padding: 24, width: 440 }} onClick={e => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 16px' }}>{t('crm.newContact', 'New Contact')}</h3>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                            {[{ k: 'name', l: t('crm.fields.nameRequired', 'Name *'), p: t('crm.fields.contactName', 'Contact name') },
                              { k: 'company', l: t('crm.fields.company', 'Company'), p: t('crm.fields.company', 'Company') },
                              { k: 'email', l: t('crm.fields.email', 'Email'), p: 'email@co.com' },
                              { k: 'phone', l: t('crm.fields.phone', 'Phone'), p: '+1...' },
                              { k: 'country', l: t('crm.fields.country', 'Country'), p: 'Germany' },
                              { k: 'industry', l: t('crm.fields.industry', 'Industry'), p: 'LED Lighting' },
                              { k: 'source', l: t('crm.fields.source', 'Source'), p: 'manual' }].map(f => (
                                <div key={f.k} style={f.k === 'notes' ? { gridColumn: '1/3' } : {}}>
                                    <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 3 }}>{f.l}</label>
                                    <input value={(form as any)[f.k]} onChange={e => setForm(p => ({ ...p, [f.k]: e.target.value }))} placeholder={f.p} style={inp} />
                                </div>
                            ))}
                            <div style={{ gridColumn: '1/3' }}>
                                <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 3 }}>{t('crm.fields.notes', 'Notes')}</label>
                                <textarea value={form.notes} onChange={e => setForm(p => ({ ...p, notes: e.target.value }))} rows={2} style={{ ...inp, resize: 'vertical' }} />
                            </div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button onClick={() => setShowAdd(false)} style={btnSec}>{t('crm.cancel', 'Cancel')}</button>
                            <button onClick={addContact} style={btn}>{t('crm.create', 'Create')}</button>
                        </div>
                    </div>
                </div>
            )}

            {/* -- Edit Contact Modal -- */}
            {editContact && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    onClick={() => setEditContact(null)}>
                    <div style={{ background: 'var(--bg-primary)', borderRadius: 12, padding: 24, width: 440 }} onClick={e => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 16px' }}>{t('crm.editContact', 'Edit Contact')}</h3>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                            {[{ k: 'name', l: t('crm.fields.name', 'Name') }, { k: 'company', l: t('crm.fields.company', 'Company') }, { k: 'email', l: t('crm.fields.email', 'Email') },
                              { k: 'phone', l: t('crm.fields.phone', 'Phone') }, { k: 'country', l: t('crm.fields.country', 'Country') }, { k: 'industry', l: t('crm.fields.industry', 'Industry') }, { k: 'source', l: t('crm.fields.source', 'Source') }].map(f => (
                                <div key={f.k}>
                                    <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 3 }}>{f.l}</label>
                                    <input value={editContact[f.k] || ''} onChange={e => setEditContact((p: any) => ({ ...p, [f.k]: e.target.value }))} style={inp} />
                                </div>
                            ))}
                            <div style={{ gridColumn: '1/3' }}>
                                <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginBottom: 3 }}>{t('crm.fields.notes', 'Notes')}</label>
                                <textarea value={editContact.notes || ''} onChange={e => setEditContact((p: any) => ({ ...p, notes: e.target.value }))} rows={2} style={{ ...inp, resize: 'vertical' }} />
                            </div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button onClick={() => setEditContact(null)} style={btnSec}>{t('crm.cancel', 'Cancel')}</button>
                            <button onClick={saveEdit} style={btn}>{t('crm.save', 'Save')}</button>
                        </div>
                    </div>
                </div>
            )}

            {/* -- Add Deal Modal -- */}
            {showDeal && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 10001, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    onClick={() => setShowDeal(null)}>
                    <div style={{ background: 'var(--bg-primary)', borderRadius: 12, padding: 24, width: 400 }} onClick={e => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 16px' }}>{t('crm.newDeal', 'New Deal')}</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            <div><label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('crm.dealFields.titleRequired', 'Title *')}</label><input value={dealForm.title} onChange={e => setDealForm(p => ({ ...p, title: e.target.value }))} style={inp} /></div>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <div style={{ flex: 1 }}><label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('crm.dealFields.value', 'Value')}</label><input type="number" value={dealForm.value} onChange={e => setDealForm(p => ({ ...p, value: e.target.value }))} style={inp} /></div>
                                <div style={{ width: 80 }}><label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('crm.dealFields.currency', 'Currency')}</label><input value={dealForm.currency} onChange={e => setDealForm(p => ({ ...p, currency: e.target.value }))} style={inp} /></div>
                            </div>
                            <div><label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('crm.dealFields.stage', 'Stage')}</label>
                                <select value={dealForm.stage} onChange={e => setDealForm(p => ({ ...p, stage: e.target.value }))} style={inp}>
                                    {STAGES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
                                </select>
                            </div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button onClick={() => setShowDeal(null)} style={btnSec}>{t('crm.cancel', 'Cancel')}</button>
                            <button onClick={addDeal} style={btn}>{t('crm.createDeal', 'Create Deal')}</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
