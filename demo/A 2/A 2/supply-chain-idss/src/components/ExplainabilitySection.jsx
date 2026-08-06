import { useRef, useState, useEffect } from 'react'
import { motion, useMotionValue, useSpring } from 'framer-motion'
import { featureImportance } from '../data/projectData'
import { headerReveal, viewport, easeOutExpo } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

/** Physics-draggable importance ball */
function ImportanceBall({ feat, containerW, containerH }) {
    const radius = 30 + feat.importance * 120   // 30px → 150px based on weight
    const x = useMotionValue(positionFor(feat.feature, containerW - radius * 2, radius, 17))
    const y = useMotionValue(positionFor(feat.feature, containerH - radius * 2, radius, 31))
    const sx = useSpring(x, { stiffness: 120, damping: 18 })
    const sy = useSpring(y, { stiffness: 120, damping: 18 })
    const [dragging, setDragging] = useState(false)

    return (
        <motion.div
            drag
            dragMomentum
            dragElastic={0.15}
            dragConstraints={{ left: 0, right: containerW - radius * 2, top: 0, bottom: containerH - radius * 2 }}
            style={{
                position: 'absolute',
                width: radius * 2,
                height: radius * 2,
                borderRadius: '50%',
                x: sx, y: sy,
                cursor: dragging ? 'grabbing' : 'grab',
                zIndex: dragging ? 10 : 1,
            }}
            onDragStart={() => setDragging(true)}
            onDragEnd={() => setDragging(false)}
            whileHover={{ scale: 1.08 }}
            whileDrag={{ scale: 1.12 }}
        >
            <div
                style={{
                    width: '100%', height: '100%', borderRadius: '50%',
                    background: `radial-gradient(circle at 35% 35%, ${feat.color}cc, ${feat.color}55)`,
                    border: `2px solid ${feat.color}80`,
                    boxShadow: `0 0 ${radius * 0.6}px ${feat.color}40, inset 0 0 ${radius * 0.3}px ${feat.color}20`,
                    display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                    textAlign: 'center', padding: 8,
                    backdropFilter: 'blur(8px)',
                    userSelect: 'none',
                }}
            >
                <div className="font-black text-white" style={{ fontSize: Math.max(10, radius * 0.28) }}>
                    {(feat.importance * 100).toFixed(1)}%
                </div>
                <div className="text-white/70 leading-tight" style={{ fontSize: Math.max(8, radius * 0.18) }}>
                    {feat.feature}
                </div>
            </div>
        </motion.div>
    )
}

function positionFor(key, span, offset, salt) {
    const code = Array.from(key).reduce((sum, ch, i) => sum + ch.charCodeAt(0) * (i + salt), 0)
    const value = Math.sin(code * 12.9898 + salt * 78.233) * 43758.5453
    return offset + (value - Math.floor(value)) * Math.max(0, span)
}

export default function ExplainabilitySection() {
    const containerRef = useRef(null)
    const [size, setSize] = useState({ w: 700, h: 420 })

    useEffect(() => {
        const measure = () => {
            if (containerRef.current) {
                setSize({ w: containerRef.current.offsetWidth, h: containerRef.current.offsetHeight })
            }
        }
        measure()
        window.addEventListener('resize', measure)
        return () => window.removeEventListener('resize', measure)
    }, [])

    return (
        <section id="explain" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #060912 0%, #080d1a 100%)' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-24"
                style={{ background: 'linear-gradient(180deg, transparent, rgba(34,211,238,0.4), transparent)' }} />

            <div className="max-w-6xl mx-auto">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-16">
                    <SectionLabel color="#22d3ee">Explainability</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        Every decision traced<br />
                        <span className="gradient-text">back to its inputs.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        Bigger ball = higher feature importance (SHAP attribution). Drag them around — heavier balls represent what the agent cares about most.
                    </p>
                </motion.div>

                {/* Physics canvas */}
                <motion.div
                    initial={{ opacity: 0, y: 30 }} whileInView={{ opacity: 1, y: 0 }}
                    viewport={viewport} transition={{ duration: 0.8, ease: easeOutExpo }}
                >
                    <GlassCard depth={1} className="relative overflow-hidden" style={{ height: 440 }}>
                        <div className="absolute top-3 left-4 text-xs font-mono text-slate-600 uppercase tracking-widest">
                            Drag the balls · Size = importance
                        </div>
                        <div ref={containerRef} className="absolute inset-0">
                            {size.w > 0 && featureImportance.map(feat => (
                                <ImportanceBall key={feat.feature} feat={feat} containerW={size.w} containerH={size.h} />
                            ))}
                        </div>
                    </GlassCard>
                </motion.div>

                {/* Decision rules */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }}
                    viewport={viewport} transition={{ duration: 0.8, delay: 0.2, ease: easeOutExpo }}
                    className="mt-8 grid md:grid-cols-2 gap-6"
                >
                    <GlassCard depth={1} accent="#60a5fa" className="p-6">
                        <h3 className="text-lg font-bold text-white mb-4">Decision Rules</h3>
                        <div className="space-y-3">
                            {[
                                { cond: 'Inventory < 10% demand', act: 'Bulk order 1,500 units', color: '#ef4444', icon: '🚨' },
                                { cond: '10–30% coverage',        act: 'Large+ order 750 units',  color: '#f97316', icon: '⚠️' },
                                { cond: '30–60% coverage',        act: 'Medium order 300 units',  color: '#f59e0b', icon: '📦' },
                                { cond: '60–100% coverage',       act: 'Small order 150 units',   color: '#60a5fa', icon: '✅' },
                                { cond: 'Inventory > 150% demand', act: 'No order — hold cost',  color: '#34d399', icon: '💤' },
                            ].map(r => (
                                <div key={r.cond} className="flex items-start gap-3 p-3 rounded-xl"
                                    style={{ background: `${r.color}0d`, border: `1px solid ${r.color}30` }}>
                                    <span className="text-lg shrink-0">{r.icon}</span>
                                    <div>
                                        <div className="text-xs text-slate-400">{r.cond}</div>
                                        <div className="text-sm font-semibold" style={{ color: r.color }}>→ {r.act}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </GlassCard>

                    <GlassCard depth={1} accent="#34d399" className="p-6">
                        <h3 className="text-lg font-bold text-white mb-4">Key Findings</h3>
                        <div className="space-y-3 text-sm text-slate-400">
                            {[
                                { icon: '📊', text: 'Inventory Level drives 28.5% of all decisions — the single biggest signal.' },
                                { icon: '📈', text: 'Demand uncertainty (Q10/Q50/Q90 spread) matters more than the point estimate.' },
                                { icon: '💰', text: 'Cost forecast at Q90 (worst case) heavily weights large-order decisions.' },
                                { icon: '🤝', text: 'Human oversight still recommended for exceptions — the agent saw 1,941 rows, not every scenario.' },
                            ].map((f, i) => (
                                <div key={i} className="flex gap-3 p-3 rounded-xl" style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.07)' }}>
                                    <span className="text-xl shrink-0">{f.icon}</span>
                                    <p className="leading-relaxed">{f.text}</p>
                                </div>
                            ))}
                        </div>
                    </GlassCard>
                </motion.div>
            </div>
        </section>
    )
}
