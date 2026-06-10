interface Props {
    /** Star count — defaults to medium density */
    density?: 'low' | 'medium' | 'high';
    /** Seed for deterministic placement */
    seed?: number;
    className?: string;
}

/** Tiny seedable PRNG so the stars are stable across renders */
function mulberry32(seed: number) {
    let s = seed >>> 0;
    return () => {
        s = (s + 0x6D2B79F5) >>> 0;
        let t = s;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

function makeStars(count: number, seed: number) {
    const rnd = mulberry32(seed);
    return Array.from({ length: count }, () => ({
        cx: Math.round(rnd() * 1440),
        cy: Math.round(rnd() * 900),
        r: +(0.4 + rnd() * 1.0).toFixed(2),
        o: +(0.35 + rnd() * 0.65).toFixed(2),
    }));
}

export default function StarField({ density = 'medium', seed = 42, className }: Props) {
    const count = density === 'high' ? 90 : density === 'low' ? 30 : 55;
    const stars = makeStars(count, seed);
    const cls = ['atlas-starfield', className].filter(Boolean).join(' ');
    return (
        <svg
            className={cls}
            viewBox="0 0 1440 900"
            preserveAspectRatio="xMidYMid slice"
            aria-hidden="true"
        >
            {stars.map((s, i) => (
                <circle key={i} cx={s.cx} cy={s.cy} r={s.r} fill="currentColor" opacity={s.o} />
            ))}
        </svg>
    );
}
