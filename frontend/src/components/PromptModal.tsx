import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

interface PromptModalProps {
    open: boolean;
    title: string;
    placeholder?: string;
    onConfirm: (value: string) => void;
    onCancel: () => void;
}

export default function PromptModal({ open, title, placeholder, onConfirm, onCancel }: PromptModalProps) {
    const { t } = useTranslation();
    const [value, setValue] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (open) {
            setValue('');
            setTimeout(() => inputRef.current?.focus(), 100);
        }
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
                width: '400px', maxWidth: '90vw', border: '1px solid var(--border-subtle)',
                boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}>
                <h4 style={{ marginBottom: '16px', fontSize: '15px' }}>{title}</h4>
                <input
                    ref={inputRef}
                    className="input"
                    value={value}
                    onChange={e => setValue(e.target.value)}
                    placeholder={placeholder || ''}
                    onKeyDown={e => {
                        if (e.key === 'Enter' && value.trim()) onConfirm(value.trim());
                        if (e.key === 'Escape') onCancel();
                    }}
                    style={{ width: '100%', marginBottom: '16px' }}
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button className="btn btn-secondary" onClick={onCancel}>{t('common.confirmActions.cancelLabel')}</button>
                    <button className="btn btn-primary" onClick={() => { if (value.trim()) onConfirm(value.trim()); }}
                        disabled={!value.trim()}>{t('common.confirmActions.confirmLabel')}</button>
                </div>
            </div>
        </div>
    );
}
