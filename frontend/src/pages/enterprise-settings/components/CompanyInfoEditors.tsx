import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconCheck } from '@tabler/icons-react';
import { buildCompanyRegions, type CompanyRegion } from '../../../utils/companyRegions';
import { useAuthStore } from '../../../stores';
import { fetchJson } from '../utils/fetchJson';

function CompanyLogoCropModal({ imageUrl, imageName, onCancel, onSave }: {
    imageUrl: string;
    imageName: string;
    onCancel: () => void;
    onSave: (blob: Blob) => void;
}) {
    const { t } = useTranslation();
    const imgRef = useRef<HTMLImageElement>(null);
    const [naturalSize, setNaturalSize] = useState({ width: 1, height: 1 });
    const [zoom, setZoom] = useState(1);
    const [offset, setOffset] = useState({ x: 0, y: 0 });
    const [dragStart, setDragStart] = useState<{ x: number; y: number; ox: number; oy: number } | null>(null);
    const cropSize = 240;

    const clampOffset = (next: { x: number; y: number }, nextZoom = zoom) => {
        const baseScale = Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height);
        const displayW = naturalSize.width * baseScale * nextZoom;
        const displayH = naturalSize.height * baseScale * nextZoom;
        const maxX = Math.max(0, (displayW - cropSize) / 2);
        const maxY = Math.max(0, (displayH - cropSize) / 2);
        return {
            x: Math.min(maxX, Math.max(-maxX, next.x)),
            y: Math.min(maxY, Math.max(-maxY, next.y)),
        };
    };

    const handleSave = () => {
        const img = imgRef.current;
        if (!img) return;
        const outputSize = 512;
        const ratio = outputSize / cropSize;
        const baseScale = Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height);
        const displayW = naturalSize.width * baseScale * zoom;
        const displayH = naturalSize.height * baseScale * zoom;
        const dx = ((cropSize - displayW) / 2 + offset.x) * ratio;
        const dy = ((cropSize - displayH) / 2 + offset.y) * ratio;
        const canvas = document.createElement('canvas');
        canvas.width = outputSize;
        canvas.height = outputSize;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, outputSize, outputSize);
        ctx.drawImage(img, dx, dy, displayW * ratio, displayH * ratio);
        canvas.toBlob((blob) => {
            if (blob) onSave(blob);
        }, 'image/png');
    };

    return (
        <div className="tenant-logo-crop-backdrop" onClick={onCancel}>
            <div className="tenant-logo-crop-modal" onClick={e => e.stopPropagation()}>
                <div className="tenant-logo-crop-header">
                    <div>
                        <h3>{t('enterprise.logo.cropTitle', 'Crop company logo')}</h3>
                        <p>{imageName}</p>
                    </div>
                    <button type="button" onClick={onCancel}>×</button>
                </div>
                <div
                    className="tenant-logo-crop-stage"
                    onPointerDown={e => {
                        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                        setDragStart({ x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y });
                    }}
                    onPointerMove={e => {
                        if (!dragStart) return;
                        setOffset(clampOffset({
                            x: dragStart.ox + e.clientX - dragStart.x,
                            y: dragStart.oy + e.clientY - dragStart.y,
                        }));
                    }}
                    onPointerUp={() => setDragStart(null)}
                    onPointerCancel={() => setDragStart(null)}
                >
                    <img
                        ref={imgRef}
                        src={imageUrl}
                        alt=""
                        draggable={false}
                        onLoad={e => {
                            const img = e.currentTarget;
                            setNaturalSize({ width: img.naturalWidth || 1, height: img.naturalHeight || 1 });
                            setOffset({ x: 0, y: 0 });
                            setZoom(1);
                        }}
                        style={{
                            width: `${naturalSize.width * Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height)}px`,
                            height: `${naturalSize.height * Math.max(cropSize / naturalSize.width, cropSize / naturalSize.height)}px`,
                            transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
                        }}
                    />
                </div>
                <div className="tenant-logo-crop-controls">
                    <span>{t('enterprise.logo.zoom', 'Zoom')}</span>
                    <input
                        type="range"
                        min="1"
                        max="3"
                        step="0.01"
                        value={zoom}
                        onChange={e => {
                            const nextZoom = Number(e.target.value);
                            setZoom(nextZoom);
                            setOffset(prev => clampOffset(prev, nextZoom));
                        }}
                    />
                </div>
                <div className="tenant-logo-crop-actions">
                    <button className="btn btn-secondary" type="button" onClick={onCancel}>{t('common.cancel', 'Cancel')}</button>
                    <button className="btn btn-primary" type="button" onClick={handleSave}>{t('common.save', 'Save')}</button>
                </div>
            </div>
        </div>
    );
}

