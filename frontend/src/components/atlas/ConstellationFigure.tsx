interface Props {
    width?: number;
    className?: string;
}

type Pt = { x: number; y: number; size: number };

const points = {
    head:        { x: 200, y: 80,  size: 12 },
    lShoulder:   { x: 158, y: 165, size: 10 },
    rShoulder:   { x: 242, y: 165, size: 10 },
    lElbow:      { x: 130, y: 240, size: 8 },
    rElbow:      { x: 270, y: 240, size: 8 },
    lHand:       { x: 108, y: 325, size: 8 },
    rHand:       { x: 292, y: 325, size: 8 },
    hips:        { x: 200, y: 360, size: 10 },
    feet:        { x: 200, y: 540, size: 12 },
} as const;

function CrossStar({ x, y, size }: Pt) {
    const h = size / 2;
    return (
        <g>
            <line x1={x - h} y1={y} x2={x + h} y2={y} />
            <line x1={x} y1={y - h} x2={x} y2={y + h} />
        </g>
    );
}

export default function ConstellationFigure({ width = 320, className }: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    const connections: Array<[Pt, Pt]> = [
        [points.head, points.lShoulder],
        [points.head, points.rShoulder],
        [points.lShoulder, points.lElbow],
        [points.lElbow, points.lHand],
        [points.rShoulder, points.rElbow],
        [points.rElbow, points.rHand],
        [points.lShoulder, points.hips],
        [points.rShoulder, points.hips],
        [points.hips, points.feet],
    ];
    const stars = Object.values(points) as Pt[];

    return (
        <svg
            className={cls}
            viewBox="0 0 400 600"
            width={width}
            height={(width * 600) / 400}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            {/* Hairline connections — drawn first so stars overlay */}
            <g opacity="0.45">
                {connections.map(([a, b], i) => (
                    <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                ))}
            </g>
            {/* Cross-mark stars */}
            {stars.map((p, i) => (
                <CrossStar key={i} {...p} />
            ))}
        </svg>
    );
}
