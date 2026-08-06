import { useState, useRef } from 'react'
import { motion, AnimatePresence, useMotionValue, useTransform, animate } from 'framer-motion'
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
    RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
    ResponsiveContainer, Cell,
} from 'recharts'
import { accuracyBreakdown, performanceComparison, agentComparison } from '../data/projectData'
import { fadeInUp, staggerContainer, staggerItem, headerReveal, viewport, easeOutExpo, useCountUp } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

const COLORS = ['#3b82f6', '#8b5cf6', '#22d3ee', '#10b981']

const TooltipBox = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    return (
        <div className="rounded-xl p-3 border border-white/10 text-xs shadow-2xl"
            style={{ background: 'rgba(14,20,45,0.95)', backdropFilter: 'blur(12px)' }}>
            <p className="font-semibold text-white mb-1">{label}</p>
            {payload.map((p, i) => (
                <p key={i} style={{ color: p.color }}>{p.name}: {typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</p>
            ))}
        </div>
    )
}

/** Draggable carousel for training runs & agent comparisons */
function DragCarousel({ items, renderItem }) {
    const x = useMotionValue(0)
    const containerRef = useRef(null)
    const [current, setCurrent] = useState(0)
    const cardW = 280
    const gap = 16

    const snapTo = (idx) => {
        const clampedIdx = Math.max(0, Math.min(idx, items.length - 1))
        setCurrent(clampedIdx)
        animate(x, -(clampedIdx * (cardW + gap)), { type: 'spring', stiffness: 300, damping: 30 })
    }

    return (
        <div className="relative overflow-hidden" ref={containerRef}>
            <motion.div
                drag="x"
                style={{ x, width: 'max-content' }}
                dragConstraints={{ left: -((items.length - 1) * (cardW + gap)), right: 0 }}
                dragElastic={0.15}
                onDragEnd={(_, info) => {
                    const vel = info.velocity.x
                    if (vel < -300) snapTo(current + 1)
                    else if (vel > 300) snapTo(current - 1)
                    else {
                        const rawIdx = Math.round(-x.get() / (cardW + gap))
                        snapTo(rawIdx)
                    }
                }}
                className="flex gap-4 cursor-grab active:cursor-grabbing select-none"
            >
                {items.map((item, i) => (
                    <motion.div
                        key={i}
                        animate={{ scale: current === i ? 1 : 0.92, opacity: current === i ? 1 : 0.55 }}
                        transition={{ duration: 0.35 }}
                        style={{ width: cardW, flexShrink: 0 }}
                    >
                        {renderItem(item, i, current === i)}
                    </motion.div>
                ))}
            </motion.div>

            {/* Dot indicators */}
            <div className="flex gap-2 justify-center mt-4">
                {items.map((_, i) => (
                    <button key={i} onClick={() => snapTo(i)}
                        className="rounded-full transition-all"
                        style={{
                            width: current === i ? 24 : 8, height: 8,
                            background: current === i ? '#a78bfa' : 'rgba(255,255,255,0.2)',
                        }}
                    />
                ))}
            </div>
        </div>
    )
}

function StatCard({ label, value, note, color }) {
    const [count, ref] = useCountUp(parseFloat(value.replace(/[^0-9.]/g, '')) || 0, { duration: 1600 })
    const isPlain = /^[+\-]?\d/.test(value)
    return (
        <motion.div ref={ref} whileHover={{ y: -5, boxShadow: `0 12px 32px rgba(0,0,0,0.4), 0 0 20px ${color}20` }}
            className="rounded-xl p-4 relative overflow-hidden transition-all cursor-default"
            style={{ background: 'rgba(255,255,255,0.02)', border: `1px solid ${color}25` }}>
            <div className="absolute top-0 left-0 right-0 h-px" style={{ background: `linear-gradient(90deg, transparent, ${color}60, transparent)` }} />
            <div className="text-2xl font-black" style={{ color }}>
                {isPlain ? (value.startsWith('+') ? '+' : '') + (Number.isInteger(parseFloat(value.replace(/[^0-9.]/g, ''))) ? Math.floor(count).toLocaleString() : count.toFixed(1)) + (value.endsWith('%') ? '%' : '') : value}
            </div>
            <div className="text-sm font-semibold text-white">{label}</div>
            <div className="text-xs text-slate-500 mt-1">{note}</div>
        </motion.div>
    )
}

