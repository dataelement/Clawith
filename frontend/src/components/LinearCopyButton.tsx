import React, { useState } from 'react';
import { copyToClipboard } from '../utils/clipboard';

interface LinearCopyButtonProps {
    textToCopy: string;
    label?: string;
    copiedLabel?: string;
    className?: string;
    style?: React.CSSProperties;
    disabled?: boolean;
    iconOnly?: boolean;
}

export default function LinearCopyButton({
    textToCopy,
    label = 'Copy',
    copiedLabel = 'Copied',
    className = 'btn btn-secondary',
    style,
    disabled = false,
    iconOnly = false
}: LinearCopyButtonProps) {
    const [copied, setCopied] = useState(false);

    const handleCopy = async (e: React.MouseEvent) => {
        e.preventDefault();
        if (disabled || !textToCopy) return;
        
        await copyToClipboard(textToCopy);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <button
            className={className}
            style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: iconOnly ? '0' : '6px',
                transition: 'all 0.2s ease',
                width: iconOnly ? 'auto' : (copied ? '80px' : 'auto'),
                minWidth: iconOnly ? 'auto' : '70px',
                justifyContent: 'center',
                ...style
            }}
            disabled={disabled}
            onClick={handleCopy}
            title={iconOnly ? label : undefined}
        >
            {copied ? (
                <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--success)' }}>
                        <polyline points="20 6 9 17 4 12" />
                    </svg>
                    {!iconOnly && <span style={{ color: 'var(--success)' }}>{copiedLabel}</span>}
                </>
            ) : (
                <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                    </svg>
                    {!iconOnly && <span>{label}</span>}
                </>
            )}
        </button>
    );
}
