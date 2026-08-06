import { motion } from 'framer-motion'
import { staggerContainer, staggerItem, viewport } from '../utils/animations'

export default function Footer() {
    return (
        <footer className="relative py-20 px-6 overflow-hidden"
            style={{ background: '#040709', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            <div className="absolute top-0 left-0 right-0 h-px"
                style={{ background: 'linear-gradient(90deg, transparent, rgba(59,130,246,0.4), rgba(139,92,246,0.4), transparent)' }} />

            <div className="max-w-7xl mx-auto">
                <motion.div variants={staggerContainer(0.1)} initial="hidden" whileInView="visible" viewport={viewport}
                    className="grid md:grid-cols-3 gap-12 mb-12">

                    {/* Brand */}
                    <motion.div variants={staggerItem}>
                        <div className="flex items-center gap-2 mb-4">
                            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-black"
                                style={{ background: 'linear-gradient(135deg, #2563eb, #7c3aed)' }}>AI</div>
                            <span className="font-black text-white" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>IDSS</span>
                        </div>
                        <p className="text-slate-500 text-sm leading-relaxed mb-4">
                            Hierarchical Reinforcement Learning for supply chain optimisation. Two DQNs. Three strategic modes. Eight tactical actions.
                        </p>
                        <div className="flex gap-2">
                            <span className="px-3 py-1 rounded-full text-xs font-bold text-emerald-400"
                                style={{ background: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.25)' }}>
                                Grade: B+ (78.7/100)
                            </span>
                            <span className="px-3 py-1 rounded-full text-xs font-bold text-blue-400"
                                style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)' }}>
                                ✓ Complete
                            </span>
                        </div>
                    </motion.div>

                    {/* System components */}
                    <motion.div variants={staggerItem}>
                        <h4 className="text-white font-bold mb-4 text-sm uppercase tracking-wider">System</h4>
                        <ul className="space-y-2 text-sm text-slate-500">
                            {[
                                'Strategic Policy (DQN · 3 modes)',
                                'Tactical Policy (DQN · 8 actions)',
                                'Hierarchical Agent Coordinator',
                                'SupplyChainEnv (Gym-style)',
                                'Simple EMA Uncertainty Model',
                                'SHAP Explainability Engine',
                            ].map(c => (
                                <li key={c} className="flex items-center gap-2">
                                    <span className="w-1 h-1 rounded-full bg-slate-600 inline-block shrink-0" />
                                    {c}
                                </li>
                            ))}
                        </ul>
                    </motion.div>

                    {/* Key results */}
                    <motion.div variants={staggerItem}>
                        <h4 className="text-white font-bold mb-4 text-sm uppercase tracking-wider">Results</h4>
                        <ul className="space-y-2 text-sm text-slate-500">
                            {[
                                ['📈', '+11,105 Best episode reward'],
                                ['🎯', '63% Service level achieved'],
                                ['📊', '78.7/100 Accuracy score'],
                                ['⚡', '33× improvement over baseline DQN'],
                                ['🔄', '521 Strategic mode shifts'],
                                ['⏱️', '~30 min training (M2 Air)'],
                            ].map(([icon, text]) => (
                                <li key={text} className="flex items-center gap-2">
                                    <span>{icon}</span>
                                    {text}
                                </li>
                            ))}
                        </ul>
                    </motion.div>
                </motion.div>

                <div className="border-t border-white/06 pt-8 flex flex-col md:flex-row items-center justify-between gap-4 text-xs text-slate-600">
                    <span>Hierarchical RL Supply Chain IDSS · Built with PyTorch + React</span>
                    <span className="font-mono">Data: Walmart M5 (1,941 rows) · Seeds: 5 · Episodes: 1,000</span>
                </div>
            </div>
        </footer>
    )
}
