import { useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

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

export default function ConfirmModal({ open, title, message, confirmLabel, cancelLabel, danger, onConfirm, onCancel }: ConfirmModalProps) {
    const { t } = useTranslation();
    const btnRef = useRef<HTMLButtonElement>(null);
    const resolvedConfirmLabel = confirmLabel ?? t('common.confirmActions.confirmLabel');
    const resolvedCancelLabel = cancelLabel ?? t('common.confirmActions.cancelLabel');

    useEffect(() => {
        if (open) setTimeout(() => btnRef.current?.focus(), 100);
    }, [open]);

    if (!open) return null;

    return (
        <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 10000,
        }} onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
            <div style={{
                background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px',
                width: '380px', maxWidth: '90vw', border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}>
                <h4 style={{ marginBottom: '12px', fontSize: '15px' }}>{title}</h4>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px', lineHeight: 1.5 }}>{message}</p>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={onCancel}>{resolvedCancelLabel}</button>
                    <button ref={btnRef} className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>{resolvedConfirmLabel}</button>
                </div>
            </div>
        </div>
    );
}
