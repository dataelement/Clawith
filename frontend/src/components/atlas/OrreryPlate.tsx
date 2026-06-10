interface Props {
    size?: number;
    className?: string;
}

// Point on an ellipse (rx, ry) at parametric angle θ (radians)
function ellipsePoint(rx: number, ry: number, theta: number): [number, number] {
    return [rx * Math.cos(theta), ry * Math.sin(theta)];
}

// Outward-pointing unit vector from origin to (x, y)
function unit(x: number, y: number): [number, number] {
    const len = Math.sqrt(x * x + y * y);
    return [x / len, y / len];
}

export default function OrreryPlate({ size = 480, className }: Props) {
    const cls = ['atlas-illustration', className].filter(Boolean).join(' ');
    const orbits: Array<{ rx: number; ry: number; rot: number; dotTheta: number }> = [
        { rx: 80, ry: 55, rot: 5, dotTheta: 45 },
        { rx: 140, ry: 95, rot: 12, dotTheta: 120 },
        { rx: 200, ry: 135, rot: -8, dotTheta: -60 },
        { rx: 265, ry: 180, rot: 20, dotTheta: 200 },
    ];
    const outer = orbits[orbits.length - 1];

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
                {/* Central body */}
                <circle cx="0" cy="0" r="12" />
                <line x1="-8" y1="0" x2="8" y2="0" />
                <line x1="0" y1="-8" x2="0" y2="8" />

                {/* Concentric orbits, each with its dot */}
                {orbits.map((o, i) => {
                    const [px, py] = ellipsePoint(o.rx, o.ry, (o.dotTheta * Math.PI) / 180);
                    return (
                        <g key={i} transform={`rotate(${o.rot})`}>
                            <ellipse cx="0" cy="0" rx={o.rx} ry={o.ry} />
                            <circle cx={px} cy={py} r="3" fill="currentColor" stroke="none" />
                        </g>
                    );
                })}

                {/* Outer-ellipse tick marks every 30° */}
                <g transform={`rotate(${outer.rot})`}>
                    {Array.from({ length: 12 }, (_, i) => {
                        const theta = (i * 30 * Math.PI) / 180;
                        const [x, y] = ellipsePoint(outer.rx, outer.ry, theta);
                        const [ux, uy] = unit(x, y);
                        const tickLen = 6;
                        return (
                            <line
                                key={i}
                                x1={x}
                                y1={y}
                                x2={x + ux * tickLen}
                                y2={y + uy * tickLen}
                            />
                        );
                    })}
                </g>
            </g>
        </svg>
    );
}
