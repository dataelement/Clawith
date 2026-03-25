import React, { useState, useRef, useEffect, useCallback } from 'react';

interface SkillItem {
    key: string;
    name: string;
    emoji?: string;
    description?: string;
}

interface SkillEntry {
    has_sub_items: boolean;
    description?: string;
    items?: SkillItem[];
}

interface SkillAutocompleteProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    skillMap: Record<string, SkillEntry>;
    placeholder?: string;
    className?: string;
    disabled?: boolean;
    inputRef?: React.RefObject<HTMLInputElement>;
}

interface DropdownItem {
    key: string;
    label: string;
    emoji?: string;
    description?: string;
    isSkill: boolean;
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
}: SkillAutocompleteProps) {
    const [showDropdown, setShowDropdown] = useState(false);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [items, setItems] = useState<DropdownItem[]>([]);
    const internalRef = useRef<HTMLInputElement>(null);
    const ref = externalRef || internalRef;
    const dropdownRef = useRef<HTMLDivElement>(null);

    const getAutocompleteState = useCallback((val: string) => {
        if (!val.startsWith('/')) return { mode: 'none' as const, query: '' };

        const withSub = val.match(/^\/([a-z0-9_-]+):(.*)$/);
        if (withSub) {
            return { mode: 'sub' as const, skill: withSub[1], query: withSub[2] };
        }

        const simple = val.match(/^\/([a-z0-9_-]*)$/);
        if (simple) {
            return { mode: 'top' as const, query: simple[1] };
        }

        return { mode: 'none' as const, query: '' };
    }, []);

    useEffect(() => {
        if (!skillMap || Object.keys(skillMap).length === 0) {
            setShowDropdown(false);
            return;
        }

        const state = getAutocompleteState(value);

        if (state.mode === 'top') {
            const query = state.query.toLowerCase();
            const filtered = Object.entries(skillMap)
                .map(([key, entry]) => ({
                    key,
                    label: key,
                    description: entry.description,
                    isSkill: true,
                }))
                .filter(item => item.key.includes(query));
            setItems(filtered);
            setShowDropdown(filtered.length > 0);
            setSelectedIndex(0);
        } else if (state.mode === 'sub') {
            const skill = skillMap[state.skill!];
            if (skill?.has_sub_items && skill.items) {
                const query = state.query.toLowerCase();
                const filtered = skill.items
                    .filter(item => item.key.includes(query) || item.name.toLowerCase().includes(query))
                    .map(item => ({
                        key: item.key,
                        label: item.key,
                        emoji: item.emoji,
                        description: item.description,
                        isSkill: false,
                    }));
                setItems(filtered);
                setShowDropdown(filtered.length > 0);
                setSelectedIndex(0);
            } else {
                setShowDropdown(false);
            }
        } else {
            setShowDropdown(false);
        }
    }, [value, skillMap, getAutocompleteState]);

    const selectItem = useCallback((item: DropdownItem) => {
        const state = getAutocompleteState(value);

        if (state.mode === 'top' && item.isSkill) {
            const skill = skillMap[item.key];
            if (skill?.has_sub_items) {
                onChange(`/${item.key}:`);
            } else {
                onChange(`/${item.key} `);
                setShowDropdown(false);
            }
        } else if (state.mode === 'sub') {
            onChange(`/${state.skill}:${item.key} `);
            setShowDropdown(false);
        }
    }, [value, skillMap, onChange, getAutocompleteState]);

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
                selectItem(items[selectedIndex]);
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
            const selected = dropdownRef.current.children[selectedIndex] as HTMLElement;
            selected?.scrollIntoView({ block: 'nearest' });
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
                            key={item.key}
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
                            <span style={{ fontWeight: 500 }}>{item.label}</span>
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
                onBlur={() => {
                    setTimeout(() => setShowDropdown(false), 200);
                }}
            />
        </div>
    );
}
