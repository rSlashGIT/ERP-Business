import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { fadeInUp, staggerContainer, staggerItem, headerReveal, viewport, easeOutExpo } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

/* ── real θ values from theta_values.json ── */
const SEEDS = [
    { id: 42,  theta: [-10.736, 7.479, 5.187, 0.504, 4.776, 3.582, 3.212, -3.520], inventory: 120, demand_mean: 65, demand_std: 15, lead_time: 3, profit: 11820, label: 'Seed 42' },
    { id: 123, theta: [1.014, 14.085, 16.017, 9.267, 9.388, 2.503, 6.527, -6.868], inventory: 180, demand_mean: 85, demand_std: 19, lead_time: 4, profit: 12785, label: 'Seed 123', best: true },
    { id: 456, theta: [2.845, 6.356, -8.978, 1.876, 10.617, 2.333, 6.407, -2.969], inventory: 150, demand_mean: 75, demand_std: 18, lead_time: 5, profit: 12340, label: 'Seed 456' },
    { id: 789, theta: [12.123, -12.566, -2.140, 3.394, -2.427, 2.571, 6.109, 6.478], inventory: 200, demand_mean: 95, demand_std: 22, lead_time: 3, profit: 11950, label: 'Seed 789' },
    { id: 999, theta: [17.979, 18.151, 9.938, 28.753, 0.330, 2.563, 5.269, -8.826], inventory: 90, demand_mean: 55, demand_std: 12, lead_time: 6, profit: 13120, label: 'Seed 999' },
]

const ACTIONS = [0, 50, 150, 300, 500, 750, 1000, 1500]

function compute(seed) {
    const [s_base, S_base, w1, w2, w3, w4, w5, w6] = seed.theta

    const s_ctx = s_base + w1 * seed.demand_mean + w2 * seed.demand_std + w3 * seed.lead_time
    const S_ctx = S_base + w4 * seed.demand_mean + w5 * seed.demand_std + w6 * seed.lead_time

    const target_order = Math.max(0, S_ctx - seed.inventory)
    const order = ACTIONS.reduce((prev, curr) =>
        Math.abs(curr - target_order) < Math.abs(prev - target_order) ? curr : prev
    )

    const projected = seed.inventory - seed.demand_mean * seed.lead_time

    return {
        s_ctx,
        S_ctx,
        order,
        target_order,
        projected,
        // breakdown
        s_parts: [
            { label: 'Base (s_base)', value: s_base, weight: null, contrib: s_base },
            { label: 'Demand Forecast', value: seed.demand_mean, weight: w1, contrib: w1 * seed.demand_mean },
            { label: 'Demand Volatility', value: seed.demand_std, weight: w2, contrib: w2 * seed.demand_std },
            { label: 'Lead Time', value: seed.lead_time, weight: w3, contrib: w3 * seed.lead_time },
        ],
        S_parts: [
            { label: 'Base (S_base)', value: S_base, weight: null, contrib: S_base },
            { label: 'Demand Forecast', value: seed.demand_mean, weight: w4, contrib: w4 * seed.demand_mean },
            { label: 'Demand Volatility', value: seed.demand_std, weight: w5, contrib: w5 * seed.demand_std },
            { label: 'Lead Time', value: seed.lead_time, weight: w6, contrib: w6 * seed.lead_time },
        ],
    }
}

/* ── sub-components ── */

function ContextRow({ label, value, sub }) {
    return (
        <div className="flex justify-between items-baseline py-2" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
            <span className="text-slate-400 text-sm">{label}</span>
            <div className="text-right">
                <span className="text-white font-semibold font-mono">{value}</span>
                {sub && <div className="text-[10px] text-slate-600">{sub}</div>}
            </div>
        </div>
    )
}

