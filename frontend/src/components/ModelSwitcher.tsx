import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconChevronDown, IconCheck } from '@tabler/icons-react';
import { enterpriseApi } from '../services/api';

interface Model {
    id: string;
    provider: string;
    model: string;
    label?: string;
    enabled?: boolean;
}

interface Props {
    // Current selection — parent-controlled so the override persists across re-renders
    // within the same session, but resets when the parent remounts.
    value: string | null;
    onChange: (modelId: string | null) => void;
    // Optional: the tenant's default model id, used to render a "默认" tag.
    tenantDefaultId?: string | null;
    disabled?: boolean;
}

export default function ModelSwitcher({ value, onChange, tenantDefaultId, disabled }: Props) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const [hovered, setHovered] = useState(false);
    const ref = useRef<HTMLDivElement>(null);
    const buttonRef = useRef<HTMLButtonElement>(null);
    const popoverRef = useRef<HTMLDivElement>(null);
    // Anchor coords for the portal-rendered popover. Recomputed when opening
    // and on scroll/resize so the dropdown follows the button. `placement`
    // says whether the popover sits above or below the button — chosen
    // based on which side has more room so the menu is never cut by the
    // viewport edge.
    const [coords, setCoords] = useState<
        { top: number; bottom: number; left: number; width: number; placement: 'above' | 'below'; maxHeight: number } | null
    >(null);

    const { data: models = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: enterpriseApi.llmModels,
    });

    const enabled = (models as Model[]).filter(m => m.enabled !== false);
    const selected = enabled.find(m => m.id === value)
        || enabled.find(m => tenantDefaultId && m.id === tenantDefaultId)
        || enabled[0]
        || null;

    // Click-outside to close. Includes the popover so clicking inside doesn't
    // close (which is important since the popover is portaled — `ref` doesn't
    // contain it via DOM).
    useEffect(() => {
        if (!open) return;
        const handler = (e: MouseEvent) => {
            const inTrigger = ref.current?.contains(e.target as Node);
            const inPopover = popoverRef.current?.contains(e.target as Node);
            if (!inTrigger && !inPopover) setOpen(false);
        };
        window.addEventListener('mousedown', handler);
        return () => window.removeEventListener('mousedown', handler);
    }, [open]);

    // Recompute popover position whenever it opens, and again if the page
    // scrolls or the window resizes while it's open. Picks the side
    // (above / below the button) with more vertical room so the popover
    // never spills off the viewport edge.
    useLayoutEffect(() => {
        if (!open) return;
        const PREFERRED_HEIGHT = 280;
        const GAP = 4;
        const VIEWPORT_PADDING = 8;
        const recompute = () => {
            const btn = buttonRef.current;
            if (!btn) return;
            const r = btn.getBoundingClientRect();
            const vh = window.innerHeight;
            const spaceAbove = r.top - VIEWPORT_PADDING - GAP;
            const spaceBelow = vh - r.bottom - VIEWPORT_PADDING - GAP;
            // Prefer above (matches the original up-pointing chevron flow)
            // unless below clearly has more room.
            const placeAbove = spaceAbove >= PREFERRED_HEIGHT
                || spaceAbove >= spaceBelow;
            const maxHeight = Math.min(
                PREFERRED_HEIGHT,
                Math.max(120, placeAbove ? spaceAbove : spaceBelow),
            );
            setCoords({
                top: r.top,
                bottom: r.bottom,
                left: r.left,
                width: r.width,
                placement: placeAbove ? 'above' : 'below',
                maxHeight,
            });
        };
        recompute();
        window.addEventListener('scroll', recompute, true);
        window.addEventListener('resize', recompute);
        return () => {
            window.removeEventListener('scroll', recompute, true);
            window.removeEventListener('resize', recompute);
        };
    }, [open]);

    if (enabled.length === 0) return null;

    const labelFor = (m: Model) => m.label || `${m.provider} · ${m.model}`;

    return (
        <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
            <button
                ref={buttonRef}
                type="button"
                onClick={() => !disabled && setOpen(o => !o)}
                disabled={disabled}
                onMouseEnter={() => setHovered(true)}
                onMouseLeave={() => setHovered(false)}
                style={{
                    display: 'inline-flex', alignItems: 'center', gap: '6px',
                    height: '28px',
                    padding: '0 10px 0 12px', fontSize: '12px',
                    border: `1px solid ${open || hovered ? 'var(--border-default)' : 'var(--border-subtle)'}`,
                    borderRadius: '999px',
                    background: open || hovered ? 'var(--bg-elevated)' : 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    opacity: disabled ? 0.6 : 1,
                    boxShadow: open ? '0 0 0 2px color-mix(in srgb, var(--accent-primary) 12%, transparent)' : 'none',
                    transition: 'background 120ms, border-color 120ms, box-shadow 120ms, color 120ms',
                }}
                title={t('chat.modelSwitcher.title', 'Switch model for this session')}
            >
                <span style={{
                    display: 'inline-block', maxWidth: '200px',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                    {selected ? labelFor(selected) : t('chat.modelSwitcher.none', 'No model')}
                </span>
                <IconChevronDown
                    size={13}
                    stroke={2}
                    style={{
                        color: 'var(--text-secondary)',
                        transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
                        transition: 'transform 120ms',
                    }}
                />
            </button>
            {open && coords && createPortal(
                <div
                    ref={popoverRef}
                    style={{
                        // `fixed` so the popover escapes any ancestor's
                        // overflow:hidden. Anchor + side picked at open time.
                        position: 'fixed',
                        ...(coords.placement === 'above'
                            ? { bottom: `calc(100vh - ${coords.top}px + 4px)` }
                            : { top: `${coords.bottom + 4}px` }),
                        left: coords.left,
                        minWidth: Math.max(220, coords.width),
                        maxHeight: `${coords.maxHeight}px`, overflowY: 'auto',
                        background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
                        borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
                        zIndex: 10001, padding: '4px',
                    }}
                >
                    {enabled.map(m => {
                        const isSelected = selected?.id === m.id;
                        const isDefault = tenantDefaultId && m.id === tenantDefaultId;
                        return (
                            <button
                                key={m.id}
                                onClick={() => { onChange(m.id); setOpen(false); }}
                                style={{
                                    display: 'flex', alignItems: 'center', width: '100%',
                                    padding: '6px 10px', gap: '8px',
                                    border: 'none', borderRadius: '6px',
                                    background: isSelected ? 'var(--bg-secondary)' : 'transparent',
                                    color: 'var(--text-primary)',
                                    cursor: 'pointer', fontSize: '12.5px', textAlign: 'left',
                                }}
                                onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-secondary)'; }}
                                onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                            >
                                <span style={{ width: '14px', display: 'inline-flex' }}>
                                    {isSelected && <IconCheck size={14} stroke={2} />}
                                </span>
                                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {labelFor(m)}
                                </span>
                                {isDefault && (
                                    <span style={{
                                        fontSize: '10px', padding: '2px 6px',
                                        background: 'var(--bg-secondary)', color: 'var(--text-tertiary)',
                                        borderRadius: '4px', letterSpacing: '0.02em',
                                    }}>
                                        {t('chat.modelSwitcher.defaultTag', '默认')}
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>,
                document.body,
            )}
        </div>
    );
}
