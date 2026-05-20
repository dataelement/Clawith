interface OrbitDef {
    /** Roman numeral / order label */
    id: string;
    /** Role text after the bullet */
    role: string;
    /** Angle in degrees, 0=right, 90=down, -90=up (SVG convention) */
    angle: number;
    /** Where the endpoint sits — inner solid circle or outer ring */
    ring: 'inner' | 'outer';
    /** Filled (active) vs hollow (vacant) endpoint */
    filled: boolean;
}

interface Props {
    size?: number;
    /** Override the orbit list to drive different states */
    orbits?: OrbitDef[];
    /** Right-ascension coordinate at bottom-left */
    alpha?: string;
    /** Declination coordinate at bottom-right */
    delta?: string;
    /** Top label */
    topLabel?: string;
    /** When provided, overrides the I·ASSISTANT orbit's label with this name */
    assistantName?: string;
    className?: string;
}

const DEFAULT_ORBITS: OrbitDef[] = [
    { id: 'I',   role: 'ASSISTANT', angle: -28,  ring: 'inner', filled: true },
    { id: 'IV',  role: 'EMPLOYEE',  angle: -78,  ring: 'outer', filled: false },
    { id: 'II',  role: 'EMPLOYEE',  angle: -158, ring: 'outer', filled: false },
    { id: 'V',   role: 'EMPLOYEE',  angle: 150,  ring: 'outer', filled: false },
    { id: 'III', role: 'EMPLOYEE',  angle: 82,   ring: 'outer', filled: false },
];

/** Render an orbit's label — the I·ASSISTANT orbit can be overridden to show
 *  the actual assistant name (e.g. CLAWIEE) instead of "I · ASSISTANT". */
function labelFor(o: OrbitDef, assistantNameOverride?: string): string {
    if (o.id === 'I' && o.role === 'ASSISTANT' && assistantNameOverride) {
        return assistantNameOverride.toUpperCase();
    }
    return `${o.id} · ${o.role}`;
}

/** Scattered tiny stars across the map */
const MAP_STARS: Array<[number, number, number, number]> = [
    [-130, -90, 0.9, 0.5],
    [80, -180, 0.8, 0.45],
    [180, -50, 1.0, 0.55],
    [220, 140, 0.9, 0.5],
    [-200, 60, 0.8, 0.4],
    [-30, 210, 0.9, 0.55],
    [60, -240, 0.7, 0.4],
    [-160, 180, 0.9, 0.5],
    [240, -120, 0.8, 0.45],
    [40, 60, 0.6, 0.35],
    [-80, -200, 0.7, 0.4],
    [120, 220, 0.8, 0.45],
];