function MetricBox({ title, value, unit, hint, color, highlight }) {
    return (
        <motion.div
            whileHover={{ y: -4, boxShadow: `0 12px 32px rgba(0,0,0,0.4), 0 0 20px ${color}20` }}
            className="rounded-xl p-5 transition-all relative overflow-hidden"
            style={{
                background: highlight ? `${color}12` : 'rgba(255,255,255,0.02)',
                border: `1px solid ${color}${highlight ? '50' : '25'}`,
            }}
        >
            {highlight && (
                <div className="absolute top-0 left-0 right-0 h-px" style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }} />
            )}
            <div className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">{title}</div>
            <div className="text-3xl font-black font-mono" style={{ color }}>{typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value}</div>
            <div className="text-xs text-slate-500 mt-1">{unit}</div>
            <div className="text-[10px] text-slate-600 mt-2 leading-relaxed">{hint}</div>
        </motion.div>
    )
}

function BreakdownRow({ part }) {
    const sign = part.contrib >= 0 ? '+' : ''
    return (
        <div className="flex items-center justify-between py-1.5 text-sm" style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
            <span className="text-slate-400">{part.label}</span>
            <div className="flex items-center gap-4">
                {part.weight !== null && (
                    <span className="text-[11px] font-mono text-slate-600">
                        {part.weight.toFixed(3)} x {part.value}
                    </span>
                )}
                <span className="font-mono font-semibold text-white w-20 text-right">
                    {sign}{part.contrib.toFixed(2)}
                </span>
            </div>
        </div>
    )
}

/* ── main section ── */

