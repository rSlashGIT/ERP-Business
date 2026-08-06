import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { buttonHover, buttonTap } from '../utils/animations'

const NAV_LINKS = [
    { label: 'Overview', href: '#overview' },
    { label: 'Architecture', href: '#architecture' },
    { label: 'Results', href: '#results' },
    { label: 'Demo', href: '#demo' },
    { label: 'Playground', href: '#playground' },
    { label: 'Explain', href: '#explain' },
    { label: 'Tech', href: '#tech' },
]

export default function Navbar() {
    const [scrolled, setScrolled] = useState(false)
    const [open, setOpen] = useState(false)
    const [active, setActive] = useState('overview')

    useEffect(() => {
        const onScroll = () => setScrolled(window.scrollY > 50)
        window.addEventListener('scroll', onScroll, { passive: true })
        return () => window.removeEventListener('scroll', onScroll)
    }, [])

    useEffect(() => {
        const onScroll = () => {
            const viewportHalf = window.innerHeight * 0.45;
            let currentId = NAV_LINKS[0].href.slice(1);
            
            for (let i = 0; i < NAV_LINKS.length; i++) {
                const id = NAV_LINKS[i].href.slice(1);
                const el = document.getElementById(id);
                if (!el) continue;
                const rect = el.getBoundingClientRect();
                if (rect.top <= viewportHalf) {
                    currentId = id;
                }
            }
            setActive(currentId);
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    return (
        <motion.header
            initial={{ y: -100, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
            className="fixed top-0 left-0 right-0 z-navbar px-6 py-4"
            style={{
                background: scrolled ? 'rgba(6,9,18,0.85)' : 'transparent',
                backdropFilter: scrolled ? 'blur(20px) saturate(180%)' : 'none',
                borderBottom: scrolled ? '1px solid rgba(255,255,255,0.06)' : 'none',
                transition: 'background 0.4s ease, backdrop-filter 0.4s ease, border-color 0.4s ease',
            }}
        >
            <nav className="max-w-7xl mx-auto flex items-center justify-between">
                {/* Logo */}
                <a href="#overview" className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-black"
                        style={{ background: 'linear-gradient(135deg, #2563eb, #7c3aed)', boxShadow: '0 0 16px rgba(99,102,241,0.5)' }}>
                        AI
                    </div>
                    <span className="font-black text-white tracking-tight" style={{ fontFamily: 'Syne, Inter, sans-serif' }}>
                        IDSS <span className="gradient-text-blue">Supply Chain</span>
                    </span>
                </a>

                {/* Desktop nav */}
                <div className="hidden md:flex items-center gap-1 p-1 rounded-full"
                    style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
                    {NAV_LINKS.map(link => {
                        const isActive = active === link.href.slice(1)
                        return (
                            <a key={link.href} href={link.href} className="relative px-4 py-1.5 rounded-full text-sm font-medium transition-colors"
                                style={{ color: isActive ? '#fff' : '#94a3b8', textDecoration: 'none' }}>
                                {isActive && (
                                    <motion.span layoutId="nav-pill" className="absolute inset-0 rounded-full"
                                        style={{ background: 'rgba(139,92,246,0.25)', border: '1px solid rgba(139,92,246,0.4)' }}
                                        transition={{ type: 'spring', stiffness: 350, damping: 30 }} />
                                )}
                                <span className="relative z-10">{link.label}</span>
                            </a>
                        )
                    })}
                </div>

                {/* Grade badge + mobile burger */}
                <div className="flex items-center gap-4">
                    <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold"
                        style={{ background: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.3)', color: '#34d399' }}>
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 breathe inline-block" />
                        B+ · 78.7/100
                    </div>
                    <motion.button
                        whileHover={buttonHover} whileTap={buttonTap}
                        onClick={() => setOpen(v => !v)}
                        className="md:hidden w-9 h-9 rounded-xl flex flex-col items-center justify-center gap-1.5"
                        style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}
                        aria-label="Menu"
                    >
                        <motion.span animate={{ rotate: open ? 45 : 0, y: open ? 5 : 0 }} className="block w-5 h-px bg-white" />
                        <motion.span animate={{ opacity: open ? 0 : 1 }} className="block w-5 h-px bg-white" />
                        <motion.span animate={{ rotate: open ? -45 : 0, y: open ? -5 : 0 }} className="block w-5 h-px bg-white" />
                    </motion.button>
                </div>
            </nav>

            {/* Mobile menu */}
            <AnimatePresence>
                {open && (
                    <motion.div
                        initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.3 }}
                        className="md:hidden overflow-hidden mt-4 rounded-2xl p-4"
                        style={{ background: 'rgba(14,20,45,0.95)', border: '1px solid rgba(255,255,255,0.08)', backdropFilter: 'blur(20px)' }}
                    >
                        {NAV_LINKS.map((link, i) => (
                            <motion.a key={link.href} href={link.href}
                                initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }}
                                transition={{ delay: i * 0.05 }}
                                onClick={() => setOpen(false)}
                                className="block px-4 py-3 rounded-xl text-slate-300 font-medium transition-colors hover:text-white hover:bg-white/05"
                            >
                                {link.label}
                            </motion.a>
                        ))}
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.header>
    )
}
