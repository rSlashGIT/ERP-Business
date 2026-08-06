import { useState, useEffect, lazy, Suspense } from 'react'
import { motion, AnimatePresence, useScroll, useTransform } from 'framer-motion'
import { strategicModes } from '../data/projectData'
import { staggerContainer, staggerItem, buttonHover, buttonTap, useCountUp, easeOutExpo } from '../utils/animations'

const DragGlobe = lazy(() => import('./DragGlobe'))

const ROTATING_TAGLINES = [
    'Two DQNs. One supply chain. Zero stockouts.',
    "A baseline DQN loses money every episode. Ours doesn't.",
    '1,941 rows of Walmart data. 1,000 episodes. Grade B+.',
    'Strategy every 30 days. Tactics every day. Learning every second.',
]

function AnimatedStat({ value, suffix = '', prefix = '', label, color, decimals = 0 }) {
    const [count, ref] = useCountUp(value, { duration: 2000 })
    const display = decimals > 0 ? count.toFixed(decimals) : Math.floor(count).toLocaleString()
    return (
        <div ref={ref}>
            <div
                className="text-3xl md:text-4xl lg:text-5xl font-black"
                style={{
                    color,
                    fontFamily: 'Syne, Inter, sans-serif',
                    textShadow: `0 0 40px ${color}40`,
                }}
            >
                {prefix}{display}{suffix}
            </div>
            <div className="text-xs uppercase tracking-widest text-slate-500 mt-1">{label}</div>
        </div>
    )
}

