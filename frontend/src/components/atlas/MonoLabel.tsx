import type { ReactNode, HTMLAttributes } from 'react';

interface Props extends HTMLAttributes<HTMLSpanElement> {
    children: ReactNode;
    as?: 'span' | 'div' | 'p';
}

export default function MonoLabel({ children, as = 'span', className, ...rest }: Props) {
    const Tag = as as any;
    const classes = ['atlas-mono', className].filter(Boolean).join(' ');
    return (
        <Tag className={classes} {...rest}>
            {children}
        </Tag>
    );
}
