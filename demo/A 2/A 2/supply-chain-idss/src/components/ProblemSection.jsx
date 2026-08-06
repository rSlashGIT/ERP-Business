import { useState, useRef } from 'react'
import { motion, useScroll, useTransform } from 'framer-motion'
import { problemCards } from '../data/projectData'
import { headerReveal, viewport, easeOutExpo } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'

function FlipCard({ card, index, progress }) {
    const [flipped, setFlipped] = useState(false)

    const targetX = (index % 2) * 320 - 160
    const targetY = Math.floor(index / 2) * 360 - 180
    const initialRot = (index - 1.5) * 5
    const targetRot = (index - 1.5) * 1.5

    const x = useTransform(progress, [0, 1], [0, targetX])
    const y = useTransform(progress, [0, 1], [0, targetY])
    const rotate = useTransform(progress, [0, 1], [initialRot, targetRot])
    const scale = useTransform(progress, [0, 1], [0.82, 1])

    return (
        <motion.div
            style={{
                x, y, rotate, scale,
                position: 'absolute',
                width: 260,
                height: 320,
                zIndex: index,
                cursor: 'pointer',
            }}
            whileHover={{ scale: 1.06, zIndex: 10 }}
            onClick={() => setFlipped(f => !f)}
        >
            <motion.div
                animate={{ rotateY: flipped ? 180 : 0 }}
                transition={{ duration: 0.7, ease: easeOutExpo }}
                style={{
                    width: '100%', height: '100%',
                    position: 'relative', transformStyle: 'preserve-3d',
                }}
            >
                {/* FRONT */}
                <div style={{
                    position: 'absolute', inset: 0,
                    backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden',
                    borderRadius: 20, padding: 24,
                    background: 'rgba(14,20,45,0.88)',
                    border: `1px solid ${card.color}50`,
                    backdropFilter: 'blur(20px)',
                    boxShadow: `0 20px 60px rgba(0,0,0,0.5), 0 0 40px ${card.color}20`,
                    display: 'flex', flexDirection: 'column',
                }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 1, background: `linear-gradient(90deg, transparent, ${card.color}cc, transparent)` }} />
                    <div className="text-4xl mb-4">{card.icon}</div>
                    <div className="text-[10px] font-mono uppercase tracking-widest mb-2" style={{ color: card.color }}>
                        Problem · {String(card.id).padStart(2, '0')}
                    </div>
                    <h3 className="text-xl font-black text-white mb-3" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        {card.title}
                    </h3>
                    <p className="text-sm text-slate-400 leading-relaxed flex-1">{card.front}</p>
                    <div className="text-[10px] text-slate-600 font-mono uppercase tracking-widest mt-3">Tap to see fix ↻</div>
                </div>

                {/* BACK */}
                <div style={{
                    position: 'absolute', inset: 0,
                    backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden',
                    transform: 'rotateY(180deg)',
                    borderRadius: 20, padding: 24,
                    background: `linear-gradient(135deg, rgba(14,20,45,0.95), ${card.color}18)`,
                    border: `1px solid ${card.color}80`,
                    backdropFilter: 'blur(24px)',
                    boxShadow: `0 20px 60px rgba(0,0,0,0.5), 0 0 50px ${card.color}40`,
                    display: 'flex', flexDirection: 'column',
                }}>
                    <div className="text-[10px] font-mono uppercase tracking-widest mb-2 text-emerald-400">How HRL solves it</div>
                    <h3 className="text-lg font-black text-white mb-3" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>{card.title}</h3>
                    <p className="text-sm text-slate-300 leading-relaxed flex-1">{card.back}</p>
                    <div className="text-[10px] text-slate-600 font-mono uppercase tracking-widest mt-3">Tap to flip back ↺</div>
                </div>
            </motion.div>
        </motion.div>
    )
}

export default function ProblemSection() {
    const ref = useRef(null)
    const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'end start'] })
    const fanProgress = useTransform(scrollYProgress, [0.05, 0.4], [0, 1])

    return (
        <section id="problem" ref={ref} className="py-32 px-6 relative overflow-hidden"
            style={{ background: 'linear-gradient(180deg, #060912 0%, #080d1a 100%)' }}>
            <div className="grid-bg-fine absolute inset-0 opacity-30 pointer-events-none" />
            <div className="max-w-7xl mx-auto relative z-10">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-16">
                    <SectionLabel color="#ef4444">Why this problem is hard</SectionLabel>
                    <h2 className="text-4xl md:text-6xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        Four things that break{' '}
                        <span className="gradient-text-warm">every classical model.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        Click any card to flip it. Each front is a real failure mode — each back shows exactly how our hierarchical agent handles it.
                    </p>
                </motion.div>

                {/* Fan-out area */}
                <div className="relative mx-auto flex items-center justify-center" style={{ height: 850 }}>
                    <div className="relative" style={{ width: 260, height: 320 }}>
                        {problemCards.map((card, i) => (
                            <FlipCard key={card.id} card={card} index={i} progress={fanProgress} />
                        ))}
                    </div>
                </div>

                <motion.p
                    initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }}
                    viewport={viewport} transition={{ duration: 0.8, ease: easeOutExpo }}
                    className="text-center text-slate-500 text-sm max-w-xl mx-auto mt-4"
                >
                    Traditional RL solves <em>one</em> of these. Hierarchical RL solves all four — by splitting the brain.
                </motion.p>
            </div>
        </section>
    )
}
