// ═══════════════════════════════════════════════════════════════
// Framer Motion variants + interaction utilities.
// Upgraded for the v2 redesign: tilt, count-up, stagger w/ springs.
// ═══════════════════════════════════════════════════════════════

import { useEffect, useRef, useState } from 'react'
import {
    useMotionValue, useSpring, useTransform, useInView,
} from 'framer-motion'

/* ── custom easings ─────────────────────────────────────────── */
export const easeOutExpo  = [0.22, 1, 0.36, 1]
export const easeInOutCubic = [0.65, 0, 0.35, 1]
export const easeBounceOut = [0.34, 1.56, 0.64, 1]

/* ── viewport triggers ──────────────────────────────────────── */
export const viewport      = { once: true, amount: 0.1 }
export const viewportEager = { once: true, amount: 0.05 }

/* ── entry variants ─────────────────────────────────────────── */
export const fadeInUp = {
    hidden:  { opacity: 0, y: 70, scale: 0.96 },
    visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.75, ease: easeOutExpo } },
}

export const fadeInLeft = {
    hidden:  { opacity: 0, x: -60, scale: 0.97 },
    visible: { opacity: 1, x: 0, scale: 1, transition: { duration: 0.7, ease: easeOutExpo } },
}

export const fadeInRight = {
    hidden:  { opacity: 0, x: 60, scale: 0.97 },
    visible: { opacity: 1, x: 0, scale: 1, transition: { duration: 0.7, ease: easeOutExpo } },
}

export const scaleIn = {
    hidden:  { opacity: 0, scale: 0.85 },
    visible: { opacity: 1, scale: 1, transition: { duration: 0.55, ease: easeOutExpo } },
}

export const blurIn = {
    hidden:  { opacity: 0, filter: 'blur(12px)', y: 20 },
    visible: { opacity: 1, filter: 'blur(0px)',  y: 0, transition: { duration: 0.8, ease: easeOutExpo } },
}

/* ── stagger ────────────────────────────────────────────────── */
export const staggerContainer = (staggerDelay = 0.1, delayChildren = 0.05) => ({
    hidden:  { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: staggerDelay, delayChildren } },
})

export const staggerItem = {
    hidden:  { opacity: 0, y: 50, scale: 0.93 },
    visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.65, ease: easeOutExpo } },
}

export const staggerItemBlur = {
    hidden:  { opacity: 0, filter: 'blur(10px)', y: 30 },
    visible: { opacity: 1, filter: 'blur(0px)',  y: 0, transition: { duration: 0.7, ease: easeOutExpo } },
}

/* ── hover / tap ────────────────────────────────────────────── */
export const cardHover    = { scale: 1.03, y: -4, transition: { type: 'spring', stiffness: 300, damping: 20 } }
export const buttonHover  = { scale: 1.05, transition: { type: 'spring', stiffness: 400, damping: 15 } }
export const buttonTap    = { scale: 0.97 }
export const magneticHover = { scale: 1.08, transition: { type: 'spring', stiffness: 500, damping: 18 } }

/* ── ambient ────────────────────────────────────────────────── */
export const glowPulse = {
    animate: {
        boxShadow: [
            '0 0 20px rgba(59,130,246,0.15)',
            '0 0 40px rgba(59,130,246,0.35)',
            '0 0 20px rgba(59,130,246,0.15)',
        ],
    },
    transition: { duration: 3, repeat: Infinity, ease: 'easeInOut' },
}

export const breathe = {
    animate: { scale: [1, 1.02, 1] },
    transition: { duration: 4, repeat: Infinity, ease: 'easeInOut' },
}

export const headerReveal = {
    hidden:  { opacity: 0, y: 35 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: easeOutExpo } },
}

/* ═══════════════════════════════════════════════════════════════
   HOOKS
═══════════════════════════════════════════════════════════════ */

/* ── 3D cursor-follow tilt ──────────────────────────────────── */
export function useTilt(maxTilt = 12) {
    const ref = useRef(null)
    const x = useMotionValue(0)
    const y = useMotionValue(0)
    const springConfig = { stiffness: 150, damping: 15 }
    const rotateX = useSpring(useTransform(y, [-0.5, 0.5], [maxTilt, -maxTilt]), springConfig)
    const rotateY = useSpring(useTransform(x, [-0.5, 0.5], [-maxTilt, maxTilt]), springConfig)

    const onMouseMove = (e) => {
        if (!ref.current) return
        const rect = ref.current.getBoundingClientRect()
        const px = (e.clientX - rect.left) / rect.width  - 0.5
        const py = (e.clientY - rect.top)  / rect.height - 0.5
        x.set(px)
        y.set(py)
    }
    const onMouseLeave = () => { x.set(0); y.set(0) }

    return { ref, rotateX, rotateY, onMouseMove, onMouseLeave }
}

/* ── Number count-up triggered on scroll into view ──────────── */
export function useCountUp(target, { duration = 1800, trigger = true } = {}) {
    const [value, setValue] = useState(0)
    const ref = useRef(null)
    const inView = useInView(ref, { once: true, amount: 0.3 })
    useEffect(() => {
        if (!inView || !trigger) return
        let raf
        const start = performance.now()
        const tick = (t) => {
            const progress = Math.min(1, (t - start) / duration)
            // easeOutCubic
            const eased = 1 - Math.pow(1 - progress, 3)
            setValue(target * eased)
            if (progress < 1) raf = requestAnimationFrame(tick)
        }
        raf = requestAnimationFrame(tick)
        return () => cancelAnimationFrame(raf)
    }, [inView, trigger, target, duration])
    return [value, ref]
}

/* ── Mouse position hook (for cursor follower / parallax) ───── */
export function useMousePosition() {
    const [pos, setPos] = useState({ x: -100, y: -100 })
    useEffect(() => {
        const handler = (e) => setPos({ x: e.clientX, y: e.clientY })
        window.addEventListener('mousemove', handler)
        return () => window.removeEventListener('mousemove', handler)
    }, [])
    return pos
}

/* ── Smooth scroll-progress spring ──────────────────────────── */
export const scrollSpringConfig = { stiffness: 120, damping: 30, restDelta: 0.001 }

/* ── prefers-reduced-motion respect ─────────────────────────── */
export function usePrefersReducedMotion() {
    const [reduced, setReduced] = useState(false)
    useEffect(() => {
        const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
        setReduced(mq.matches)
        const handler = (e) => setReduced(e.matches)
        mq.addEventListener('change', handler)
        return () => mq.removeEventListener('change', handler)
    }, [])
    return reduced
}
