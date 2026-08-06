import { useRef, useState } from 'react'
import { motion, useMotionValue, animate } from 'framer-motion'
import { techStack, pipelineStages } from '../data/projectData'
import { headerReveal, viewport, easeOutExpo, staggerContainer, staggerItem } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

function DragCarousel({ items, renderItem }) {
    const x = useMotionValue(0)
    const [current, setCurrent] = useState(0)
    const cardW = 220
    const gap = 14

    const snapTo = (idx) => {
        const clamped = Math.max(0, Math.min(idx, items.length - 1))
        setCurrent(clamped)
        animate(x, -(clamped * (cardW + gap)), { type: 'spring', stiffness: 300, damping: 30 })
    }

    return (
        <div className="overflow-hidden">
            <motion.div
                drag="x"
                style={{ x, width: 'max-content', gap }}
                dragConstraints={{ left: -((items.length - 1) * (cardW + gap)), right: 0 }}
                dragElastic={0.15}
                onDragEnd={(_, info) => {
                    if (info.velocity.x < -300) snapTo(current + 1)
                    else if (info.velocity.x > 300) snapTo(current - 1)
                    else snapTo(Math.round(-x.get() / (cardW + gap)))
                }}
                className="flex cursor-grab active:cursor-grabbing select-none"
            >
                {items.map((item, i) => (
                    <motion.div key={i}
                        animate={{ scale: current === i ? 1 : 0.93, opacity: current === i ? 1 : 0.55 }}
                        style={{ width: cardW, flexShrink: 0 }}>
                        {renderItem(item, i, current === i)}
                    </motion.div>
                ))}
            </motion.div>
            <div className="flex gap-2 justify-center mt-5">
                {items.map((_, i) => (
                    <button key={i} onClick={() => snapTo(i)}
                        className="rounded-full transition-all"
                        style={{ width: current === i ? 20 : 8, height: 8, background: current === i ? '#a78bfa' : 'rgba(255,255,255,0.2)' }} />
                ))}
            </div>
        </div>
    )
}

export default function TechStackSection() {
    return (
        <section id="tech" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #080d1a 0%, #060912 100%)' }}>
            <div className="max-w-7xl mx-auto">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-20">
                    <SectionLabel color="#a78bfa">Built With</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        Every layer<br />
                        <span className="gradient-text">intentionally chosen.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        Python, PyTorch, 1,941 rows of real Walmart M5 data. No shortcuts.
                    </p>
                </motion.div>

                {/* Tech grid */}
                <motion.div variants={staggerContainer(0.07)} initial="hidden" whileInView="visible" viewport={viewport}
                    className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-20">
                    {techStack.map(tech => (
                        <motion.div key={tech.name} variants={staggerItem}
                            whileHover={{ y: -8, scale: 1.04 }} className="group">
                            <GlassCard depth={1} className="p-6 text-center h-full transition-all group-hover:border-purple-500/30">
                                <motion.div
                                    className="text-4xl mb-3"
                                    whileHover={{ rotate: [0, -10, 10, 0], transition: { duration: 0.4 } }}
                                >
                                    {tech.icon}
                                </motion.div>
                                <div className="font-bold text-white">{tech.name}</div>
                                <div className="text-xs text-purple-400 font-mono mt-1">{tech.category}</div>
                                <div className="text-xs text-slate-500 mt-2">{tech.description}</div>
                            </GlassCard>
                        </motion.div>
                    ))}
                </motion.div>

                {/* Pipeline carousel */}
                <div className="mb-6">
                    <h3 className="text-xl font-bold text-white mb-1">End-to-End Pipeline</h3>
                    <p className="text-slate-400 text-sm mb-6">Drag to step through the full system flow.</p>
                    <DragCarousel items={pipelineStages} renderItem={(stage, i, active) => (
                        <GlassCard depth={active ? 2 : 1} accent={active ? stage.color : undefined} className="p-6" style={{ minHeight: 200 }}>
                            <div className="text-[10px] font-mono uppercase tracking-widest mb-3" style={{ color: stage.color }}>
                                Step {stage.id} of {pipelineStages.length}
                            </div>
                            <div className="text-3xl mb-3">{stage.icon}</div>
                            <div className="font-bold text-white mb-2">{stage.name}</div>
                            <div className="text-sm text-slate-400 leading-relaxed">{stage.description}</div>
                        </GlassCard>
                    )} />
                </div>
            </div>
        </section>
    )
}
