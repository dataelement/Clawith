import { useCallback, useState } from 'react';

export type ToastType = 'success' | 'error' | 'info';

export interface ToastState {
    message: string;
    type: ToastType;
}

export function useToast(durationMs = 3000) {
    const [toast, setToast] = useState<ToastState | null>(null);

    const showToast = useCallback((message: string, type: ToastType = 'success') => {
        setToast({ message, type });
        window.setTimeout(() => setToast(null), durationMs);
    }, [durationMs]);

    const dismissToast = useCallback(() => setToast(null), []);

    return { toast, showToast, dismissToast };
}
