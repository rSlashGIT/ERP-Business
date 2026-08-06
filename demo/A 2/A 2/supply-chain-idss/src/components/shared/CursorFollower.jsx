import { useEffect, useState } from 'react'
import { motion, useMotionValue, useSpring } from 'framer-motion'

/**
 * Soft glowing blob that follows the cursor with spring delay.
 * Disabled on touch devices (no cursor) and when prefers-reduced-motion.
 */
export default function CursorFollower() {
    const [enabled, setEnabled] = useState(true)
    const mx = useMotionValue(-200)
    const my = useMotionValue(-200)
    const sx = useSpring(mx, { stiffness: 150, damping: 20, mass: 0.6 })
    const sy = useSpring(my, { stiffness: 150, damping: 20, mass: 0.6 })

    useEffect(() => {
        const isTouch = window.matchMedia('(hover: none)').matches
        const reduce  = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        if (isTouch || reduce) { setEnabled(false); return }
        const handler = (e) => { mx.set(e.clientX - 100); my.set(e.clientY - 100) }
        window.addEventListener('mousemove', handler)
        return () => window.removeEventListener('mousemove', handler)
    }, [mx, my])

    if (!enabled) return null

    return (
        <motion.div
            aria-hidden
            style={{ x: sx, y: sy }}
            className="pointer-events-none fixed top-0 left-0 z-[9998]"
        >
            <div
                style={{
                    width: 200, height: 200, borderRadius: '50%',
                    background: 'radial-gradient(circle, rgba(96,165,250,0.18) 0%, rgba(139,92,246,0.08) 40%, transparent 70%)',
                    filter: 'blur(20px)',
                }}
            />
        </motion.div>
    )
}
