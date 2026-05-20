import type { ReactNode } from 'react';

interface Props {
    caption: string;
    children: ReactNode;
    className?: string;
}

export default function Plate({ caption, children, className }: Props) {
    const classes = ['atlas-plate', className].filter(Boolean).join(' ');
    return (
        <div className={classes}>
            <div className="atlas-plate-figure">{children}</div>
            <div className="atlas-plate-caption">{caption}</div>
        </div>
    );
}
