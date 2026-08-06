import { motion, useScroll, useSpring } from 'framer-motion'
import Navbar from './components/Navbar'
import Hero from './components/Hero'
import ProblemSection from './components/ProblemSection'
import ArchitectureSection from './components/ArchitectureSection'
import ResultsSection from './components/ResultsSection'
import DemoSection from './components/DemoSection'
import PolicyPlaygroundSection from './components/PolicySeed'
import ExplainabilitySection from './components/ExplainabilitySection'
import TechStackSection from './components/TechStackSection'
import Footer from './components/Footer'
import MarqueeStrip from './components/shared/MarqueeStrip'
import DotNav from './components/shared/DotNav'
import ChapterIndicator from './components/shared/ChapterIndicator'
import CursorFollower from './components/shared/CursorFollower'

const MARQUEE_CHAIN = [
    { icon: '📦', label: 'Inventory' }, { icon: '🚚', label: 'Logistics' },
    { icon: '🏭', label: 'Warehouse' }, { icon: '📊', label: 'Demand Forecast' },
    { icon: '💰', label: 'Cost Control' }, { icon: '🔄', label: 'Replenishment' },
    { icon: '⚖️', label: 'Balance' }, { icon: '📈', label: 'Service Level' },
]
const MARQUEE_CODE = [
    { icon: '🧠', label: 'DQN Policy' }, { icon: '⚙️', label: 'Tactical Net' },
    { icon: '🎯', label: 'Q-Values' }, { icon: '🔥', label: 'PyTorch' },
    { icon: '🐍', label: 'Python' }, { icon: '📡', label: 'State Space' },
    { icon: '⚡', label: 'Reward Fn' }, { icon: '🌡️', label: 'ε Decay' },
]
const MARQUEE_TECH = [
    { icon: '🐼', label: 'Pandas' }, { icon: '🔢', label: 'NumPy' },
    { icon: '🤖', label: 'Scikit-learn' }, { icon: '🔍', label: 'SHAP' },
    { icon: '📋', label: 'M5 Dataset' }, { icon: '⚛️', label: 'React 18' },
    { icon: '🌊', label: 'Framer Motion' }, { icon: '📉', label: 'Recharts' },
]

function ScrollProgressBar() {
    const { scrollYProgress } = useScroll()
    const scaleX = useSpring(scrollYProgress, { stiffness: 200, damping: 30, restDelta: 0.001 })
    return (
        <motion.div style={{ scaleX, transformOrigin: '0%',
            background: 'linear-gradient(90deg, #3b82f6, #8b5cf6, #34d399)',
            boxShadow: '0 0 8px rgba(139,92,246,0.6)' }}
            className="fixed top-0 left-0 right-0 z-[9999] h-[3px]" />
    )
}

export default function App() {
    return (
        <>
            <ScrollProgressBar />
            <CursorFollower />
            <DotNav />
            <ChapterIndicator />

            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                transition={{ duration: 0.5 }} className="min-h-screen">
                <Navbar />
                <Hero />

                <MarqueeStrip items={MARQUEE_CHAIN} direction="left" tilt={-1.5} accent="#3b82f6" />

                <ProblemSection />

                <MarqueeStrip items={MARQUEE_CODE} direction="right" tilt={1.5} accent="#8b5cf6" />

                <ArchitectureSection />

                <MarqueeStrip items={MARQUEE_CHAIN} direction="left" tilt={-1} accent="#34d399" />

                <ResultsSection />

                <DemoSection />

                <PolicyPlaygroundSection />

                <ExplainabilitySection />

                <MarqueeStrip items={MARQUEE_TECH} direction="right" tilt={1} accent="#f59e0b" />

                <TechStackSection />
                <Footer />
            </motion.div>
        </>
    )
}
