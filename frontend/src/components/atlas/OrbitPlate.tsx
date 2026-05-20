interface Props {
    /** Label rendered next to the assistant satellite. Defaults to "I — ASSISTANT" */
    assistantLabel?: string;
    /** Label rendered below the central founder marker. Defaults to "FOUNDER" */
    founderLabel?: string;
    width?: number;
    className?: string;
}

const ORBIT_STARS: Array<[number, number, number, number]> = [
    // [x, y, r, opacity] in local coords (origin at ellipse center)
    [-260, -40, 0.9, 0.55],
    [260, 70, 0.9, 0.5],
    [-90, 80, 0.8, 0.45],
    [40, -90, 0.7, 0.4],
    [180, -55, 1.0, 0.5],
];

export default function OrbitPlate({
    assistantLabel = 'I — ASSISTANT',
    founderLabel = 'FOUNDER',
    width = 600,
    className,
}: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    // ellipse geometry — wide horizontal arc
    const rx = 200;
    const ry = 60;
    // assistant satellite sits at the rightmost point of the ellipse
    const ax = rx;
    const ay = 0;

    return (
        <svg
            className={cls}
            viewBox="0 0 600 320"
            width={width}
            height={(width * 320) / 600}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            <g transform="translate(300 160)">
                {/* Orbit ellipse */}
                <ellipse cx="0" cy="0" rx={rx} ry={ry} opacity="0.55" />

                {/* Scattered stars */}
                {ORBIT_STARS.map(([x, y, r, o], i) => (
                    <circle key={i} cx={x} cy={y} r={r} fill="currentColor" stroke="none" opacity={o} />
                ))}

                {/* Center FOUNDER marker — pulsing halo + outer ring + breathing dot */}
                <circle cx="0" cy="0" r="10" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.5">
                    <animate attributeName="r" values="10;18" dur="3.2s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.45;0" dur="3.2s" repeatCount="indefinite" />
                </circle>
                <circle cx="0" cy="0" r="10" />
                <circle cx="0" cy="0" r="2.6" fill="currentColor" stroke="none">
                    <animate attributeName="opacity" values="1;0.6;1" dur="3.2s" repeatCount="indefinite" />
                </circle>

                {/* Founder label */}
                <text
                    x="0"
                    y="30"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill="currentColor"
                    stroke="none"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontSize="10"
                    style={{ letterSpacing: '0.14em' }}
                >
                    {founderLabel}
                </text>

                {/* Assistant satellite — pulsing halo + open ring + center dot.
                    The halo ring expands and fades, giving a quiet "alive" pulse. */}
                <circle cx={ax} cy={ay} r="6" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.6">
                    <animate attributeName="r" values="6;14" dur="2.4s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.55;0" dur="2.4s" repeatCount="indefinite" />
                </circle>
                <circle cx={ax} cy={ay} r="6" />
                <circle cx={ax} cy={ay} r="1.6" fill="currentColor" stroke="none">
                    <animate attributeName="opacity" values="1;0.5;1" dur="2.4s" repeatCount="indefinite" />
                </circle>

                {/* Assistant label */}
                <text
                    x={ax + 12}
                    y={ay - 14}
                    fill="currentColor"
                    stroke="none"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontSize="10"
                    opacity="0.85"
                    style={{ letterSpacing: '0.14em' }}
                >
                    {assistantLabel}
                </text>
            </g>
        </svg>
    );
}
