import type { ToastState } from '../hooks/useToast';

const COLOR_BY_TYPE: Record<ToastState['type'], string> = {
    success: 'rgba(34, 197, 94, 0.92)',
    error: 'rgba(239, 68, 68, 0.92)',
    info: 'rgba(59, 130, 246, 0.92)',
};

export function Toast({ toast }: { toast: ToastState | null }) {
    if (!toast) return null;
    return (
        <div
            style={{
                position: 'fixed',
                top: 20,
                right: 20,
                zIndex: 20000,
                padding: '12px 20px',
                borderRadius: 8,
                background: COLOR_BY_TYPE[toast.type],
                color: '#fff',
                fontSize: 14,
                fontWeight: 500,
                boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                maxWidth: '60vw',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
            }}
        >
            {toast.message}
        </div>
    );
}

export default Toast;
