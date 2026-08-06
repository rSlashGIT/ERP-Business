import { useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { strategicModes, tacticalActions } from '../data/projectData'
import { headerReveal, staggerContainer, staggerItem, buttonHover, buttonTap, viewport, useTilt } from '../utils/animations'
import SectionLabel from './shared/SectionLabel'
import GlassCard from './shared/GlassCard'

// Real action map from environment.py
const ACTION_UNITS = [0, 50, 150, 300, 500, 750, 1000, 1500]
const DEMAND_FACTORS = [0.82, 0.94, 1.08, 1.18, 0.91, 1.04, 1.16, 0.88, 1.10, 0.97]

function getDecision(inventory, demand) {
    const r = inventory / Math.max(demand, 1)
    if (r < 0.1) return { action: 7, color: '#ef4444', badge: 'Critical' }
    if (r < 0.3) return { action: 5, color: '#f97316', badge: 'Low' }
    if (r < 0.6) return { action: 3, color: '#f59e0b', badge: 'Moderate' }
    if (r < 1.0) return { action: 2, color: '#60a5fa', badge: 'Adequate' }
    if (r < 1.5) return { action: 1, color: '#34d399', badge: 'Good' }
    return { action: 0, color: '#10b981', badge: 'Excess' }
}

function getStrategy(serviceLevel, costPressure) {
    if (serviceLevel < 30) return strategicModes[0]     // Conservative
    if (costPressure > 70) return strategicModes[2]     // Aggressive
    return strategicModes[1]                             // Balanced
}

// Simulate (s,S) classical baseline: reorder to S when below s
function getSsDecision(inventory, s = 200, S = 800) {
    if (inventory <= s) {
        const need = S - inventory
        // Find closest action
        let bestIdx = 0
        ACTION_UNITS.forEach((u, i) => { if (Math.abs(u - need) < Math.abs(ACTION_UNITS[bestIdx] - need)) bestIdx = i })
        return { action: bestIdx, color: '#94a3b8' }
    }
    return { action: 0, color: '#94a3b8' }
}

function TiltPanel({ children, className = '', style = {} }) {
    const tilt = useTilt(8)
    return (
        <motion.div
            ref={tilt.ref}
            onMouseMove={tilt.onMouseMove}
            onMouseLeave={tilt.onMouseLeave}
            style={{ rotateX: tilt.rotateX, rotateY: tilt.rotateY, transformStyle: 'preserve-3d', ...style }}
            className={className}
        >
            {children}
        </motion.div>
    )
}

export default function DemoSection() {
    const [inventory, setInventory] = useState(150)
    const [demand, setDemand] = useState(200)
    const [serviceLevel, setServiceLevel] = useState(63)
    const [costPressure, setCostPressure] = useState(40)
    const [log, setLog] = useState([])
    const [compareLog, setCompareLog] = useState([])
    const [running, setRunning] = useState(false)
    const [compareMode, setCompareMode] = useState(false)

    const decision = getDecision(inventory, demand)
    const strategy = getStrategy(serviceLevel, costPressure)
    const ssDecision = getSsDecision(inventory)
    const ratio = inventory / Math.max(demand, 1)

    const runSim = useCallback(() => {
        setRunning(true)
        let inv = inventory
        let invSS = inventory
        const rows = []
        const rowsSS = []

        for (let day = 1; day <= 10; day++) {
            const dem = Math.round(demand * DEMAND_FACTORS[day - 1])

            // HRL
            const dec = getDecision(inv, dem)
            const order = ACTION_UNITS[dec.action]
            const sales = Math.min(inv, dem)
            inv = Math.max(0, inv - sales) + order
            rows.push({ day, inventory: Math.round(inv), demand: dem, sales, order, badge: dec.badge })

            // (s,S)
            const decSS = getSsDecision(invSS)
            const orderSS = ACTION_UNITS[decSS.action]
            const salesSS = Math.min(invSS, dem)
            invSS = Math.max(0, invSS - salesSS) + orderSS
            rowsSS.push({ day, inventory: Math.round(invSS), demand: dem, sales: salesSS, order: orderSS })
        }

        setLog(rows)
        setCompareLog(rowsSS)
        setTimeout(() => setRunning(false), 200)
    }, [inventory, demand])

    const sliders = [
        { label: 'Current Inventory', value: inventory, set: setInventory, min: 0, max: 500, color: '#60a5fa', suffix: ' units' },
        { label: 'Expected Demand', value: demand, set: setDemand, min: 10, max: 600, color: '#a78bfa', suffix: ' units' },
        { label: '30-day Service Level', value: serviceLevel, set: setServiceLevel, min: 0, max: 100, color: '#34d399', suffix: '%' },
        { label: 'Cost Pressure', value: costPressure, set: setCostPressure, min: 0, max: 100, color: '#f59e0b', suffix: '%' },
    ]

    return (
        <section id="demo" className="py-32 px-6 relative"
            style={{ background: 'linear-gradient(180deg, #080d1a 0%, #060912 100%)' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-px h-24"
                style={{ background: 'linear-gradient(180deg, transparent, rgba(6,182,212,0.4), transparent)' }} />

            <div className="max-w-7xl mx-auto">
                <motion.div variants={headerReveal} initial="hidden" whileInView="visible" viewport={viewport} className="text-center mb-20">
                    <SectionLabel color="#22d3ee">Interactive Demo</SectionLabel>
                    <h2 className="text-4xl md:text-5xl font-black text-white mb-6" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        Move the sliders.<br />
                        <span className="gradient-text">Watch the agent decide.</span>
                    </h2>
                    <p className="text-slate-400 text-lg max-w-2xl mx-auto">
                        Every value you touch triggers a decision from both the strategic and tactical policy — in real time.
                    </p>
                </motion.div>

                {/* Compare toggle */}
                <div className="flex justify-center mb-8">
                    <button
                        onClick={() => setCompareMode(v => !v)}
                        className="px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                        style={{
                            background: compareMode ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.03)',
                            border: compareMode ? '1px solid rgba(99,102,241,0.5)' : '1px solid rgba(255,255,255,0.1)',
                            color: compareMode ? '#a5b4fc' : '#94a3b8',
                        }}
                    >
                        {compareMode ? '✓' : '+'} Compare vs (s,S) classical baseline
                    </button>
                </div>

                <motion.div variants={staggerContainer(0.15)} initial="hidden" whileInView="visible" viewport={viewport}
                    className="grid lg:grid-cols-2 gap-8">

                    {/* Controls */}
                    <motion.div variants={staggerItem} className="space-y-5">
                        <TiltPanel>
                            <GlassCard depth={2} accent="#22d3ee" className="p-8">
                                <h3 className="text-lg font-bold text-white mb-6">Environment State</h3>
                                <div className="space-y-6">
                                    {sliders.map(s => (
                                        <div key={s.label}>
                                            <div className="flex justify-between text-sm mb-2">
                                                <span className="text-slate-400">{s.label}</span>
                                                <motion.span key={s.value} initial={{ scale: 1.2 }} animate={{ scale: 1 }}
                                                    className="font-mono font-bold" style={{ color: s.color }}>
                                                    {s.value}{s.suffix}
                                                </motion.span>
                                            </div>
                                            <input type="range" min={s.min} max={s.max} value={s.value}
                                                onChange={e => s.set(+e.target.value)}
                                                className="w-full" style={{ accentColor: s.color }} />
                                        </div>
                                    ))}
                                </div>
                            </GlassCard>
                        </TiltPanel>

                        {/* Coverage ratio bar */}
                        <GlassCard depth={1} className="p-6">
                            <div className="flex justify-between text-sm mb-2">
                                <span className="text-slate-400">Inventory Coverage Ratio</span>
                                <span className="text-white font-mono font-bold">{ratio.toFixed(2)}×</span>
                            </div>
                            <div className="w-full rounded-full h-3 overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
                                <motion.div
                                    animate={{ width: `${Math.min(100, ratio * 50)}%` }}
                                    transition={{ type: 'spring', stiffness: 200, damping: 20 }}
                                    className="h-3 rounded-full"
                                    style={{
                                        background: ratio < 0.3
                                            ? 'linear-gradient(90deg, #dc2626, #ef4444)'
                                            : ratio < 1.0
                                                ? 'linear-gradient(90deg, #d97706, #f59e0b)'
                                                : 'linear-gradient(90deg, #059669, #34d399)',
                                        boxShadow: ratio < 0.3 ? '0 0 12px rgba(239,68,68,0.5)' : '0 0 12px rgba(52,211,153,0.5)',
                                    }}
                                />
                            </div>
                            <div className="flex justify-between text-xs text-slate-600 mt-1.5">
                                <span>Critical</span><span>Low</span><span>Good</span><span>Excess</span>
                            </div>
                        </GlassCard>
                    </motion.div>

                    {/* Decision output */}
                    <motion.div variants={staggerItem} className="space-y-4">
                        {/* Strategic */}
                        <AnimatePresence mode="wait">
                            <motion.div key={strategy.id}
                                initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}>
                                <GlassCard depth={2} accent={strategy.color} cornerGlow className="p-6">
                                    <div className="text-xs font-bold mb-3 uppercase tracking-wider" style={{ color: strategy.color }}>
                                        Strategic Policy — Every 30 Days
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }}
                                            transition={{ type: 'spring', stiffness: 300 }} className="text-4xl">
                                            {strategy.icon}
                                        </motion.div>
                                        <div>
                                            <div className="text-2xl font-bold text-white">Mode {strategy.id}</div>
                                            <div className="font-semibold" style={{ color: strategy.color }}>{strategy.name}</div>
                                            <div className="text-xs text-slate-500 mt-1">{strategy.description}</div>
                                        </div>
                                    </div>
                                </GlassCard>
                            </motion.div>
                        </AnimatePresence>

                        {/* Tactical — HRL */}
                        <AnimatePresence mode="wait">
                            <motion.div key={decision.action}
                                initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}>
                                <GlassCard depth={2} accent={decision.color} className="p-6">
                                    <div className="text-xs font-bold mb-3 uppercase tracking-wider" style={{ color: '#a78bfa' }}>
                                        Tactical Policy — Daily
                                    </div>
                                    <div className="text-2xl font-bold mb-1" style={{ color: decision.color }}>
                                        Order {ACTION_UNITS[decision.action]} units
                                    </div>
                                    <div className="text-xs font-mono px-2 py-0.5 rounded-full inline-block mb-4"
                                        style={{ background: `${decision.color}20`, color: decision.color, border: `1px solid ${decision.color}40` }}>
                                        {decision.badge} inventory
                                    </div>
                                    {/* Q-value bars for all 8 actions */}
                                    <div className="space-y-1.5">
                                        <div className="text-xs text-slate-500 mb-2">Estimated Q-Values (8 actions)</div>
                                        {ACTION_UNITS.map((units, i) => {
                                            const selected = i === decision.action
                                            const width = selected ? 85 : Math.max(5, 85 - Math.abs(i - decision.action) * 15)
                                            return (
                                                <div key={i} className="flex items-center gap-2">
                                                    <span className="text-[10px] font-mono w-14 shrink-0 text-right"
                                                        style={{ color: selected ? '#fff' : '#475569', fontWeight: selected ? 700 : 400 }}>
                                                        {units}u
                                                    </span>
                                                    <div className="flex-1 rounded-full h-1.5 overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
                                                        <motion.div animate={{ width: `${width}%` }}
                                                            transition={{ type: 'spring', stiffness: 200, damping: 20 }}
                                                            className="h-1.5 rounded-full"
                                                            style={{
                                                                background: selected ? `linear-gradient(90deg, ${decision.color}aa, ${decision.color})` : 'rgba(255,255,255,0.08)',
                                                                boxShadow: selected ? `0 0 8px ${decision.color}60` : 'none',
                                                            }} />
                                                    </div>
                                                    {selected && <span className="text-[10px] shrink-0" style={{ color: decision.color }}>✓</span>}
                                                </div>
                                            )
                                        })}
                                    </div>

                                    {/* (s,S) comparison if enabled */}
                                    {compareMode && (
                                        <div className="mt-4 pt-4 border-t border-white/10">
                                            <div className="text-xs text-slate-500 mb-1">(s,S) Classical baseline would order:</div>
                                            <div className="text-lg font-bold" style={{ color: '#94a3b8' }}>
                                                {ACTION_UNITS[ssDecision.action]} units
                                            </div>
                                            <div className="text-xs text-slate-600">Policy: reorder to 800 when below 200</div>
                                        </div>
                                    )}
                                </GlassCard>
                            </motion.div>
                        </AnimatePresence>

                        <motion.button onClick={runSim} disabled={running}
                            whileHover={buttonHover} whileTap={buttonTap}
                            className="w-full py-4 rounded-xl font-bold text-white disabled:opacity-50"
                            style={{
                                background: 'linear-gradient(135deg, #2563eb, #7c3aed)',
                                boxShadow: '0 0 30px rgba(59,130,246,0.3), 0 4px 20px rgba(0,0,0,0.4)',
                            }}>
                            {running ? '⏳ Simulating…' : '▶ Run 10-Day Simulation'}
                        </motion.button>
                    </motion.div>
                </motion.div>

                {/* Simulation log */}
                <AnimatePresence>
                    {log.length > 0 && (
                        <motion.div initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                            transition={{ duration: 0.5 }} className="mt-8">
                            <div className={`grid gap-6 ${compareMode ? 'md:grid-cols-2' : 'grid-cols-1'}`}>
                                {[
                                    { title: 'Hierarchical RL', rows: log, color: '#3b82f6' },
                                    ...(compareMode ? [{ title: '(s,S) Classical Baseline', rows: compareLog, color: '#94a3b8' }] : []),
                                ].map(({ title, rows, color }) => (
                                    <GlassCard key={title} depth={1} accent={color} className="overflow-hidden">
                                        <div className="px-6 pt-6 pb-3 border-b border-white/08">
                                            <h3 className="text-base font-bold" style={{ color }}>{title}</h3>
                                        </div>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead>
                                                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                                                        {['Day', 'Inv.', 'Demand', 'Sales', 'Order'].map(h => (
                                                            <th key={h} className="px-4 py-2 text-left text-slate-400 font-medium text-xs uppercase tracking-wider">{h}</th>
                                                        ))}
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {rows.map((row, i) => (
                                                        <motion.tr key={row.day}
                                                            initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
                                                            transition={{ delay: i * 0.04 }}
                                                            className="hover:bg-white/[0.02] transition-colors"
                                                            style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                                            <td className="px-4 py-2 text-slate-400 font-mono text-xs">D{row.day}</td>
                                                            <td className="px-4 py-2 font-mono" style={{ color }}>{row.inventory}</td>
                                                            <td className="px-4 py-2 font-mono text-purple-400">{row.demand}</td>
                                                            <td className="px-4 py-2 font-mono text-emerald-400">{row.sales}</td>
                                                            <td className="px-4 py-2 font-mono text-yellow-400">+{row.order}</td>
                                                        </motion.tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </GlassCard>
                                ))}
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </section>
    )
}
