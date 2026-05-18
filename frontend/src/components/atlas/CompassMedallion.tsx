interface Props {
    size?: number;
    className?: string;
}

export default function CompassMedallion({ size = 160, className }: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    const cx = 100;
    const cy = 100;
    const rOuter = 80;
    const rInner = 30;

    // Inscribed squares: one axis-aligned, one rotated 45° → 8-point star
    const sqA = rOuter / Math.SQRT2; // half-side for axis-aligned square
    const squareA = [
        [cx - sqA, cy - sqA],
        [cx + sqA, cy - sqA],
        [cx + sqA, cy + sqA],
        [cx - sqA, cy + sqA],
    ];
    // Rotated 45°: corners at the four cardinal points on the circle
    const squareB = [
        [cx, cy - rOuter],
        [cx + rOuter, cy],
        [cx, cy + rOuter],
        [cx - rOuter, cy],
    ];
    const toPathPoints = (pts: number[][]) => pts.map((p) => p.join(',')).join(' ');

    return (
        <svg
            className={cls}
            viewBox="0 0 200 200"
            width={size}
            height={size}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            {/* Outer ring */}
            <circle cx={cx} cy={cy} r={rOuter} />

            {/* Tick marks every 15° on the outer ring */}
            {Array.from({ length: 24 }, (_, i) => {
                const theta = (i * 15 * Math.PI) / 180 - Math.PI / 2; // start at top
                const x1 = cx + Math.cos(theta) * rOuter;
                const y1 = cy + Math.sin(theta) * rOuter;
                const tickLen = i % 6 === 0 ? 6 : 3;
                const x2 = cx + Math.cos(theta) * (rOuter + tickLen);
                const y2 = cy + Math.sin(theta) * (rOuter + tickLen);
                return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />;
            })}

            {/* Two squares — 8-point star */}
            <polygon points={toPathPoints(squareA)} />
            <polygon points={toPathPoints(squareB)} />

            {/* Inner ring */}
            <circle cx={cx} cy={cy} r={rInner} />

            {/* Center 4-point cross */}
            <line x1={cx - 5} y1={cy} x2={cx + 5} y2={cy} />
            <line x1={cx} y1={cy - 5} x2={cx} y2={cy + 5} />

            {/* Cardinal labels */}
            <g
                fill="currentColor"
                stroke="none"
                fontFamily="JetBrains Mono, ui-monospace, monospace"
                fontSize="8"
                fontWeight="500"
                letterSpacing="0.08em"
                textAnchor="middle"
                dominantBaseline="middle"
            >
                <text x={cx} y="10">I</text>
                <text x="192" y={cy}>II</text>
                <text x={cx} y="192">III</text>
                <text x="8" y={cy}>IV</text>
            </g>
        </svg>
    );
}
