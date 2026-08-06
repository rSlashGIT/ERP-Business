import { motion } from 'framer-motion'
import { useTilt } from '../../utils/animations'

/**
 * Configurable glass card with 4 depth levels, optional 3D tilt,
 * optional colored accent, optional top accent line.
 */
const DEPTHS = {
    0: { bg: 'rgba(14,20,45,0.35)', blur: 8,  border: 'rgba(255,255,255,0.04)' },
    1: { bg: 'rgba(14,20,45,0.60)', blur: 16, border: 'rgba(255,255,255,0.07)' },
    2: { bg: 'rgba(14,20,45,0.80)', blur: 24, border: 'rgba(255,255,255,0.10)' },
    3: { bg: 'rgba(20,28,60,0.92)', blur: 32, border: 'rgba(255,255,255,0.15)' },
}

export default function GlassCard({
    depth = 1,
    accent,          // hex string, tints the border + top line
    tilt = false,
    topLine = true,
    cornerGlow = false,
    className = '',
    children,
    style = {},
    ...rest
}) {
    const d = DEPTHS[depth]
    const tiltProps = useTilt(10)
    const motionStyle = tilt
        ? { rotateX: tiltProps.rotateX, rotateY: tiltProps.rotateY, transformStyle: 'preserve-3d' }
        : {}
    const handlers = tilt
        ? { onMouseMove: tiltProps.onMouseMove, onMouseLeave: tiltProps.onMouseLeave }
        : {}

    return (
        <motion.div
            ref={tilt ? tiltProps.ref : undefined}
            {...handlers}
            {...rest}
            style={{
                background: d.bg,
                backdropFilter: `blur(${d.blur}px) saturate(180%)`,
                WebkitBackdropFilter: `blur(${d.blur}px) saturate(180%)`,
                border: `1px solid ${accent ? `${accent}40` : d.border}`,
                borderRadius: 20,
                position: 'relative',
                overflow: 'hidden',
                willChange: 'transform',
                ...motionStyle,
                ...style,
            }}
            className={className}
        >
            {topLine && (
                <div
                    style={{
                        position: 'absolute', top: 0, left: 0, right: 0, height: 1,
                        background: accent
                            ? `linear-gradient(90deg, transparent, ${accent}cc, transparent)`
                            : 'linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)',
                        pointerEvents: 'none',
                    }}
                />
            )}
            {cornerGlow && accent && (
                <div
                    style={{
                        position: 'absolute', top: 0, right: 0, width: 180, height: 180,
                        background: `radial-gradient(circle at top right, ${accent}22, transparent 70%)`,
                        pointerEvents: 'none',
                    }}
                />
            )}
            {children}
        </motion.div>
    )
}