export function CompanyLogoEditor() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [name, setName] = useState('');
    const [logoUrl, setLogoUrl] = useState('');
    const [logoError, setLogoError] = useState('');
    const [logoSaving, setLogoSaving] = useState(false);
    const [cropSource, setCropSource] = useState<{ url: string; name: string } | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => {
                if (d?.name) setName(d.name);
                setLogoUrl(d?.logo_url || '');
            })
            .catch(() => { });
    }, [tenantId]);

    const handleLogoFile = (file: File | undefined) => {
        setLogoError('');
        if (!file) return;
        if (file.size > 1024 * 1024) {
            setLogoError(t('enterprise.logo.tooLarge', 'Logo image must be 1 MB or smaller.'));
            return;
        }
        if (!file.type.startsWith('image/')) {
            setLogoError(t('enterprise.logo.invalidType', 'Please choose an image file.'));
            return;
        }
        setCropSource({ url: URL.createObjectURL(file), name: file.name });
    };

    const uploadCroppedLogo = async (blob: Blob) => {
        if (!tenantId) return;
        setLogoError('');
        if (blob.size > 1024 * 1024) {
            setLogoError(t('enterprise.logo.croppedTooLarge', 'Cropped logo is still larger than 1 MB.'));
            return;
        }
        setLogoSaving(true);
        try {
            const form = new FormData();
            form.append('file', blob, 'company-logo.png');
            const res = await fetch(`/api/tenants/${tenantId}/logo`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
                body: form,
            });
            if (!res.ok) {
                throw new Error(t('enterprise.logo.uploadFailed', 'Failed to upload logo.'));
            }
            const tenant = await res.json();
            setLogoUrl(tenant.logo_url || '');
            setCropSource(null);
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
        } catch (e: any) {
            setLogoError(e.message || t('enterprise.logo.uploadFailed', 'Failed to upload logo.'));
        } finally {
            setLogoSaving(false);
        }
    };

    const resetLogo = async () => {
        if (!tenantId || !logoUrl) return;
        setLogoError('');
        setLogoSaving(true);
        try {
            const res = await fetch(`/api/tenants/${tenantId}/logo`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
            });
            if (!res.ok) {
                throw new Error(t('enterprise.logo.resetFailed', 'Failed to reset logo.'));
            }
            setLogoUrl('');
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
        } catch (e: any) {
            setLogoError(e.message || t('enterprise.logo.resetFailed', 'Failed to reset logo.'));
        } finally {
            setLogoSaving(false);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {t('enterprise.logo.title', 'Company Logo')}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '14px' }}>
                {t('enterprise.logo.description', 'Used in the sidebar workspace switcher and company selection menus.')}
            </div>
            <div className="company-identity-logo-row">
                <div className="company-identity-logo-preview">
                    {logoUrl ? <img src={logoUrl} alt="" /> : <span>{(Array.from(name.trim())[0] as string | undefined)?.toUpperCase() || 'C'}</span>}
                </div>
                <div>
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        style={{ display: 'none' }}
                        onChange={e => {
                            handleLogoFile(e.target.files?.[0]);
                            e.currentTarget.value = '';
                        }}
                    />
                    <button className="btn btn-secondary" type="button" onClick={() => fileInputRef.current?.click()} disabled={logoSaving}>
                        {logoSaving ? t('common.loading') : t('enterprise.logo.upload', 'Upload logo')}
                    </button>
                    {logoUrl && (
                        <button
                            className="btn btn-ghost"
                            type="button"
                            onClick={resetLogo}
                            disabled={logoSaving}
                            style={{ marginLeft: '8px' }}
                        >
                            {t('enterprise.logo.reset', 'Reset to default')}
                        </button>
                    )}
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                        {t('enterprise.logo.hint', 'PNG, JPG, or WebP. Max 1 MB. You will crop it to a square before saving.')}
                    </div>
                    {logoError && <div style={{ fontSize: '12px', color: 'var(--error)', marginTop: '6px' }}>{logoError}</div>}
                </div>
            </div>
            {cropSource && (
                <CompanyLogoCropModal
                    imageUrl={cropSource.url}
                    imageName={cropSource.name}
                    onCancel={() => setCropSource(null)}
                    onSave={uploadCroppedLogo}
                />
            )}
        </div>
    );
}

