import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

type DialogType = 'info' | 'success' | 'warning' | 'error';

interface AlertOptions {
    title?: string;
    type?: DialogType;
    details?: string;
    confirmLabel?: string;
}

interface ConfirmOptions {
    title?: string;
    danger?: boolean;
    confirmLabel?: string;
    cancelLabel?: string;
}

interface DialogContextValue {
    alert: (message: string, options?: AlertOptions) => Promise<void>;
    confirm: (message: string, options?: ConfirmOptions) => Promise<boolean>;
}

const DialogContext = createContext<DialogContextValue | null>(null);

type ModalState =
    | { kind: 'alert'; message: string; options: AlertOptions; resolve: () => void }
    | { kind: 'confirm'; message: string; options: ConfirmOptions; resolve: (ok: boolean) => void }
    | null;

const TYPE_META: Record<DialogType, { color: string; icon: string }> = {
    info: { color: 'var(--info)', icon: 'ℹ' },
    success: { color: 'var(--success)', icon: '✓' },
    warning: { color: 'var(--warning)', icon: '⚠' },
    error: { color: 'var(--error)', icon: '✕' },
};

export function DialogProvider({ children }: { children: ReactNode }) {
    const [state, setState] = useState<ModalState>(null);

    const alert = useCallback(
        (message: string, options: AlertOptions = {}) =>
            new Promise<void>((resolve) => setState({ kind: 'alert', message, options, resolve })),
        [],
    );

    const confirm = useCallback(
        (message: string, options: ConfirmOptions = {}) =>
            new Promise<boolean>((resolve) => setState({ kind: 'confirm', message, options, resolve })),
        [],
    );

    const close = useCallback((result?: boolean) => {
        setState((s) => {
            if (!s) return null;
            if (s.kind === 'alert') s.resolve();
            else s.resolve(!!result);
            return null;
        });
    }, []);

    return (
        <DialogContext.Provider value={{ alert, confirm }}>
            {children}
            {state && <DialogModal state={state} onClose={close} />}
        </DialogContext.Provider>
    );
}

function DialogModal({ state, onClose }: { state: NonNullable<ModalState>; onClose: (result?: boolean) => void }) {
    const btnRef = useRef<HTMLButtonElement>(null);
    const [showDetails, setShowDetails] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => btnRef.current?.focus(), 50);
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose(false);
            if (e.key === 'Enter' && state.kind === 'alert') onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => { clearTimeout(timer); window.removeEventListener('keydown', onKey); };
    }, [state, onClose]);

    const isConfirm = state.kind === 'confirm';
    const type: DialogType = isConfirm
        ? (state.options.danger ? 'error' : 'info')
        : (state.options.type ?? 'info');
    const meta = TYPE_META[type];
    const title = state.options.title
        ?? (isConfirm ? '请确认' : type === 'error' ? '出错了' : type === 'success' ? '成功' : type === 'warning' ? '提示' : '提示');
    const details = !isConfirm ? state.options.details : undefined;

    return (
        <div
            style={{
                position: 'fixed', inset: 0,
                background: 'rgba(0,0,0,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 10000,
            }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(false); }}
        >
            <div
                role="dialog"
                aria-modal="true"
                style={{
                    background: 'var(--bg-primary)',
                    borderRadius: '12px',
                    padding: '24px',
                    width: '420px',
                    maxWidth: '90vw',
                    maxHeight: '80vh',
                    overflow: 'auto',
                    border: '1px solid var(--border-subtle)',
                    boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                    <span
                        aria-hidden
                        style={{
                            width: '22px', height: '22px', borderRadius: '50%',
                            background: meta.color, color: '#fff',
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '13px', fontWeight: 700, flexShrink: 0,
                        }}
                    >{meta.icon}</span>
                    <h4 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>{title}</h4>
                </div>
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: details ? '12px' : '20px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {state.message}
                </div>
                {details && (
                    <div style={{ marginBottom: '20px' }}>
                        <button
                            type="button"
                            onClick={() => setShowDetails((v) => !v)}
                            style={{
                                background: 'none', border: 'none', padding: 0,
                                color: 'var(--text-tertiary)', fontSize: '12px',
                                cursor: 'pointer', textDecoration: 'underline',
                            }}
                        >
                            {showDetails ? '收起详细信息' : '查看详细信息'}
                        </button>
                        {showDetails && (
                            <pre style={{
                                marginTop: '8px',
                                padding: '10px 12px',
                                background: 'var(--bg-tertiary)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '6px',
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                maxHeight: '240px',
                                overflow: 'auto',
                                whiteSpace: 'pre-wrap',
                                wordBreak: 'break-all',
                                fontFamily: 'var(--font-mono)',
                            }}>{details}</pre>
                        )}
                    </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    {isConfirm && (
                        <button className="btn btn-secondary" onClick={() => onClose(false)}>
                            {state.options.cancelLabel ?? '取消'}
                        </button>
                    )}
                    <button
                        ref={btnRef}
                        className={isConfirm && state.options.danger ? 'btn btn-danger' : 'btn btn-primary'}
                        onClick={() => onClose(true)}
                    >
                        {isConfirm
                            ? (state.options.confirmLabel ?? '确定')
                            : (state.options.confirmLabel ?? '确定')}
                    </button>
                </div>
            </div>
        </div>
    );
}

export function useDialog() {
    const ctx = useContext(DialogContext);
    if (!ctx) throw new Error('useDialog must be used within DialogProvider');
    return ctx;
}