export default function ResultsSection() {
    const [gradeExpanded, setGradeExpanded] = useState(false)
    const radarData = accuracyBreakdown.map(d => ({ subject: d.component.replace(' Score', '').replace(' Level', ''), score: d.score }))
    const barData = agentComparison.map(a => ({ name: a.agent, serviceLevel: a.serviceLevel }))

    return (
        <section id="results" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #060912 0%, #080d1a 100%)' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-24"
                style={{ background: 'linear-gradient(180deg, transparent, rgba(52,211,153,0.4), transparent)' }} />

            <div className="max-w-7xl mx-auto">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-20">
                    <SectionLabel color="#34d399">Results & Performance</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif', lineHeight: 1.2 }}>
                        A baseline DQN loses money.<br />
                        <span className="gradient-text">Ours doesn't.</span>
                    </h2>
                </motion.div>

                {/* Grade card — expandable */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport} className="mb-8">
                    <GlassCard depth={2} accent="#3b82f6" cornerGlow
                        className="cursor-pointer"
                        onClick={() => setGradeExpanded(v => !v)}
                        whileHover={{ y: -4 }}
                        layout
                    >
                        <div className="p-8 md:p-10">
                            <div className="flex flex-col md:flex-row items-center justify-between gap-8">
                                <motion.div layout className="text-center md:text-left">
                                    <div className="font-black gradient-text leading-none"
                                        style={{ fontSize: '5rem', fontFamily: 'Syne, Inter, sans-serif', textShadow: '0 0 80px rgba(96,165,250,0.3)' }}>
                                        B+
                                    </div>
                                    <div className="text-2xl font-bold text-white mt-1">78.7 / 100</div>
                                    <div className="text-slate-500 text-sm mt-1 flex items-center gap-2">
                                        Final accuracy score
                                        <span className="text-xs text-purple-400 font-mono">
                                            {gradeExpanded ? '▲ collapse' : '▼ expand breakdown'}
                                        </span>
                                    </div>
                                </motion.div>

                                <motion.div variants={staggerContainer(0.1)} initial="hidden" whileInView="visible" viewport={viewport}
                                    className="grid grid-cols-2 gap-4 flex-1 max-w-lg">
                                    <StatCard label="Best Reward" value="+11,105" note="vs +336 original" color="#34d399" />
                                    <StatCard label="Service Level" value="63%" note="vs 0% baseline" color="#60a5fa" />
                                    <StatCard label="Stability σ" value="±9,630" note="76% improvement" color="#a78bfa" />
                                    <StatCard label="Mode Shifts" value="521" note="strategic adaptations" color="#f59e0b" />
                                </motion.div>
                            </div>

                            <AnimatePresence>
                                {gradeExpanded && (
                                    <motion.div
                                        initial={{ opacity: 0, height: 0 }}
                                        animate={{ opacity: 1, height: 'auto' }}
                                        exit={{ opacity: 0, height: 0 }}
                                        transition={{ duration: 0.5, ease: easeOutExpo }}
                                        className="overflow-hidden"
                                    >
                                        <div className="mt-8 pt-8 border-t border-white/10 grid md:grid-cols-2 gap-6">
                                            <div>
                                                <h4 className="text-white font-semibold mb-4">Score Breakdown</h4>
                                                <ResponsiveContainer width="100%" height={200}>
                                                    <BarChart data={accuracyBreakdown} layout="vertical">
                                                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                                                        <XAxis type="number" domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                                                        <YAxis type="category" dataKey="component" tick={{ fill: '#94a3b8', fontSize: 10 }} width={130} />
                                                        <Tooltip content={<TooltipBox />} />
                                                        <Bar dataKey="score" radius={[0, 4, 4, 0]} name="Score">
                                                            {accuracyBreakdown.map((_, i) => <Cell key={i} fill={COLORS[i]} />)}
                                                        </Bar>
                                                    </BarChart>
                                                </ResponsiveContainer>
                                            </div>
                                            <div>
                                                <h4 className="text-white font-semibold mb-4">Performance Radar</h4>
                                                <ResponsiveContainer width="100%" height={200}>
                                                    <RadarChart data={radarData}>
                                                        <PolarGrid stroke="rgba(255,255,255,0.06)" />
                                                        <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                                                        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 9 }} />
                                                        <Radar name="Score" dataKey="score" stroke="#8b5cf6" fill="#8b5cf6" fillOpacity={0.2} />
                                                    </RadarChart>
                                                </ResponsiveContainer>
                                            </div>
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    </GlassCard>
                </motion.div>

                {/* Service level comparison chart */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport} className="mb-10">
                    <GlassCard depth={1} className="p-8">
                        <h3 className="text-lg font-bold text-white mb-6">Service Level — All Agents vs Baseline</h3>
                        <ResponsiveContainer width="100%" height={180}>
                            <BarChart data={barData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                                <XAxis dataKey="name" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                                <YAxis domain={[0, 100]} unit="%" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                                <Tooltip content={<TooltipBox />} />
                                <Bar dataKey="serviceLevel" radius={[4, 4, 0, 0]} name="Service Level (%)">
                                    {barData.map((entry, i) => (
                                        <Cell key={i} fill={entry.name === 'Hierarchical RL' ? '#3b82f6' : '#1e293b'} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </GlassCard>
                </motion.div>

                {/* Training runs carousel */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport} className="mb-6">
                    <div className="mb-6">
                        <h3 className="text-xl font-bold text-white mb-1">Three Training Runs</h3>
                        <p className="text-slate-400 text-sm">Drag to explore. +176% improvement through hyperparameter tuning.</p>
                    </div>
                    <DragCarousel items={performanceComparison} renderItem={(run, i, active) => (
                        <GlassCard depth={active ? 2 : 1} accent={i === 1 ? '#34d399' : undefined} className="p-6 h-full" style={{ minHeight: 220 }}>
                            {i === 1 && (
                                <div className="absolute -top-2.5 left-4 px-2 py-0.5 rounded-full text-xs font-bold text-black"
                                    style={{ background: 'linear-gradient(135deg, #10b981, #34d399)', boxShadow: '0 0 12px rgba(52,211,153,0.5)' }}>
                                    BEST
                                </div>
                            )}
                            <div className="text-slate-400 text-sm mb-1">{run.name}</div>
                            <div className="text-4xl font-black mb-1" style={{ fontFamily: 'Syne, Inter, sans-serif', color: i === 1 ? '#34d399' : '#fff' }}>
                                {run.grade}
                            </div>
                            <div className="text-lg font-bold text-white mb-4">{run.accuracy}/100</div>
                            <div className="space-y-1.5 text-sm border-t border-white/10 pt-4">
                                <div className="flex justify-between"><span className="text-slate-400">Best Reward</span><span className="font-mono text-white">+{run.bestReward.toLocaleString()}</span></div>
                                <div className="flex justify-between"><span className="text-slate-400">Service Level</span><span className="text-white">{run.serviceLevel}%</span></div>
                                <div className="flex justify-between"><span className="text-slate-400">Episodes</span><span className="text-white">{run.episodes}</span></div>
                            </div>
                            <p className="text-xs text-slate-500 mt-4 leading-relaxed">{run.narrative}</p>
                        </GlassCard>
                    )} />
                </motion.div>

                {/* Agent comparison carousel */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport}>
                    <div className="mb-6">
                        <h3 className="text-xl font-bold text-white mb-1">Agent Comparison</h3>
                        <p className="text-slate-400 text-sm">Drag to compare. The (s,S) baseline is deliberately shown — we only win if we beat it.</p>
                    </div>
                    <DragCarousel items={agentComparison} renderItem={(agent, i, active) => (
                        <GlassCard depth={active ? 2 : 1} accent={agent.agent === 'Hierarchical RL' ? '#3b82f6' : undefined} className="p-6" style={{ minHeight: 180 }}>
                            <div className="font-bold text-white mb-2">{agent.agent}</div>
                            <div className="text-3xl font-black mb-1" style={{ color: agent.serviceLevel >= 60 ? '#34d399' : agent.serviceLevel >= 30 ? '#f59e0b' : '#ef4444' }}>
                                {agent.serviceLevel}%
                            </div>
                            <div className="text-xs text-slate-500 mb-3">Service level</div>
                            <div className="text-sm font-mono text-slate-400 mb-3">
                                Avg reward: {agent.avgReward.toLocaleString()}
                            </div>
                            <p className="text-xs text-slate-500 leading-relaxed">{agent.description}</p>
                        </GlassCard>
                    )} />
                </motion.div>
            </div>
        </section>
    )
}
