import React, { useState, useRef, useEffect, useCallback } from 'react';

interface SkillMapEntry {
    name: string;
    emoji?: string;
    description?: string;
}

interface SkillAutocompleteProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    skillMap: Record<string, SkillMapEntry>;
    placeholder?: string;
    className?: string;
    disabled?: boolean;
    inputRef?: React.RefObject<HTMLInputElement | null>;
    onPaste?: React.ClipboardEventHandler<HTMLInputElement>;
}

interface DropdownItem {
    segment: string;
    fullKey: string;
    isLeaf: boolean;
    name?: string;
    emoji?: string;
    description?: string;
}

export default function SkillAutocomplete({
    value,
    onChange,
    onSubmit,
    skillMap,
    placeholder,
    className,
    disabled,
    inputRef: externalRef,
    onPaste,
}: SkillAutocompleteProps) {
    const [showDropdown, setShowDropdown] = useState(false);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [items, setItems] = useState<DropdownItem[]>([]);
    const internalRef = useRef<HTMLInputElement>(null);
    const ref = externalRef || internalRef;
    const dropdownRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!skillMap || !value.startsWith('/')) {
            setShowDropdown(false);
            return;
        }

        const keys = Object.keys(skillMap);
        if (keys.length === 0) {
            setShowDropdown(false);
            return;
        }

        const raw = value.slice(1);
        const prefix = raw.endsWith(':') ? raw : raw.includes(':') ? raw.slice(0, raw.lastIndexOf(':') + 1) : '';
        const query = raw.endsWith(':') ? '' : (raw.includes(':') ? raw.slice(raw.lastIndexOf(':') + 1) : raw);

        const matching = keys.filter(k => k.startsWith(prefix));

        const segmentMap = new Map<string, DropdownItem>();
        for (const key of matching) {
            const rest = key.slice(prefix.length);
            const nextColon = rest.indexOf(':');
            const segment = nextColon >= 0 ? rest.slice(0, nextColon) : rest;

            if (!segment) continue;
            if (query && !segment.toLowerCase().includes(query.toLowerCase())) continue;

            if (!segmentMap.has(segment)) {
                const fullKey = prefix + segment;
                const isLeaf = fullKey in skillMap;
                const entry = isLeaf ? skillMap[fullKey] : undefined;
                const hasChildren = keys.some(k => k.startsWith(fullKey + ':'));

                segmentMap.set(segment, {
                    segment,
                    fullKey,
                    isLeaf,
                    name: entry?.name || segment,
                    emoji: entry?.emoji,
                    description: entry?.description || (hasChildren && !isLeaf ? 'Package' : undefined),
                });
            }
        }

        const filtered = Array.from(segmentMap.values());
        setItems(filtered);
        setShowDropdown(filtered.length > 0);
        setSelectedIndex(0);
    }, [value, skillMap]);

    const hasChildren = useCallback((item: DropdownItem) => {
        return Object.keys(skillMap).some(k => k.startsWith(item.fullKey + ':'));
    }, [skillMap]);

    const selectItem = useCallback((item: DropdownItem) => {
        if (item.isLeaf && !hasChildren(item)) {
            // Pure leaf — select and close
            onChange(`/${item.fullKey} `);
            setShowDropdown(false);
        } else {
            // Has children (whether or not it's also a leaf) — drill down
            onChange(`/${item.fullKey}:`);
        }
    }, [onChange, hasChildren]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (showDropdown) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelectedIndex(prev => Math.min(prev + 1, items.length - 1));
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelectedIndex(prev => Math.max(prev - 1, 0));
                return;
            }
            if (e.key === 'Enter' && items[selectedIndex]) {
                e.preventDefault();
                const item = items[selectedIndex];
                if (item.isLeaf) {
                    // Enter always selects as leaf (even if it has children)
                    onChange(`/${item.fullKey} `);
                    setShowDropdown(false);
                } else {
                    selectItem(item);
                }
                return;
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                setShowDropdown(false);
                return;
            }
            if (e.key === 'Tab' && items[selectedIndex]) {
                e.preventDefault();
                selectItem(items[selectedIndex]);
                return;
            }
        }

        if (e.key === 'Enter' && !e.shiftKey && !showDropdown) {
            e.preventDefault();
            onSubmit();
        }
    };

    useEffect(() => {
        if (showDropdown && dropdownRef.current) {
            const el = dropdownRef.current.children[selectedIndex] as HTMLElement;
            el?.scrollIntoView({ block: 'nearest' });
        }
    }, [selectedIndex, showDropdown]);

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            {showDropdown && (
                <div
                    ref={dropdownRef}
                    style={{
                        position: 'absolute',
                        bottom: '100%',
                        left: 0,
                        right: 0,
                        maxHeight: '240px',
                        overflowY: 'auto',
                        background: 'var(--bg-secondary, #1e1e1e)',
                        border: '1px solid var(--border-color, #333)',
                        borderRadius: '8px',
                        marginBottom: '4px',
                        zIndex: 100,
                        boxShadow: '0 -4px 12px rgba(0,0,0,0.3)',
                    }}
                >
                    {items.map((item, i) => (
                        <div
                            key={item.fullKey}
                            onClick={() => selectItem(item)}
                            style={{
                                padding: '8px 12px',
                                cursor: 'pointer',
                                background: i === selectedIndex ? 'var(--bg-hover, #2a2a2a)' : 'transparent',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '8px',
                                fontSize: '14px',
                            }}
                        >
                            {item.emoji && <span>{item.emoji}</span>}
                            <span style={{ fontWeight: 500 }}>{item.segment}</span>
                            {!item.isLeaf && <span style={{ color: 'var(--text-secondary, #888)', fontSize: '11px' }}>▸</span>}
                            {item.description && (
                                <span style={{
                                    color: 'var(--text-secondary, #888)',
                                    fontSize: '12px',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                    flex: 1,
                                }}>
                                    {item.description}
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            )}
            <input
                ref={ref}
                className={className}
                value={value}
                onChange={e => onChange(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                disabled={disabled}
                onPaste={onPaste}
                onBlur={() => {
                    setTimeout(() => setShowDropdown(false), 200);
                }}
            />
        </div>
    );
}
