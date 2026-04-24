import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

type ToastType = 'info' | 'success' | 'warning' | 'error';

interface ToastOptions {
    duration?: number;
    details?: string;
}

interface ToastItem {
    id: number;
    type: ToastType;
    message: string;
    details?: string;
    duration: number;
}

interface ToastContextValue {
    show: (type: ToastType, message: string, options?: ToastOptions) => void;
    info: (message: string, options?: ToastOptions) => void;
    success: (message: string, options?: ToastOptions) => void;
    warning: (message: string, options?: ToastOptions) => void;
    error: (message: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TYPE_META: Record<ToastType, { color: string; icon: string }> = {
    info: { color: 'var(--info)', icon: 'ℹ' },
    success: { color: 'var(--success)', icon: '✓' },
    warning: { color: 'var(--warning)', icon: '⚠' },
    error: { color: 'var(--error)', icon: '✕' },
};

let idSeq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
    const [items, setItems] = useState<ToastItem[]>([]);

    const remove = useCallback((id: number) => {
        setItems((list) => list.filter((t) => t.id !== id));
    }, []);

    const show = useCallback((type: ToastType, message: string, options: ToastOptions = {}) => {
        const id = ++idSeq;
        const duration = options.duration ?? (type === 'error' ? 6000 : 3500);
        setItems((list) => [...list, { id, type, message, details: options.details, duration }]);
    }, []);

    const value: ToastContextValue = {
        show,
        info: (m, o) => show('info', m, o),
        success: (m, o) => show('success', m, o),
        warning: (m, o) => show('warning', m, o),
        error: (m, o) => show('error', m, o),
    };

    return (
        <ToastContext.Provider value={value}>
            {children}
            <div
                style={{
                    position: 'fixed',
                    top: '20px',
                    right: '20px',
                    zIndex: 10001,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px',
                    pointerEvents: 'none',
                    maxWidth: '380px',
                }}
            >
                {items.map((t) => (
                    <ToastCard key={t.id} item={t} onClose={() => remove(t.id)} />
                ))}
            </div>
        </ToastContext.Provider>
    );
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
    const [showDetails, setShowDetails] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const timerRef = useRef<number | null>(null);
    const meta = TYPE_META[item.type];

    useEffect(() => {
        timerRef.current = window.setTimeout(() => {
            setLeaving(true);
            window.setTimeout(onClose, 180);
        }, item.duration);
        return () => { if (timerRef.current) window.clearTimeout(timerRef.current); };
    }, [item.duration, onClose]);

    const pause = () => { if (timerRef.current) window.clearTimeout(timerRef.current); };

    return (
        <div
            role="status"
            onMouseEnter={pause}
            style={{
                pointerEvents: 'auto',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderLeft: `3px solid ${meta.color}`,
                borderRadius: '8px',
                padding: '12px 14px',
                boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '10px',
                fontSize: '13px',
                color: 'var(--text-primary)',
                opacity: leaving ? 0 : 1,
                transform: leaving ? 'translateX(20px)' : 'translateX(0)',
                transition: 'opacity 180ms ease, transform 180ms ease',
                minWidth: '240px',
            }}
        >
            <span
                aria-hidden
                style={{
                    width: '18px', height: '18px', borderRadius: '50%',
                    background: meta.color, color: '#fff',
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '11px', fontWeight: 700, flexShrink: 0, marginTop: '1px',
                }}
            >{meta.icon}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ lineHeight: 1.5, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>{item.message}</div>
                {item.details && (
                    <>
                        <button
                            type="button"
                            onClick={() => setShowDetails((v) => !v)}
                            style={{
                                background: 'none', border: 'none', padding: 0,
                                color: 'var(--text-tertiary)', fontSize: '11px',
                                cursor: 'pointer', textDecoration: 'underline', marginTop: '4px',
                            }}
                        >
                            {showDetails ? '收起详情' : '查看详情'}
                        </button>
                        {showDetails && (
                            <pre style={{
                                marginTop: '6px',
                                padding: '8px',
                                background: 'var(--bg-tertiary)',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '4px',
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                maxHeight: '160px',
                                overflow: 'auto',
                                whiteSpace: 'pre-wrap',
                                wordBreak: 'break-all',
                                fontFamily: 'var(--font-mono)',
                            }}>{item.details}</pre>
                        )}
                    </>
                )}
            </div>
            <button
                type="button"
                onClick={() => { setLeaving(true); window.setTimeout(onClose, 180); }}
                aria-label="Close"
                style={{
                    background: 'none', border: 'none', padding: 0,
                    color: 'var(--text-tertiary)', cursor: 'pointer',
                    fontSize: '14px', lineHeight: 1, flexShrink: 0,
                }}
            >✕</button>
        </div>
    );
}

export function useToast() {
    const ctx = useContext(ToastContext);
    if (!ctx) throw new Error('useToast must be used within ToastProvider');
    return ctx;
}
