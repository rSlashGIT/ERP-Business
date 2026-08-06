import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { chapters } from '../../data/projectData'

/**
 * Floating vertical dot navigator on the right edge.
 * Highlights the active section + lets users jump around.
 */
export default function DotNav() {
    const [active, setActive] = useState('overview')

    useEffect(() => {
        const onScroll = () => {
            const viewportHalf = window.innerHeight * 0.45;
            let currentId = chapters[0].id;
            
            for (let i = 0; i < chapters.length; i++) {
                const el = document.getElementById(chapters[i].id);
                if (!el) continue;
                const rect = el.getBoundingClientRect();
                if (rect.top <= viewportHalf) {
                    currentId = chapters[i].id;
                }
            }
            setActive(currentId);
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    return (
        <div className="fixed right-6 top-1/2 -translate-y-1/2 z-dotnav hidden lg:flex flex-col gap-4">
            {chapters.map((ch, i) => {
                const isActive = ch.id === active
                return (
                    <a
                        key={ch.id}
                        href={`#${ch.id}`}
                        className="group relative flex items-center justify-end"
                        aria-label={`Jump to ${ch.title}`}
                    >
                        <AnimatePresence>
                            {isActive && (
                                <motion.span
                                    initial={{ opacity: 0, x: 10 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    exit={{ opacity: 0, x: 10 }}
                                    className="absolute right-8 px-3 py-1 rounded-full text-xs font-semibold whitespace-nowrap"
                                    style={{
                                        background: 'rgba(14,20,45,0.85)',
                                        border: '1px solid rgba(139,92,246,0.35)',
                                        backdropFilter: 'blur(12px)',
                                        color: '#c4b5fd',
                                    }}
                                >
                                    {`0${i + 1} · ${ch.title}`}
                                </motion.span>
                            )}
                        </AnimatePresence>
                        <motion.span
                            layout
                            animate={{
                                width: isActive ? 28 : 8,
                                height: 8,
                                backgroundColor: isActive ? '#a78bfa' : 'rgba(255,255,255,0.3)',
                                boxShadow: isActive ? '0 0 12px rgba(139,92,246,0.6)' : '0 0 0 transparent',
                            }}
                            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                            className="rounded-full block"
                        />
                    </a>
                )
            })}
        </div>
    )
}
