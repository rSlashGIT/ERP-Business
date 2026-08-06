import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { chapters } from '../../data/projectData'

export default function ChapterIndicator() {
    const [active, setActive] = useState(0)

    useEffect(() => {
        const onScroll = () => {
            const viewportHalf = window.innerHeight * 0.45;
            let currentIdx = 0;
            
            for (let i = 0; i < chapters.length; i++) {
                const el = document.getElementById(chapters[i].id);
                if (!el) continue;
                const rect = el.getBoundingClientRect();
                // If the top of the section is above middle-ish or the section covers viewport
                if (rect.top <= viewportHalf) {
                    currentIdx = i;
                }
            }
            setActive(currentIdx);
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    const chapter = chapters[active]
    if (!chapter) return null

    return (
        <div className="fixed bottom-6 left-6 z-chapter hidden md:block pointer-events-none">
            <AnimatePresence mode="wait">
                <motion.div
                    key={chapter.id}
                    initial={{ opacity: 0, y: 20, filter: 'blur(8px)' }}
                    animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                    exit={{ opacity: 0, y: -10, filter: 'blur(8px)' }}
                    transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                    className="rounded-2xl px-5 py-3 max-w-sm"
                    style={{
                        background: 'rgba(14,20,45,0.75)',
                        border: '1px solid rgba(139,92,246,0.25)',
                        backdropFilter: 'blur(16px) saturate(180%)',
                        boxShadow: '0 10px 40px rgba(0,0,0,0.5), 0 0 30px rgba(139,92,246,0.1)',
                    }}
                >
                    <div className="flex items-center gap-3 mb-1">
                        <span className="text-[10px] font-mono uppercase tracking-widest text-purple-400">
                            Chapter {active + 1} of {chapters.length}
                        </span>
                        <div className="flex gap-1">
                            {chapters.map((_, i) => (
                                <span
                                    key={i}
                                    className="block h-px"
                                    style={{
                                        width: 14,
                                        background: i <= active ? '#a78bfa' : 'rgba(255,255,255,0.15)',
                                    }}
                                />
                            ))}
                        </div>
                    </div>
                    <div className="text-white font-bold text-sm">{chapter.title}</div>
                    <div className="text-slate-400 text-xs mt-0.5">{chapter.beat}</div>
                </motion.div>
            </AnimatePresence>
        </div>
    )
}
