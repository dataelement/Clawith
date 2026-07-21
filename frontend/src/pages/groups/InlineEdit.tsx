import { useEffect, useRef, useState } from 'react';

interface InlineEditProps {
    initialValue?: string;
    placeholder?: string;
    className?: string;
    /** Called with the trimmed value. Empty means "nothing typed" — the caller decides what that means. */
    onCommit: (value: string) => void;
    onCancel: () => void;
}

/**
 * A one-line text field that edits a name in place — used for creating a group, creating a session,
 * and renaming a session, so all three feel the same. Enter or losing focus commits; Escape cancels.
 * Nothing is created or renamed until commit, so clicking "new" no longer means "already created".
 */
export default function InlineEdit({
    initialValue = '',
    placeholder,
    className,
    onCommit,
    onCancel,
}: InlineEditProps) {
    const [value, setValue] = useState(initialValue);
    const inputRef = useRef<HTMLInputElement>(null);
    const settled = useRef(false);

    useEffect(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        el.select();
    }, []);

    // Enter and blur both settle; the guard stops a blur that fires right after Enter/Escape from
    // committing a second time.
    const settle = (commit: boolean) => {
        if (settled.current) return;
        settled.current = true;
        if (commit) onCommit(value.trim());
        else onCancel();
    };

    return (
        <input
            ref={inputRef}
            className={className}
            value={value}
            placeholder={placeholder}
            onChange={(event) => setValue(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
                // Let the IME keep Enter while composing pinyin etc.
                if (event.nativeEvent.isComposing) return;
                if (event.key === 'Enter') {
                    event.preventDefault();
                    settle(true);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    settle(false);
                }
            }}
            onBlur={() => settle(true)}
        />
    );
}