export default function Hero() {
    const [tagIdx, setTagIdx] = useState(0)
    const [activeMode, setActiveMode] = useState(1)
    const { scrollYProgress } = useScroll()
    const heroOpacity = useTransform(scrollYProgress, [0, 0.15], [1, 0])
    const heroY = useTransform(scrollYProgress, [0, 0.2], [0, -80])

    useEffect(() => {
        const t = setInterval(() => setTagIdx(i => (i + 1) % ROTATING_TAGLINES.length), 3500)
        return () => clearInterval(t)
    }, [])

    useEffect(() => {
        const t = setInterval(() => setActiveMode(m => (m + 1) % 3), 4500)
        return () => clearInterval(t)
    }, [])

    return (
        <section id="overview" className="relative min-h-screen flex items-center overflow-hidden pt-24 pb-16 gradient-bg">
            <div className="grid-bg-fine absolute inset-0 opacity-40 pointer-events-none" />

            {/* Ambient glow blobs */}
            <motion.div
                className="absolute w-[600px] h-[600px] rounded-full pointer-events-none"
                style={{
                    top: '5%', left: '-10%',
                    background: 'radial-gradient(circle, rgba(59,130,246,0.18), transparent 65%)',
                    filter: 'blur(80px)',
                }}
                animate={{ scale: [1, 1.1, 1], opacity: [0.6, 0.9, 0.6] }}
                transition={{ duration: 9, repeat: Infinity, ease: 'easeInOut' }}
            />
            <motion.div
                className="absolute w-[500px] h-[500px] rounded-full pointer-events-none"
                style={{
                    bottom: '5%', right: '-8%',
                    background: 'radial-gradient(circle, rgba(139,92,246,0.15), transparent 65%)',
                    filter: 'blur(80px)',
                }}
                animate={{ scale: [1.1, 1, 1.1], opacity: [0.5, 0.8, 0.5] }}
                transition={{ duration: 11, repeat: Infinity, ease: 'easeInOut' }}
            />

            <motion.div style={{ opacity: heroOpacity, y: heroY }} className="relative z-10 w-full max-w-7xl mx-auto px-6">
                {/* Live badge */}
                <motion.div
                    initial={{ opacity: 0, y: -20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.6, ease: easeOutExpo }}
                    className="flex justify-center mb-8"
                >
                    <div
                        className="inline-flex items-center gap-3 px-5 py-2 rounded-full"
                        style={{
                            background: 'rgba(52,211,153,0.08)',
                            border: '1px solid rgba(52,211,153,0.28)',
                            backdropFilter: 'blur(12px)',
                        }}
                    >
                        <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
                        </span>
                        <span className="text-xs font-mono uppercase tracking-widest text-emerald-300">
                            Live decision system · Grade B+ · 78.7/100
                        </span>
                    </div>
                </motion.div>

                <div className="grid lg:grid-cols-2 gap-12 items-center">
                    {/* Left: globe */}
                    <motion.div
                        initial={{ opacity: 0, scale: 0.85 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 1.2, ease: easeOutExpo }}
                        className="relative order-2 lg:order-1"
                    >
                        <Suspense
                            fallback={
                                <div
                                    className="w-full aspect-square max-w-[560px] mx-auto rounded-full"
                                    style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.1), transparent 70%)' }}
                                />
                            }
                        >
                            <DragGlobe mode={activeMode} size={560} />
                        </Suspense>

                        {/* Floating mode pill */}
                        <div className="absolute top-4 left-4 md:left-8">
                            <AnimatePresence mode="wait">
                                <motion.div
                                    key={activeMode}
                                    initial={{ opacity: 0, y: -10, filter: 'blur(6px)' }}
                                    animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                                    exit={{ opacity: 0, y: 10, filter: 'blur(6px)' }}
                                    transition={{ duration: 0.4 }}
                                    className="px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-2"
                                    style={{
                                        background: 'rgba(14,20,45,0.8)',
                                        border: `1px solid ${strategicModes[activeMode].color}55`,
                                        backdropFilter: 'blur(12px)',
                                        color: strategicModes[activeMode].color,
                                    }}
                                >
                                    <span>{strategicModes[activeMode].icon}</span>
                                    <span className="font-mono">
                                        MODE {activeMode} · {strategicModes[activeMode].name.toUpperCase()}
                                    </span>
                                </motion.div>
                            </AnimatePresence>
                        </div>

                        {/* Drag hint */}
                        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 text-[10px] text-slate-500 font-mono uppercase tracking-widest pointer-events-none">
                            ← drag to rotate →
                        </div>
                    </motion.div>

                    {/* Right: content */}
                    <div className="order-1 lg:order-2">
                        <motion.h1
                            initial={{ opacity: 0, y: 40 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: 0.9, delay: 0.2, ease: easeOutExpo }}
                            className="text-4xl md:text-5xl lg:text-6xl font-black leading-[0.95] mb-6 text-white"
                            style={{ fontFamily: 'Syne, Inter, sans-serif' }}
                        >
                            The supply chain<br />
                            <span className="gradient-text">that thinks twice.</span>
                        </motion.h1>

                        <AnimatePresence mode="wait">
                            <motion.p
                                key={tagIdx}
                                initial={{ opacity: 0, y: 10, filter: 'blur(6px)' }}
                                animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                                exit={{ opacity: 0, y: -10, filter: 'blur(6px)' }}
                                transition={{ duration: 0.5 }}
                                className="text-lg md:text-xl text-slate-400 mb-10 min-h-[3.5rem] max-w-xl"
                            >
                                {ROTATING_TAGLINES[tagIdx]}
                            </motion.p>
                        </AnimatePresence>

                        <motion.div
                            variants={staggerContainer(0.15, 0.6)}
                            initial="hidden"
                            animate="visible"
                            className="flex flex-wrap gap-4 mb-12"
                        >
                            <motion.a
                                variants={staggerItem}
                                whileHover={buttonHover}
                                whileTap={buttonTap}
                                href="#demo"
                                className="px-7 py-3.5 rounded-xl font-bold text-white"
                                style={{
                                    background: 'linear-gradient(135deg, #2563eb, #7c3aed)',
                                    boxShadow: '0 0 30px rgba(59,130,246,0.35), 0 4px 20px rgba(0,0,0,0.5)',
                                }}
                            >
                                Try the demo →
                            </motion.a>
                            <motion.a
                                variants={staggerItem}
                                whileHover={buttonHover}
                                whileTap={buttonTap}
                                href="#architecture"
                                className="px-7 py-3.5 rounded-xl font-bold"
                                style={{
                                    background: 'rgba(255,255,255,0.03)',
                                    border: '1px solid rgba(255,255,255,0.12)',
                                    color: '#e2e8f0',
                                    backdropFilter: 'blur(12px)',
                                }}
                            >
                                How it works
                            </motion.a>
                        </motion.div>

                        <motion.div
                            variants={staggerContainer(0.12, 0.9)}
                            initial="hidden"
                            animate="visible"
                            className="flex flex-wrap gap-x-12 gap-y-6 lg:justify-between"
                        >
                            <motion.div variants={staggerItem}>
                                <AnimatedStat value={78.7} suffix="/100" label="Accuracy" color="#60a5fa" decimals={1} />
                            </motion.div>
                            <motion.div variants={staggerItem}>
                                <AnimatedStat value={11105} prefix="+" label="Best reward" color="#34d399" />
                            </motion.div>
                            <motion.div variants={staggerItem}>
                                <AnimatedStat value={63} suffix="%" label="Service level" color="#a78bfa" />
                            </motion.div>
                            <motion.div variants={staggerItem}>
                                <AnimatedStat value={521} label="Mode shifts" color="#f59e0b" />
                            </motion.div>
                        </motion.div>
                    </div>
                </div>
            </motion.div>

            {/* Scroll indicator */}
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 2 }}
                className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 text-slate-500"
            >
                <span className="text-[10px] uppercase tracking-widest">Scroll</span>
                <motion.div
                    animate={{ y: [0, 8, 0] }}
                    transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
                    className="w-px h-8"
                    style={{ background: 'linear-gradient(180deg, #64748b, transparent)' }}
                />
            </motion.div>
        </section>
    )
}
