import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { strategicModes, tacticalActions, pipelineStages, hyperparameters } from '../data/projectData'
import { fadeInUp, staggerContainer, staggerItem, headerReveal, viewport, easeOutExpo } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

/** Animated data-stream dots flowing through a horizontal connector line */
function DataStream({ color }) {
    return (
        <div className="relative w-full h-6 flex items-center overflow-hidden">
            <div className="absolute inset-y-0 left-0 right-0 flex items-center">
                <div className="w-full h-px" style={{ background: `linear-gradient(90deg, transparent, ${color}40, transparent)` }} />
            </div>
            {[0, 1, 2].map(i => (
                <motion.div key={i}
                    className="absolute w-2 h-2 rounded-full"
                    style={{ background: color, boxShadow: `0 0 8px ${color}`, left: '-8px' }}
                    animate={{ x: ['0%', 'calc(100vw)'], opacity: [0, 1, 1, 0] }}
                    transition={{ duration: 2.5, delay: i * 0.8, repeat: Infinity, ease: 'linear' }}
                />
            ))}
        </div>
    )
}

/** One expandable layer card */
function LayerCard({ title, badge, badgeColor, icon, description, children, expanded, onToggle }) {
    return (
        <motion.div layout transition={{ duration: 0.5, ease: easeOutExpo }}>
            <GlassCard
                depth={expanded ? 2 : 1}
                accent={badgeColor}
                cornerGlow
                className="w-full cursor-pointer transition-all"
                onClick={onToggle}
                whileHover={!expanded ? { y: -4 } : {}}
            >
                {/* Badge pill */}
                <div className="absolute -top-3 left-6 px-3 py-0.5 rounded-full text-xs font-bold"
                    style={{ background: `linear-gradient(135deg, ${badgeColor}cc, ${badgeColor}88)`, color: '#fff', boxShadow: `0 0 16px ${badgeColor}60` }}>
                    {badge}
                </div>

                <div className="p-6 md:p-8">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-4">
                            <motion.span
                                className="text-3xl"
                                animate={expanded ? { rotate: [0, -10, 10, 0] } : { y: [0, -4, 0] }}
                                transition={{ duration: expanded ? 0.5 : 3, repeat: expanded ? 0 : Infinity, ease: 'easeInOut' }}
                            >
                                {icon}
                            </motion.span>
                            <div>
                                <h3 className="text-xl font-bold text-white">{title}</h3>
                                <p className="text-slate-400 text-sm mt-1 max-w-lg">{description}</p>
                            </div>
                        </div>
                        <motion.div
                            animate={{ rotate: expanded ? 180 : 0 }}
                            transition={{ duration: 0.3 }}
                            className="text-slate-500 text-lg shrink-0 ml-4"
                        >
                            ↓
                        </motion.div>
                    </div>

                    <AnimatePresence>
                        {expanded && (
                            <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.5, ease: easeOutExpo }}
                                className="overflow-hidden"
                            >
                                <div className="mt-6 pt-6 border-t border-white/10">
                                    {children}
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                </div>
            </GlassCard>
        </motion.div>
    )
}

