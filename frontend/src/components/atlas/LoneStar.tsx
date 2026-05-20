interface Props {
    className?: string;
}

/**
 * Full-bleed background plate for Screen 2.
 * Renders a faint stereographic celestial grid + the lone cross-mark star
 * positioned at ~40% from the top. Grid sits at 8% opacity of currentColor.
 */
export default function LoneStar({ className }: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    // Star position — horizontal center, 40% from top of viewBox
    const starX = 720;
    const starY = 360;
    const starSize = 16;
    // Grid center sits slightly below the star to suggest a horizon
    const gridCx = 720;
    const gridCy = 480;
    const sphereR = 360;

    // Parallels (concentric circles)
    const parallels = [60, 130, 200, 270, sphereR];

    // Meridian "longitudes" — ellipses with shrinking ry, simulating sphere projection
    const meridianRYs = [40, 110, 180, 250, 320, sphereR];

    return (
        <svg
            className={cls}
            viewBox="0 0 1440 900"
            preserveAspectRatio="xMidYMid slice"
            width="100%"
            height="100%"
            fill="none"
            stroke="currentColor"
            strokeWidth="0.5"
            aria-hidden="true"
        >
            {/* Faint celestial grid */}
            <g opacity="0.08">
                <circle cx={gridCx} cy={gridCy} r={sphereR} />
                {parallels.map((r, i) => (
                    <circle key={`p-${i}`} cx={gridCx} cy={gridCy} r={r} />
                ))}
                {meridianRYs.map((ry, i) => (
                    <ellipse key={`m-${i}`} cx={gridCx} cy={gridCy} rx={sphereR} ry={ry} />
                ))}
            </g>

            {/* 4 background dot-stars, intentionally placed */}
            <g fill="currentColor" stroke="none" opacity="0.5">
                <circle cx="320" cy="180" r="1" />
                <circle cx="1100" cy="240" r="1" />
                <circle cx="480" cy="720" r="1" />
                <circle cx="1180" cy="680" r="1" />
            </g>

            {/* The lone cross-mark star */}
            <g>
                <line x1={starX - starSize / 2} y1={starY} x2={starX + starSize / 2} y2={starY} />
                <line x1={starX} y1={starY - starSize / 2} x2={starX} y2={starY + starSize / 2} />
            </g>
        </svg>
    );
}
