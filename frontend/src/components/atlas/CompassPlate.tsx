interface Props {
    size?: number;
    className?: string;
}

/** Tiny scattered "stars" inside the compass rings — fixed positions for stability */
const COMPASS_STARS: Array<[number, number, number, number]> = [
    // [x, y, r, opacity]
    [60, -110, 1.1, 0.62],
    [205, -55, 1.3, 0.72],
    [-115, 95, 0.9, 0.5],
    [-30, 150, 1.1, 0.6],
    [125, 175, 0.95, 0.55],
];

export default function CompassPlate({ size = 620, className }: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    // Five concentric ring radii — outermost biggest, scaled to fill the canvas
    const rings = [270, 220, 165, 110, 55];
    const rOuter = rings[0];
    const labelDist = rOuter + 22;

    return (
        <svg
            className={cls}
            viewBox="0 0 600 600"
            width={size}
            height={size}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            <g transform="translate(300 300)">
                {/* Concentric rings — outer is faintest, inner gradually stronger */}
                {rings.map((r, i) => (
                    <circle
                        key={i}
                        cx="0"
                        cy="0"
                        r={r}
                        opacity={i === 0 ? 0.42 : 0.3 + i * 0.04}
                    />
                ))}

                {/* Cardinal axis hairlines that cross the entire chart */}
                <line x1={-rOuter} y1="0" x2={rOuter} y2="0" opacity="0.18" />
                <line x1="0" y1={-rOuter} x2="0" y2={rOuter} opacity="0.18" />

                {/* Dense tick marks on the outer ring — every 3°, longer at every 15°
                    (skipping the cardinal positions, which get labels) */}
                {Array.from({ length: 120 }, (_, i) => {
                    const deg = i * 3;
                    if (deg % 90 === 0) return null; // skip N/S/E/W
                    const a = (deg * Math.PI) / 180;
                    const isMajor = deg % 15 === 0;
                    const r1 = rOuter;
                    const r2 = rOuter + (isMajor ? 8 : 4);
                    return (
                        <line
                            key={i}
                            x1={r1 * Math.cos(a)}
                            y1={r1 * Math.sin(a)}
                            x2={r2 * Math.cos(a)}
                            y2={r2 * Math.sin(a)}
                            opacity={isMajor ? 0.55 : 0.32}
                        />
                    );
                })}

                {/* Scattered stars (faint dots inside the rings) */}
                {COMPASS_STARS.map(([x, y, r, o], i) => (
                    <circle key={i} cx={x} cy={y} r={r} fill="currentColor" stroke="none" opacity={o} />
                ))}

                {/* Center origin marker — open ring + tiny dot */}
                <circle cx="0" cy="0" r="6" />
                <circle cx="0" cy="0" r="1.4" fill="currentColor" stroke="none" />

                {/* Cardinal labels */}
                <g
                    fill="currentColor"
                    stroke="none"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontSize="11"
                    opacity="0.6"
                    style={{ letterSpacing: '0.1em' }}
                >
                    <text x="0" y={-labelDist} textAnchor="middle" dominantBaseline="middle">N</text>
                    <text x={labelDist} y="0" dominantBaseline="middle">E</text>
                    <text x="0" y={labelDist} textAnchor="middle" dominantBaseline="middle">S</text>
                    <text x={-labelDist} y="0" textAnchor="end" dominantBaseline="middle">W</text>
                </g>

                {/* Origin coordinate next to center marker */}
                <g
                    fill="currentColor"
                    stroke="none"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontSize="9.5"
                    opacity="0.5"
                    style={{ letterSpacing: '0.06em' }}
                >
                    <text x="14" y="-2">00°00′00″</text>
                    <text x="14" y="11">ORIGIN</text>
                </g>
            </g>
        </svg>
    );
}
