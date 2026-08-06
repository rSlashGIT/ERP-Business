import { motion } from 'framer-motion'

/**
 * Infinite horizontal marquee strip used as a section break.
 * Pure CSS animation via motion.div for GPU-friendly loops.
 */
export default function MarqueeStrip({
    items = [],
    direction = 'left',     // 'left' | 'right'
    speed = 40,             // seconds per full loop
    tilt = 0,               // degrees
    className = '',
    accent = '#3b82f6',
}) {
    const doubled = [...items, ...items, ...items] // triple for seamless loop
    const from = direction === 'left' ? 0   : '-33.333%'
    const to   = direction === 'left' ? '-33.333%' : 0

    return (
        <div
            className={`relative w-full overflow-hidden py-6 ${className}`}
            style={{
                transform: `rotate(${tilt}deg)`,
                maskImage: 'linear-gradient(90deg, transparent, black 15%, black 85%, transparent)',
                WebkitMaskImage: 'linear-gradient(90deg, transparent, black 15%, black 85%, transparent)',
            }}
        >
            <motion.div
                animate={{ x: [from, to] }}
                transition={{ duration: speed, repeat: Infinity, ease: 'linear' }}
                className="flex gap-8 items-center whitespace-nowrap"
                style={{ width: 'max-content' }}
            >
                {doubled.map((it, i) => (
                    <div
                        key={i}
                        className="flex items-center gap-3 px-6 py-3 rounded-full shrink-0"
                        style={{
                            background: 'rgba(14,20,45,0.4)',
                            border: `1px solid ${accent}22`,
                            backdropFilter: 'blur(8px)',
                        }}
                    >
                        <span className="text-2xl">{it.icon}</span>
                        <span className="text-sm font-mono uppercase tracking-widest text-slate-300">
                            {it.label}
                        </span>
                    </div>
                ))}
            </motion.div>
        </div>
    )
}
