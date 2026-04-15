/**
 * useDropZone — reusable drag-and-drop file upload hook.
 *
 * Uses a counter-based approach to handle nested elements correctly:
 * dragenter/dragleave fire on every child element, so a simple boolean
 * would flicker. The counter increments on dragenter and decrements on
 * dragleave; isDragging is true when counter > 0.
 */
import { useState, useRef, useCallback, type DragEvent } from 'react';

export interface UseDropZoneOptions {
    /** Callback when files are dropped. Receives the filtered file list. */
    onDrop: (files: File[]) => void;
    /** When true, the drop zone is inactive (no visual feedback, drops ignored). */
    disabled?: boolean;
    /**
     * Optional comma-separated list of accepted MIME types or extensions.
     * e.g. ".json" or "image/*,.pdf"
     * Files not matching are silently filtered out.
     */
    accept?: string;
}

export interface UseDropZoneReturn {
    /** True when a drag-with-files is hovering over the zone. */
    isDragging: boolean;
    /** Spread these onto the container element acting as the drop zone. */
    dropZoneProps: {
        onDragEnter: (e: DragEvent) => void;
        onDragOver: (e: DragEvent) => void;
        onDragLeave: (e: DragEvent) => void;
        onDrop: (e: DragEvent) => void;
    };
}

/** Check whether a drag event contains files (vs plain text / URLs). */
function hasFiles(e: DragEvent): boolean {
    if (e.dataTransfer?.types) {
        for (const t of Array.from(e.dataTransfer.types)) {
            if (t === 'Files') return true;
        }
    }
    return false;
}

/** Filter a FileList by an accept string (same format as <input accept>). */
function filterFiles(files: FileList, accept?: string): File[] {
    const list = Array.from(files);
    if (!accept) return list;

    const tokens = accept.split(',').map(t => t.trim().toLowerCase());

    return list.filter(file => {
        const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
        const mime = file.type.toLowerCase();

        return tokens.some(token => {
            if (token.startsWith('.')) return ext === token;
            if (token.endsWith('/*')) return mime.startsWith(token.slice(0, -1));
            return mime === token;
        });
    });
}

export function useDropZone({ onDrop, disabled = false, accept }: UseDropZoneOptions): UseDropZoneReturn {
    const [isDragging, setIsDragging] = useState(false);
    const counterRef = useRef(0);

    const handleDragEnter = useCallback((e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled || !hasFiles(e)) return;
        counterRef.current += 1;
        if (counterRef.current === 1) setIsDragging(true);
    }, [disabled]);

    const handleDragOver = useCallback((e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (!disabled && hasFiles(e)) {
            e.dataTransfer.dropEffect = 'copy';
        }
    }, [disabled]);

    const handleDragLeave = useCallback((e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled) return;
        counterRef.current -= 1;
        if (counterRef.current <= 0) {
            counterRef.current = 0;
            setIsDragging(false);
        }
    }, [disabled]);

    const handleDrop = useCallback((e: DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        counterRef.current = 0;
        setIsDragging(false);
        if (disabled) return;

        const rawFiles = e.dataTransfer?.files;
        if (!rawFiles || rawFiles.length === 0) return;

        const filtered = filterFiles(rawFiles, accept);
        if (filtered.length > 0) {
            onDrop(filtered);
        }
    }, [disabled, accept, onDrop]);

    return {
        isDragging,
        dropZoneProps: {
            onDragEnter: handleDragEnter,
            onDragOver: handleDragOver,
            onDragLeave: handleDragLeave,
            onDrop: handleDrop,
        },
    };
}
