import { useEffect, useState } from 'react';
import Particles, { initParticlesEngine } from '@tsparticles/react';
import { loadSlim } from '@tsparticles/slim';

export default function CosmicBackground() {
    const [ready, setReady] = useState(false);

    useEffect(() => {
        initParticlesEngine(async (engine) => {
            await loadSlim(engine);
        }).then(() => setReady(true));
    }, []);

    if (!ready) return null;

    return (
        <Particles
            id="cosmic-particles"
            className="cosmic-particles"
            options={{
                fullScreen: { enable: false },
                background: { color: { value: 'transparent' } },
                fpsLimit: 60,
                detectRetina: true,
                particles: {
                    number: {
                        value: 140,
                        density: { enable: true, width: 1600, height: 1000 },
                    },
                    color: { value: '#ffffff' },
                    shape: { type: 'circle' },
                    opacity: {
                        value: { min: 0.12, max: 0.75 },
                        animation: {
                            enable: true,
                            speed: 0.4,
                            sync: false,
                            startValue: 'random',
                        },
                    },
                    size: {
                        value: { min: 0.4, max: 1.8 },
                    },
                    move: {
                        enable: true,
                        speed: 0.12,
                        direction: 'none',
                        random: true,
                        straight: false,
                        outModes: { default: 'out' },
                    },
                    twinkle: {
                        particles: {
                            enable: true,
                            frequency: 0.025,
                            opacity: 1,
                        },
                    },
                },
            }}
        />
    );
}
