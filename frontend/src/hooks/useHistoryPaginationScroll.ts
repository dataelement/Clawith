import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import type {
    KeyboardEvent as ReactKeyboardEvent,
    PointerEvent as ReactPointerEvent,
    RefObject,
    TouchEvent as ReactTouchEvent,
    WheelEvent as ReactWheelEvent,
} from 'react';

interface PrependScrollAnchorOptions<T extends HTMLElement> {
    containerRef: RefObject<T | null>;
    itemCount: number;
    scopeKey: string | null | undefined;
}

interface ScrollAnchor<T extends HTMLElement> {
    element: T;
    scrollHeight: number;
    scrollTop: number;
}

/** Preserve the visible content position while older rows are prepended. */
export function usePrependScrollAnchor<T extends HTMLElement>({
    containerRef,
    itemCount,
    scopeKey,
}: PrependScrollAnchorOptions<T>) {
    const anchorRef = useRef<ScrollAnchor<T> | null>(null);
    const releaseFrameRef = useRef<number | null>(null);
    const isPrependingRef = useRef(false);

    const cancelReleaseFrame = useCallback(() => {
        if (releaseFrameRef.current == null) return;
        window.cancelAnimationFrame(releaseFrameRef.current);
        releaseFrameRef.current = null;
    }, []);

    const captureAnchor = useCallback(() => {
        const element = containerRef.current;
        if (!element) return;
        cancelReleaseFrame();
        isPrependingRef.current = true;
        anchorRef.current = {
            element,
            scrollHeight: element.scrollHeight,
            scrollTop: element.scrollTop,
        };
    }, [cancelReleaseFrame, containerRef]);

    useLayoutEffect(() => {
        const anchor = anchorRef.current;
        if (!anchor) return;
        anchorRef.current = null;
        if (anchor.element === containerRef.current) {
            anchor.element.scrollTop = anchor.scrollTop
                + (anchor.element.scrollHeight - anchor.scrollHeight);
        }
        cancelReleaseFrame();
        releaseFrameRef.current = window.requestAnimationFrame(() => {
            isPrependingRef.current = false;
            releaseFrameRef.current = null;
        });
    }, [cancelReleaseFrame, containerRef, itemCount]);

    useEffect(() => {
        anchorRef.current = null;
        isPrependingRef.current = false;
        cancelReleaseFrame();
    }, [cancelReleaseFrame, scopeKey]);

    useEffect(() => () => cancelReleaseFrame(), [cancelReleaseFrame]);

    return { captureAnchor, isPrependingRef };
}

interface OlderHistoryGestureOptions<T extends HTMLElement> {
    containerRef: RefObject<T | null>;
    canLoad: boolean;
    onLoadMore: () => void | Promise<void>;
    beforeLoad?: () => void;
    topThreshold?: number;
}

/** Request one older page only in response to an explicit upward-history gesture. */
export function useOlderHistoryGesture<T extends HTMLElement>({
    containerRef,
    canLoad,
    onLoadMore,
    beforeLoad,
    topThreshold = 100,
}: OlderHistoryGestureOptions<T>) {
    const requestInFlightRef = useRef(false);
    const pointerIntentUntilRef = useRef(0);
    const touchStartYRef = useRef<number | null>(null);
    const touchPageRequestedRef = useRef(false);
    const wheelGestureLatchedRef = useRef(false);
    const wheelGestureReleaseRef = useRef<number | null>(null);

    const requestOlder = useCallback(async () => {
        const element = containerRef.current;
        if (!element || element.scrollTop > topThreshold || !canLoad || requestInFlightRef.current) return;
        requestInFlightRef.current = true;
        pointerIntentUntilRef.current = 0;
        beforeLoad?.();
        try {
            await onLoadMore();
        } finally {
            requestInFlightRef.current = false;
        }
    }, [beforeLoad, canLoad, containerRef, onLoadMore, topThreshold]);

    const onScroll = useCallback(() => {
        if (Date.now() > pointerIntentUntilRef.current) return;
        void requestOlder();
    }, [requestOlder]);

    const onWheelCapture = useCallback((event: ReactWheelEvent<T>) => {
        if (event.deltaY >= 0) return;
        pointerIntentUntilRef.current = Date.now() + 1000;
        if (wheelGestureReleaseRef.current != null) {
            window.clearTimeout(wheelGestureReleaseRef.current);
        }
        wheelGestureReleaseRef.current = window.setTimeout(() => {
            wheelGestureLatchedRef.current = false;
            wheelGestureReleaseRef.current = null;
        }, 180);
        if (wheelGestureLatchedRef.current) return;
        wheelGestureLatchedRef.current = true;
        void requestOlder();
    }, [requestOlder]);

    const onPointerDownCapture = useCallback((_event: ReactPointerEvent<T>) => {
        pointerIntentUntilRef.current = Date.now() + 1000;
    }, []);

    const onTouchStartCapture = useCallback((event: ReactTouchEvent<T>) => {
        touchStartYRef.current = event.touches[0]?.clientY ?? null;
        touchPageRequestedRef.current = false;
    }, []);

    const onTouchMoveCapture = useCallback((event: ReactTouchEvent<T>) => {
        const startY = touchStartYRef.current;
        const currentY = event.touches[0]?.clientY;
        if (startY == null || currentY == null || currentY - startY <= 6 || touchPageRequestedRef.current) return;
        touchPageRequestedRef.current = true;
        touchStartYRef.current = currentY;
        void requestOlder();
    }, [requestOlder]);

    const onTouchEndCapture = useCallback(() => {
        touchStartYRef.current = null;
        touchPageRequestedRef.current = false;
    }, []);

    const onKeyDownCapture = useCallback((event: ReactKeyboardEvent<T>) => {
        if (event.repeat || !['ArrowUp', 'PageUp', 'Home'].includes(event.key)) return;
        pointerIntentUntilRef.current = Date.now() + 1000;
        void requestOlder();
    }, [requestOlder]);

    useEffect(() => () => {
        if (wheelGestureReleaseRef.current != null) {
            window.clearTimeout(wheelGestureReleaseRef.current);
        }
    }, []);

    return {
        onKeyDownCapture,
        onPointerDownCapture,
        onScroll,
        onTouchMoveCapture,
        onTouchStartCapture,
        onTouchEndCapture,
        onWheelCapture,
        requestOlder,
    };
}
