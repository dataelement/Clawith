import { useRef, useEffect } from 'react';

interface ConfirmModalProps {
    open: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

export default function ConfirmModal({ open, title, message, confirmLabel = '确定', cancelLabel = '取消', danger, onConfirm, onCancel }: ConfirmModalProps) {
    const btnRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        if (open) setTimeout(() => btnRef.current?.focus(), 100);
    }, [open]);

    if (!open) return null;

    return (
        <div
            className="dialog-backdrop"
            style={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.5)',
                backdropFilter: 'blur(4px)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000,
                animation: 'backdropFadeIn 0.2s ease forwards',
            }}
            onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
            <div
                className="dialog-content"
                style={{
                    background: 'var(--bg-primary)',
                    borderRadius: '16px',
                    padding: '24px',
                    width: '380px',
                    maxWidth: '90vw',
                    border: '1px solid var(--border-default)',
                    boxShadow: '0 24px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05) inset',
                    animation: 'modalFadeIn 0.25s cubic-bezier(0.34, 1.56, 0.64, 1) forwards',
                }}
            >
                <h4 style={{ marginBottom: '12px', fontSize: '15px' }}>{title}</h4>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px', lineHeight: 1.5 }}>{message}</p>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={onCancel}>{cancelLabel}</button>
                    <button ref={btnRef} className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>{confirmLabel}</button>
                </div>
            </div>
        </div>
    );
}