export default function UniverseMap({
    size = 600,
    orbits = DEFAULT_ORBITS,
    alpha = 'α 00ʰ00ᵐ',
    delta = 'δ +00°00′',
    topLabel = 'YOUR UNIVERSE',
    assistantName,
    className,
}: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    const cx = 300;
    const cy = 320;
    // Larger rings so the chart matches the user-supplied mock's footprint.
    // padX is just enough horizontal viewBox margin to let the longest
    // outward labels ("II · EMPLOYEE" / "V · EMPLOYEE") fit without clipping.
    const rOuter = 265;
    // Outer pair gap = 10 (~20% wider than before)
    const rOuterOut = rOuter + 5;
    const rOuterIn = rOuter - 5;
    const rInner = 110;
    const padX = 80;
    const vbX = -padX;
    const vbW = 600 + padX * 2;
    const vbH = 670;

    return (
        <svg
            className={cls}
            viewBox={`${vbX} 0 ${vbW} ${vbH}`}
            width={size}
            height={(size * vbH) / vbW}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            {/* Top label */}
            <text
                x={cx}
                y="22"
                textAnchor="middle"
                fill="currentColor"
                stroke="none"
                fontFamily="IBM Plex Mono, ui-monospace, monospace"
                fontSize="11"
                opacity="0.55"
                style={{ letterSpacing: '0.18em' }}
            >
                {topLabel}
            </text>

            {/* Bottom-left stellar coordinate (40px in from the visible left edge) */}
            <text
                x={vbX + 40}
                y={vbH - 28}
                fill="currentColor"
                stroke="none"
                fontFamily="IBM Plex Mono, ui-monospace, monospace"
                fontSize="11"
                opacity="0.5"
                style={{ letterSpacing: '0.1em' }}
            >
                {alpha}
            </text>

            {/* Bottom-right stellar coordinate (40px in from the visible right edge) */}
            <text
                x={vbX + vbW - 40}
                y={vbH - 28}
                textAnchor="end"
                fill="currentColor"
                stroke="none"
                fontFamily="IBM Plex Mono, ui-monospace, monospace"
                fontSize="11"
                opacity="0.5"
                style={{ letterSpacing: '0.1em' }}
            >
                {delta}
            </text>

            <g transform={`translate(${cx} ${cy})`}>
                {/* Outer perimeter — two very faint thin concentric rings,
                    "double-line" / railway-track look */}
                <circle cx="0" cy="0" r={rOuterOut} opacity="0.2" />
                <circle cx="0" cy="0" r={rOuterIn} opacity="0.2" />

                {/* Inner ring — single prominent line */}
                <circle cx="0" cy="0" r={rInner} opacity="0.95" strokeWidth="0.7" />

                {/* Sparse, short tick marks inside the outer pair — every 6°,
                    inset slightly from both rings so they don't bridge the gap. */}
                {Array.from({ length: 60 }, (_, i) => {
                    const deg = i * 6;
                    const a = (deg * Math.PI) / 180;
                    const isMajor = deg % 30 === 0;
                    const r1 = rOuterIn + 2.5;
                    const r2 = rOuterOut - (isMajor ? 0 : 2.5);
                    return (
                        <line
                            key={i}
                            x1={r1 * Math.cos(a)}
                            y1={r1 * Math.sin(a)}
                            x2={r2 * Math.cos(a)}
                            y2={r2 * Math.sin(a)}
                            opacity={isMajor ? 0.32 : 0.2}
                        />
                    );
                })}

                {/* Scattered stars */}
                {MAP_STARS.map(([x, y, r, o], i) => (
                    <circle key={i} cx={x} cy={y} r={r} fill="currentColor" stroke="none" opacity={o} />
                ))}

                {/* Radial orbits with endpoint markers + labels */}
                {orbits.map((o) => {
                    const a = (o.angle * Math.PI) / 180;
                    const r = o.ring === 'inner' ? rInner : rOuter;
                    const ex = r * Math.cos(a);
                    const ey = r * Math.sin(a);
                    const labelOffset = 14;
                    const lx = ex + (Math.abs(ex) < 30 ? 0 : ex > 0 ? labelOffset : -labelOffset);
                    const ly = ey + (ey > 30 ? labelOffset + 6 : ey < -30 ? -8 : -10);
                    const anchor = Math.abs(ex) < 30 ? 'middle' : ex > 0 ? 'start' : 'end';
                    return (
                        <g key={o.id}>
                            <line
                                x1="0"
                                y1="0"
                                x2={ex}
                                y2={ey}
                                strokeDasharray={o.filled ? undefined : '2.5 4'}
                                opacity={o.filled ? 0.85 : 0.45}
                            />
                            {o.filled ? (
                                <>
                                    {/* Pulsing halo for the active orbit endpoint */}
                                    <circle cx={ex} cy={ey} r="7" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.55">
                                        <animate attributeName="r" values="7;16" dur="2.4s" repeatCount="indefinite" />
                                        <animate attributeName="opacity" values="0.55;0" dur="2.4s" repeatCount="indefinite" />
                                    </circle>
                                    <circle cx={ex} cy={ey} r="4.5" fill="currentColor" stroke="none">
                                        <animate attributeName="opacity" values="1;0.55;1" dur="2.4s" repeatCount="indefinite" />
                                    </circle>
                                    <circle cx={ex} cy={ey} r="7" opacity="0.5" />
                                </>
                            ) : (
                                <circle cx={ex} cy={ey} r="4" opacity="0.65" />
                            )}
                            <text
                                x={lx}
                                y={ly}
                                textAnchor={anchor}
                                fill="currentColor"
                                stroke="none"
                                fontFamily="IBM Plex Mono, ui-monospace, monospace"
                                fontSize="10"
                                opacity={o.filled ? 0.85 : 0.5}
                                style={{ letterSpacing: '0.12em' }}
                            >
                                {labelFor(o, assistantName)}
                            </text>
                        </g>
                    );
                })}

                {/* Center FOUNDER marker — pulsing halo + outer ring + breathing dot */}
                <circle cx="0" cy="0" r="7" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.5">
                    <animate attributeName="r" values="7;15" dur="3.2s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.45;0" dur="3.2s" repeatCount="indefinite" />
                </circle>
                <circle cx="0" cy="0" r="7" />
                <circle cx="0" cy="0" r="1.6" fill="currentColor" stroke="none">
                    <animate attributeName="opacity" values="1;0.6;1" dur="3.2s" repeatCount="indefinite" />
                </circle>
                {/* Tiny cross-hairs through center */}
                <line x1="-12" y1="0" x2="-9" y2="0" opacity="0.4" />
                <line x1="9" y1="0" x2="12" y2="0" opacity="0.4" />
                <line x1="0" y1="-12" x2="0" y2="-9" opacity="0.4" />
                <line x1="0" y1="9" x2="0" y2="12" opacity="0.4" />

                {/* Founder label */}
                <text
                    x="14"
                    y="3"
                    fill="currentColor"
                    stroke="none"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontSize="9.5"
                    opacity="0.7"
                    style={{ letterSpacing: '0.14em' }}
                >
                    FOUNDER
                </text>
            </g>
        </svg>
    );
}
