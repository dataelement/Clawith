import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'outline' | 'ghost';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: Variant;
    children: ReactNode;
}

export default function Button({ variant = 'primary', className, children, ...rest }: Props) {
    const classes = ['atlas-btn', `atlas-btn--${variant}`, className].filter(Boolean).join(' ');
    return (
        <button className={classes} {...rest}>
            {children}
        </button>
    );
}