export default function ArchitectureSection() {
    const [expandedLayer, setExpandedLayer] = useState(null)
    const [activeMode, setActiveMode] = useState(0)
    const [activeAction, setActiveAction] = useState(2)

    const toggle = (id) => setExpandedLayer(v => v === id ? null : id)

    return (
        <section id="architecture" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #080d1a 0%, #060912 100%)' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-24"
                style={{ background: 'linear-gradient(180deg, transparent, rgba(139,92,246,0.4), transparent)' }} />

            <div className="max-w-4xl mx-auto">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-20">
                    <SectionLabel color="#a78bfa">System Architecture</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        Two-level{' '}
                        <span className="gradient-text">hierarchical intelligence.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        Inspired by how real organisations operate — executives set strategy every quarter, managers execute daily.
                        Click any layer to explore it.
                    </p>
                </motion.div>

                <div className="flex flex-col gap-6">

                    {/* Strategic Layer */}
                    <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport}>
                        <LayerCard
                            title="Strategic Policy (DQN)"
                            badge="STRATEGIC LAYER · Every 30 Days"
                            badgeColor="#3b82f6"
                            icon="🧠"
                            description="Reads 30-day operational history and picks one of 3 modes: Conservative, Balanced, or Aggressive."
                            expanded={expandedLayer === 'strategic'}
                            onToggle={() => toggle('strategic')}
                        >
                            <div className="space-y-6">
                                <div className="text-xs font-mono text-slate-500 mb-2 uppercase tracking-wider">
                                    Architecture: 7 inputs → Dense(128) → ReLU → Dense(64) → ReLU → 3 modes (Dueling DQN)
                                </div>
                                <div className="grid grid-cols-3 gap-3">
                                    {strategicModes.map(m => (
                                        <motion.button key={m.id}
                                            onClick={e => { e.stopPropagation(); setActiveMode(m.id) }}
                                            whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
                                            className="text-left px-4 py-3 rounded-xl text-sm transition-all"
                                            style={activeMode === m.id ? {
                                                background: `linear-gradient(135deg, ${m.color}30, ${m.color}15)`,
                                                border: `1px solid ${m.color}60`,
                                                boxShadow: `0 0 20px ${m.color}30`,
                                            } : {
                                                background: 'rgba(255,255,255,0.02)',
                                                border: '1px solid rgba(255,255,255,0.07)',
                                            }}
                                        >
                                            <div className="text-xl mb-1">{m.icon}</div>
                                            <div className="font-bold text-white text-xs">{m.name}</div>
                                            <div className="text-[10px] font-mono mt-1" style={{ color: m.color }}>
                                                Reorder ×{m.multipliers.reorder}
                                            </div>
                                        </motion.button>
                                    ))}
                                </div>
                                <AnimatePresence mode="wait">
                                    <motion.div key={activeMode}
                                        initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                                        className="p-4 rounded-xl"
                                        style={{ background: `${strategicModes[activeMode].color}12`, border: `1px solid ${strategicModes[activeMode].color}30` }}>
                                        <div className="font-semibold text-sm mb-1" style={{ color: strategicModes[activeMode].color }}>
                                            {strategicModes[activeMode].icon} {strategicModes[activeMode].name}
                                        </div>
                                        <div className="text-slate-400 text-sm">{strategicModes[activeMode].description}</div>
                                        <div className="text-xs text-slate-500 mt-2">Best for: {strategicModes[activeMode].bestFor}</div>
                                        <div className="flex gap-4 mt-2 font-mono text-xs text-slate-500">
                                            <span>Reorder ×{strategicModes[activeMode].multipliers.reorder}</span>
                                            <span>Holding ×{strategicModes[activeMode].multipliers.holding}</span>
                                            <span>Stockout ×{strategicModes[activeMode].multipliers.stockout}</span>
                                        </div>
                                    </motion.div>
                                </AnimatePresence>
                            </div>
                        </LayerCard>
                    </motion.div>

                    {/* Connector */}
                    <div className="flex flex-col items-center gap-3 py-4">
                        <DataStream color="#6366f1" />
                        <span className="text-[10px] font-mono uppercase tracking-widest px-4 py-1.5 rounded-full text-purple-400 bg-slate-900/40"
                            style={{ border: '1px solid rgba(139,92,246,0.3)', backdropFilter: 'blur(8px)' }}>
                            Strategic mode conditions tactical decisions
                        </span>
                    </div>

                    {/* Tactical Layer */}
                    <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport}>
                        <LayerCard
                            title="Tactical Policy (DQN)"
                            badge="TACTICAL LAYER · Every Day"
                            badgeColor="#8b5cf6"
                            icon="⚙️"
                            description="Conditioned on the current strategic mode, selects one of 8 order quantities: 0 → 1,500 units."
                            expanded={expandedLayer === 'tactical'}
                            onToggle={() => toggle('tactical')}
                        >
                            <div className="space-y-4">
                                <div className="text-xs font-mono text-slate-500 mb-2 uppercase tracking-wider">
                                    Architecture: 10 inputs (7 state + 3 one-hot mode) → Dense(256) → Dense(128) → Dense(64) → 8 actions
                                </div>
                                <div className="grid grid-cols-4 gap-2">
                                    {tacticalActions.map(a => (
                                        <motion.button key={a.id}
                                            onClick={e => { e.stopPropagation(); setActiveAction(a.id) }}
                                            whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
                                            className="text-center px-3 py-3 rounded-xl text-xs transition-all"
                                            style={activeAction === a.id ? {
                                                background: `linear-gradient(135deg, ${a.color}30, ${a.color}15)`,
                                                border: `1px solid ${a.color}60`,
                                                boxShadow: `0 0 16px ${a.color}30`,
                                            } : {
                                                background: 'rgba(255,255,255,0.02)',
                                                border: '1px solid rgba(255,255,255,0.07)',
                                            }}
                                        >
                                            <div className="font-black text-xl text-white">{a.units}</div>
                                            <div className="font-medium text-slate-400 text-[10px]">{a.name}</div>
                                        </motion.button>
                                    ))}
                                </div>
                                <AnimatePresence mode="wait">
                                    <motion.div key={activeAction}
                                        initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                                        className="p-3 rounded-xl text-sm text-slate-400"
                                        style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.07)' }}>
                                        <span style={{ color: tacticalActions[activeAction].color }} className="font-bold">
                                            Action {activeAction}: {tacticalActions[activeAction].units} units
                                        </span>
                                        {' — '}{tacticalActions[activeAction].description}
                                    </motion.div>
                                </AnimatePresence>
                            </div>
                        </LayerCard>
                    </motion.div>

                    {/* Connector */}
                    <div className="flex flex-col items-center gap-3 py-4">
                        <DataStream color="#34d399" />
                        <span className="text-[10px] font-mono uppercase tracking-widest px-4 py-1.5 rounded-full text-emerald-400 bg-slate-900/40"
                            style={{ border: '1px solid rgba(52,211,153,0.3)', backdropFilter: 'blur(8px)' }}>
                            Actions execute → reward flows back up
                        </span>
                    </div>

                    {/* Environment */}
                    <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport}>
                        <LayerCard
                            title="Supply Chain Environment"
                            badge="ENVIRONMENT · Walmart M5 Data"
                            badgeColor="#34d399"
                            icon="🏭"
                            description="1,941 rows of real Walmart M5 time-series data. Simulates procurement, holding costs, and stochastic demand."
                            expanded={expandedLayer === 'env'}
                            onToggle={() => toggle('env')}
                        >
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                {[
                                    { label: 'Data rows', val: '1,941', note: 'Walmart M5 history', color: '#34d399' },
                                    { label: 'Episode length', val: '90 days', note: 'Per training episode', color: '#60a5fa' },
                                    { label: 'Warehouse cap', val: '5,000', note: 'Max units stored', color: '#a78bfa' },
                                    { label: 'Lagrangian λ', val: '10.0', note: 'Adaptive penalty', color: '#f59e0b' },
                                ].map(it => (
                                    <div key={it.label} className="rounded-xl p-4"
                                        style={{ background: `${it.color}0d`, border: `1px solid ${it.color}30` }}>
                                        <div className="text-xl font-black" style={{ color: it.color }}>{it.val}</div>
                                        <div className="text-white text-sm font-semibold mt-1">{it.label}</div>
                                        <div className="text-slate-500 text-xs mt-0.5">{it.note}</div>
                                    </div>
                                ))}
                            </div>
                        </LayerCard>
                    </motion.div>
                </div>

                {/* Hyperparameters */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport} className="mt-10">
                    <GlassCard depth={1} className="p-8">
                        <h3 className="text-lg font-bold text-white mb-6">Best Hyperparameters (Tuned Training · B+)</h3>
                        <motion.div variants={staggerContainer(0.04)} initial="hidden" whileInView="visible" viewport={viewport}
                            className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
                            {hyperparameters.map(({ k, v, group }) => (
                                <motion.div key={k} variants={staggerItem}
                                    whileHover={{ y: -3, borderColor: 'rgba(59,130,246,0.3)', boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}
                                    className="rounded-lg p-3 transition-all"
                                    style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
                                    <div className="text-slate-500 text-xs">{k}</div>
                                    <div className="font-bold" style={{ color: group === 'exploration' ? '#60a5fa' : group === 'reward' ? '#34d399' : group === 'optimiser' ? '#a78bfa' : '#f59e0b' }}>
                                        {v}
                                    </div>
                                </motion.div>
                            ))}
                        </motion.div>
                    </GlassCard>
                </motion.div>
            </div>
        </section>
    )
}