export function CompanyNameEditor() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const tenantId = localStorage.getItem('current_tenant_id') || '';
    const [name, setName] = useState('');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => { if (d?.name) setName(d.name); })
            .catch(() => { });
    }, [tenantId]);

    const handleSave = async () => {
        if (!tenantId || !name.trim()) return;
        setSaving(true);
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT', body: JSON.stringify({ name: name.trim() }),
            });
            qc.invalidateQueries({ queryKey: ['tenants'] });
            qc.invalidateQueries({ queryKey: ['tenant', tenantId] });
            qc.invalidateQueries({ queryKey: ['my-tenants'] });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) { }
        setSaving(false);
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '10px' }}>
                {t('enterprise.companyName.title', 'Company Name')}
            </div>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                <input
                    className="form-input"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    placeholder={t('enterprise.companyName.placeholder', 'Enter company name')}
                    style={{ flex: 1, fontSize: '14px' }}
                    onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button className="btn btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
                    {saving ? t('common.loading') : t('common.save', 'Save')}
                </button>
                {saved && <IconCheck size={15} stroke={2} style={{ color: 'var(--success)' }} />}
            </div>
        </div>
    );
}

export function CompanyTimezoneEditor() {
    const { t, i18n } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const tenantId = user?.tenant_id || localStorage.getItem('current_tenant_id') || '';
    const regionPickerRef = useRef<HTMLDivElement>(null);
    const [timezone, setTimezone] = useState('UTC');
    const [countryRegion, setCountryRegion] = useState('001');
    const [regionInput, setRegionInput] = useState('');
    const [regionOpen, setRegionOpen] = useState(false);
    const [highlightedRegion, setHighlightedRegion] = useState(0);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState('');
    const zh = i18n.language?.startsWith('zh');
    const companyRegions = useMemo(() => buildCompanyRegions(zh ? 'zh-Hans' : 'en'), [zh]);
    const regionLabel = (r: CompanyRegion) => zh ? r.zh : r.en;
    const selectedRegion = companyRegions.find(r => r.code === countryRegion) || companyRegions[0];
    const filteredRegions = useMemo(() => {
        const query = regionInput.trim().toLowerCase();
        if (!query || (!regionOpen && regionInput === regionLabel(selectedRegion))) return companyRegions;
        return companyRegions.filter(r => {
            const localName = regionLabel(r).toLowerCase();
            const altName = (zh ? r.en : r.zh).toLowerCase();
            return localName.includes(query)
                || altName.includes(query)
                || r.code.toLowerCase().includes(query)
                || r.timezone.toLowerCase().includes(query);
        });
    }, [companyRegions, regionInput, regionOpen, selectedRegion, zh]);

    useEffect(() => {
        setRegionInput(regionLabel(selectedRegion));
    }, [countryRegion, zh]);

    useEffect(() => {
        if (!regionOpen) return;
        const handlePointerDown = (e: MouseEvent) => {
            if (!regionPickerRef.current?.contains(e.target as Node)) {
                setRegionOpen(false);
                setRegionInput(regionLabel(selectedRegion));
                setHighlightedRegion(0);
            }
        };
        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, [regionOpen, selectedRegion, zh]);

    useEffect(() => {
        setHighlightedRegion(0);
    }, [regionInput]);

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => {
                if (d?.timezone) setTimezone(d.timezone);
                if (d?.country_region) setCountryRegion(d.country_region);
            })
            .catch((e: any) => setError(e.message || 'Failed to load timezone'));
    }, [tenantId]);

    const handleSave = async (regionCode: string) => {
        if (!tenantId) return;
        const region = companyRegions.find(r => r.code === regionCode) || companyRegions[0];
        setCountryRegion(region.code);
        setTimezone(region.timezone);
        setSaving(true);
        setError('');
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT',
                body: JSON.stringify({
                    country_region: region.code,
                    timezone: region.timezone,
                }),
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e: any) {
            setError(e.message || 'Failed to save timezone');
        }
        setSaving(false);
    };

    const selectRegion = (region: CompanyRegion) => {
        setRegionInput(regionLabel(region));
        setRegionOpen(false);
        setHighlightedRegion(0);
        if (region.code !== countryRegion) {
            handleSave(region.code);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {zh ? '公司所在国家或地区' : 'Company Country or Region'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {zh
                    ? `用于自动设置公司默认时区和 OKR 休息日规则。当前时区：${timezone}`
                    : `Used to set the company timezone and OKR non-workday rules. Current timezone: ${timezone}`}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '10px', width: '100%' }}>
                <div ref={regionPickerRef} style={{ position: 'relative', width: 'min(420px, 100%)' }}>
                    <input
                        className="form-input"
                        value={regionInput}
                        onChange={e => {
                            setRegionInput(e.target.value);
                            setRegionOpen(true);
                        }}
                        onFocus={() => {
                            setRegionOpen(true);
                            setRegionInput('');
                        }}
                        onKeyDown={e => {
                            if (e.key === 'ArrowDown') {
                                e.preventDefault();
                                setRegionOpen(true);
                                setHighlightedRegion(i => Math.min(i + 1, Math.max(filteredRegions.length - 1, 0)));
                            } else if (e.key === 'ArrowUp') {
                                e.preventDefault();
                                setHighlightedRegion(i => Math.max(i - 1, 0));
                            } else if (e.key === 'Enter') {
                                e.preventDefault();
                                const region = filteredRegions[highlightedRegion];
                                if (region) selectRegion(region);
                            } else if (e.key === 'Escape') {
                                setRegionOpen(false);
                                setRegionInput(regionLabel(selectedRegion));
                            }
                        }}
                        placeholder={zh ? '搜索国家或地区、代码或时区' : 'Search country, code, or timezone'}
                        style={{
                            width: '100%',
                            fontSize: '13px',
                            paddingRight: '42px',
                            cursor: saving || !tenantId ? 'not-allowed' : 'text',
                        }}
                        disabled={saving || !tenantId}
                        role="combobox"
                        aria-expanded={regionOpen}
                        aria-controls="company-region-listbox"
                        aria-autocomplete="list"
                    />
                    <button
                        type="button"
                        onClick={() => {
                            if (saving || !tenantId) return;
                            setRegionOpen(v => !v);
                            if (!regionOpen) setRegionInput('');
                        }}
                        disabled={saving || !tenantId}
                        aria-label={regionOpen ? (zh ? '收起地区列表' : 'Collapse region list') : (zh ? '展开地区列表' : 'Expand region list')}
                        style={{
                            position: 'absolute',
                            right: '7px',
                            top: '50%',
                            transform: `translateY(-50%) rotate(${regionOpen ? 180 : 0}deg)`,
                            border: 'none',
                            background: 'transparent',
                            color: 'var(--text-secondary)',
                            cursor: saving || !tenantId ? 'not-allowed' : 'pointer',
                            width: '30px',
                            height: '30px',
                            padding: 0,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            transition: 'transform 120ms ease',
                        }}
                    >
                        <span
                            aria-hidden="true"
                            style={{
                                width: '8px',
                                height: '8px',
                                borderRight: '1.6px solid currentColor',
                                borderBottom: '1.6px solid currentColor',
                                transform: 'rotate(45deg) translateY(-2px)',
                                borderRadius: '1px',
                            }}
                        />
                    </button>
                    {regionOpen && (
                        <div
                            id="company-region-listbox"
                            role="listbox"
                            style={{
                                position: 'absolute',
                                top: 'calc(100% + 6px)',
                                left: 0,
                                right: 0,
                                zIndex: 30,
                                background: 'var(--bg-primary)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '8px',
                                boxShadow: '0 12px 28px rgba(15, 23, 42, 0.14)',
                                maxHeight: '260px',
                                overflowY: 'auto',
                                padding: '6px',
                            }}
                        >
                            {filteredRegions.length > 0 ? filteredRegions.map((region, index) => {
                                const active = region.code === countryRegion;
                                const highlighted = index === highlightedRegion;
                                return (
                                    <button
                                        key={region.code}
                                        type="button"
                                        role="option"
                                        aria-selected={active}
                                        onMouseEnter={() => setHighlightedRegion(index)}
                                        onMouseDown={e => e.preventDefault()}
                                        onClick={() => selectRegion(region)}
                                        style={{
                                            width: '100%',
                                            border: 'none',
                                            background: highlighted ? 'var(--bg-elevated)' : 'transparent',
                                            color: 'var(--text-primary)',
                                            borderRadius: '6px',
                                            padding: '9px 10px',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                            gap: '12px',
                                            textAlign: 'left',
                                            cursor: 'pointer',
                                        }}
                                    >
                                        <span style={{ minWidth: 0 }}>
                                            <span style={{ display: 'block', fontSize: '13px', fontWeight: active ? 600 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {regionLabel(region)}
                                            </span>
                                            <span style={{ display: 'block', marginTop: '2px', color: 'var(--text-tertiary)', fontSize: '11px' }}>
                                                {region.code} · {region.timezone}
                                            </span>
                                        </span>
                                        {active && <span style={{ color: 'var(--text-primary)', fontSize: '14px', flexShrink: 0 }}>✓</span>}
                                    </button>
                                );
                            }) : (
                                <div style={{ padding: '12px 10px', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                                    {zh ? '没有匹配的国家或地区' : 'No matching country or region'}
                                </div>
                            )}
                        </div>
                    )}
                </div>
                {(saved || error || !tenantId) && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minHeight: '16px', flexWrap: 'wrap' }}>
                        {saved && <span style={{ color: 'var(--success)', fontSize: '12px' }}>已保存</span>}
                        {error && (
                            <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                                {error}
                            </div>
                        )}
                        {!tenantId && (
                            <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                                {t('enterprise.timezone.noTenant', 'No company selected. Please refresh the page or contact support.')}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

export function A2AAsyncToggle() {
    const { t, i18n } = useTranslation();
    const user = useAuthStore((s) => s.user);
    const tenantId = user?.tenant_id || localStorage.getItem('current_tenant_id') || '';
    const [enabled, setEnabled] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const zh = i18n.language?.startsWith('zh');

    useEffect(() => {
        if (!tenantId) return;
        fetchJson<any>(`/tenants/${tenantId}`)
            .then(d => setEnabled(!!d?.a2a_async_enabled))
            .catch((e: any) => setError(e.message || 'Failed to load A2A setting'));
    }, [tenantId]);

    const handleToggle = async () => {
        if (!tenantId || saving) return;
        const next = !enabled;
        setEnabled(next);
        setSaving(true);
        setError('');
        try {
            await fetchJson(`/tenants/${tenantId}`, {
                method: 'PUT',
                body: JSON.stringify({ a2a_async_enabled: next }),
            });
        } catch (e: any) {
            setEnabled(!next);
            setError(e.message || 'Failed to save A2A setting');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
            <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
                {zh ? 'Agent 异步协作（Beta）' : 'Agent Async Collaboration (Beta)'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                {zh
                    ? '开启后，数字员工之间可使用 notify / task_delegate 等异步协作模式。关闭后，Agent 间消息统一走同步 consult。'
                    : 'When enabled, agents can use async notify and task_delegate modes. When disabled, agent-to-agent messaging falls back to synchronous consult.'}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '12px' }}>
                <div style={{ width: '100%' }}>
                    {error && (
                        <div style={{ fontSize: '11px', color: 'var(--error)' }}>
                            {error}
                        </div>
                    )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', flexShrink: 0, opacity: saving ? 0.6 : 1 }}>
                        <input
                            type="checkbox"
                            checked={enabled}
                            onChange={handleToggle}
                            disabled={saving || !tenantId}
                            style={{ opacity: 0, width: 0, height: 0 }}
                        />
                        <span style={{
                            position: 'absolute', inset: 0,
                            borderRadius: '999px',
                            cursor: saving ? 'not-allowed' : 'pointer',
                            background: enabled ? 'var(--accent-primary)' : 'var(--border-subtle)',
                            transition: '0.2s',
                        }}>
                            <span style={{
                                position: 'absolute',
                                top: '2px',
                                left: enabled ? '20px' : '2px',
                                width: '18px',
                                height: '18px',
                                borderRadius: '50%',
                                background: '#fff',
                                transition: '0.2s',
                                boxShadow: '0 1px 2px rgba(0,0,0,0.12)',
                            }} />
                        </span>
                    </label>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {enabled ? (zh ? '已开启' : 'Enabled') : (zh ? '已关闭' : 'Disabled')}
                    </span>
                </div>
            </div>
            <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)', maxWidth: '640px' }}>
                {zh
                    ? '说明：OKR 日报收集本身会优先使用更稳的同步方式，不依赖这里的异步开关。'
                    : 'Note: OKR daily collection itself uses the more reliable synchronous path and does not depend on this toggle.'}
            </div>
        </div>
    );
}