export default function PolicyPlaygroundSection() {
    const [activeIdx, setActiveIdx] = useState(1) // default to seed 123
    const [showMath, setShowMath] = useState(false)

    const seed = SEEDS[activeIdx]
    const result = useMemo(() => compute(seed), [activeIdx])

    return (
        <section id="playground" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #060912 0%, #0a1020 50%, #060912 100%)' }}>

            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-24"
                style={{ background: 'linear-gradient(180deg, transparent, rgba(139,92,246,0.4), transparent)' }} />

            <div className="max-w-7xl mx-auto">
                {/* Header */}
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-16">
                    <SectionLabel color="#a78bfa">Policy Playground</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif', lineHeight: 1.2 }}>
                        Real parameters.<br />
                        <span className="gradient-text">Real decisions.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        These are the actual learned parameters from CMA-ES optimization.
                        See how the (s,S) policy adapts to each scenario.
                    </p>
                </motion.div>

                {/* Seed selector pills */}
                <motion.div variants={fadeInUp} initial="hidden" whileInView="visible" viewport={viewport}
                    className="flex gap-3 flex-wrap justify-center mb-12">
                    {SEEDS.map((s, i) => (
                        <motion.button
                            key={s.id}
                            onClick={() => { setActiveIdx(i); setShowMath(false) }}
                            whileHover={{ y: -2 }}
                            whileTap={{ scale: 0.97 }}
                            className="relative px-5 py-3 rounded-xl font-semibold transition-all"
                            style={{
                                background: activeIdx === i ? 'rgba(139,92,246,0.2)' : 'rgba(255,255,255,0.03)',
                                border: activeIdx === i ? '1px solid rgba(139,92,246,0.5)' : '1px solid rgba(255,255,255,0.08)',
                                color: activeIdx === i ? '#fff' : '#94a3b8',
                            }}
                        >
                            {s.best && (
                                <span className="absolute -top-2 -right-2 px-1.5 py-0.5 rounded-full text-[9px] font-bold text-black"
                                    style={{ background: 'linear-gradient(135deg, #10b981, #34d399)' }}>
                                    BEST
                                </span>
                            )}
                            <div className="text-sm font-bold">{s.label}</div>
                            <div className="text-xs mt-0.5" style={{ color: activeIdx === i ? '#34d399' : '#475569' }}>
                                +${s.profit.toLocaleString()}
                            </div>
                        </motion.button>
                    ))}
                </motion.div>

                <AnimatePresence mode="wait">
                    <motion.div
                        key={seed.id}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                        transition={{ duration: 0.4, ease: easeOutExpo }}
                    >
                        <motion.div variants={staggerContainer(0.12)} initial="hidden" whileInView="visible" viewport={viewport}
                            className="grid lg:grid-cols-2 gap-8">

                            {/* Left: Context */}
                            <motion.div variants={staggerItem}>
                                <GlassCard depth={2} accent="#3b82f6" className="p-8 h-full">
                                    <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-3">
                                        <span className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                                            style={{ background: 'rgba(59,130,246,0.2)', border: '1px solid rgba(59,130,246,0.3)' }}>
                                            S{seed.id}
                                        </span>
                                        Today's Situation
                                    </h3>

                                    <div className="space-y-1">
                                        <ContextRow label="Current Inventory" value={`${seed.inventory} units`} />
                                        <ContextRow label="Expected Daily Demand" value={`${seed.demand_mean} units/day`} />
                                        <ContextRow label="Demand Uncertainty" value={`\u00B1${seed.demand_std} units`} sub="standard deviation" />
                                        <ContextRow label="Supplier Lead Time" value={`${seed.lead_time} days`} />
                                    </div>

                                    <div className="mt-6 p-4 rounded-lg" style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.15)' }}>
                                        <div className="text-xs text-slate-400 mb-1">Projected stock when shipment arrives</div>
                                        <div className="text-xl font-bold font-mono" style={{ color: result.projected < 0 ? '#ef4444' : result.projected < 50 ? '#f59e0b' : '#34d399' }}>
                                            {result.projected.toFixed(0)} units
                                        </div>
                                        <div className="text-[10px] text-slate-600 mt-1">
                                            = {seed.inventory} - ({seed.demand_mean} x {seed.lead_time} days)
                                        </div>
                                    </div>
                                </GlassCard>
                            </motion.div>

                            {/* Right: Decision */}
                            <motion.div variants={staggerItem}>
                                <GlassCard depth={2} accent="#22d3ee" cornerGlow className="p-8 h-full">
                                    <h3 className="text-lg font-bold text-white mb-6">Policy Decision</h3>

                                    <div className="grid grid-cols-3 gap-4 mb-6">
                                        <MetricBox
                                            title="Reorder Trigger (s)"
                                            value={result.s_ctx.toFixed(2)}
                                            unit="units"
                                            hint="Order when inventory drops below this"
                                            color="#10b981"
                                        />
                                        <MetricBox
                                            title="Target Level (S)"
                                            value={result.S_ctx.toFixed(2)}
                                            unit="units"
                                            hint="Order up to this level"
                                            color="#8b5cf6"
                                        />
                                        <MetricBox
                                            title="Order Now"
                                            value={result.order}
                                            unit="units"
                                            hint="Nearest valid action"
                                            color="#f59e0b"
                                            highlight
                                        />
                                    </div>

                                    {/* Explanation */}
                                    <div className="p-4 rounded-lg" style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)' }}>
                                        <p className="text-sm text-slate-300 leading-relaxed">
                                            {seed.inventory > result.s_ctx ? (
                                                <>
                                                    Inventory ({seed.inventory}) is <strong className="text-emerald-400">above</strong> the reorder trigger ({result.s_ctx.toFixed(0)}).
                                                    {result.order > 0
                                                        ? <> But the policy still orders <strong className="text-amber-400">{result.order} units</strong> to proactively build buffer before projected demand depletes stock to {result.projected.toFixed(0)} units.</>
                                                        : <> No order needed right now.</>
                                                    }
                                                </>
                                            ) : (
                                                <>
                                                    Inventory ({seed.inventory}) is <strong className="text-red-400">below</strong> the reorder trigger ({result.s_ctx.toFixed(0)}).
                                                    Ordering <strong className="text-amber-400">{result.order} units</strong> to bring stock toward the target level of {result.S_ctx.toFixed(0)}.
                                                </>
                                            )}
                                        </p>
                                    </div>

                                    {/* Show math toggle */}
                                    <button
                                        onClick={() => setShowMath(v => !v)}
                                        className="mt-4 w-full py-2.5 rounded-xl text-sm font-semibold transition-all"
                                        style={{
                                            background: showMath ? 'rgba(139,92,246,0.15)' : 'rgba(255,255,255,0.03)',
                                            border: showMath ? '1px solid rgba(139,92,246,0.4)' : '1px solid rgba(255,255,255,0.08)',
                                            color: showMath ? '#c4b5fd' : '#94a3b8',
                                        }}
                                    >
                                        {showMath ? '- Hide' : '+ Show'} Calculation Breakdown
                                    </button>
                                </GlassCard>
                            </motion.div>
                        </motion.div>

                        {/* Math breakdown */}
                        <AnimatePresence>
                            {showMath && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0 }}
                                    animate={{ opacity: 1, height: 'auto' }}
                                    exit={{ opacity: 0, height: 0 }}
                                    transition={{ duration: 0.5, ease: easeOutExpo }}
                                    className="overflow-hidden mt-8"
                                >
                                    <div className="grid md:grid-cols-2 gap-8">
                                        {/* s(ctx) */}
                                        <GlassCard depth={1} accent="#10b981" className="p-8">
                                            <h4 className="text-sm font-bold uppercase tracking-wider mb-1" style={{ color: '#10b981' }}>
                                                Reorder Point
                                            </h4>
                                            <div className="text-xs text-slate-500 font-mono mb-6">
                                                s(ctx) = s_base + w1*demand + w2*volatility + w3*lead_time
                                            </div>

                                            <div className="space-y-1">
                                                {result.s_parts.map(p => <BreakdownRow key={p.label} part={p} />)}
                                            </div>

                                            <div className="mt-4 pt-3 flex justify-between items-baseline" style={{ borderTop: '1px solid rgba(16,185,129,0.3)' }}>
                                                <span className="text-white font-semibold">Total s(context)</span>
                                                <span className="text-2xl font-black font-mono" style={{ color: '#10b981' }}>
                                                    {result.s_ctx.toFixed(2)}
                                                </span>
                                            </div>
                                        </GlassCard>

                                        {/* S(ctx) */}
                                        <GlassCard depth={1} accent="#8b5cf6" className="p-8">
                                            <h4 className="text-sm font-bold uppercase tracking-wider mb-1" style={{ color: '#8b5cf6' }}>
                                                Order-Up-To Level
                                            </h4>
                                            <div className="text-xs text-slate-500 font-mono mb-6">
                                                S(ctx) = S_base + w4*demand + w5*volatility + w6*lead_time
                                            </div>

                                            <div className="space-y-1">
                                                {result.S_parts.map(p => <BreakdownRow key={p.label} part={p} />)}
                                            </div>

                                            <div className="mt-4 pt-3 flex justify-between items-baseline" style={{ borderTop: '1px solid rgba(139,92,246,0.3)' }}>
                                                <span className="text-white font-semibold">Total S(context)</span>
                                                <span className="text-2xl font-black font-mono" style={{ color: '#8b5cf6' }}>
                                                    {result.S_ctx.toFixed(2)}
                                                </span>
                                            </div>
                                        </GlassCard>
                                    </div>

                                    {/* Raw theta */}
                                    <div className="mt-6">
                                        <GlassCard depth={0} className="p-6">
                                            <div className="flex items-center gap-3 mb-3">
                                                <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Raw Parameters</span>
                                                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full"
                                                    style={{ background: 'rgba(255,255,255,0.04)', color: '#64748b', border: '1px solid rgba(255,255,255,0.06)' }}>
                                                    8 values
                                                </span>
                                            </div>
                                            <div className="font-mono text-sm text-slate-400">
                                                <span className="text-slate-600">theta = </span>
                                                [{seed.theta.map((v, i) => (
                                                    <span key={i}>
                                                        <span style={{ color: i < 2 ? '#60a5fa' : i < 5 ? '#10b981' : '#8b5cf6' }}>
                                                            {v.toFixed(3)}
                                                        </span>
                                                        {i < 7 ? ', ' : ''}
                                                    </span>
                                                ))}]
                                            </div>
                                            <div className="flex gap-4 mt-3 text-[10px]">
                                                <span><span className="inline-block w-2 h-2 rounded-sm mr-1" style={{ background: '#60a5fa' }} />Bases (s, S)</span>
                                                <span><span className="inline-block w-2 h-2 rounded-sm mr-1" style={{ background: '#10b981' }} />Weights for s</span>
                                                <span><span className="inline-block w-2 h-2 rounded-sm mr-1" style={{ background: '#8b5cf6' }} />Weights for S</span>
                                            </div>
                                        </GlassCard>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </motion.div>
                </AnimatePresence>
            </div>
        </section>
    )
}
