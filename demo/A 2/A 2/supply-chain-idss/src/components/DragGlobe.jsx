import { useEffect, useRef, useState } from 'react'
import createGlobe from 'cobe'

/**
 * Interactive draggable globe showing supply chain nodes & routes.
 * Uses cobe (tiny canvas globe library).
 *
 * Nodes are real supply chain hubs (as a rough illustration).
 * Color pulses based on the active strategic mode.
 */

// Major supply chain hubs [lat, lng, size]
const NODES = [
    { location: [37.7749,  -122.4194], size: 0.08, label: 'San Francisco DC'  }, // SF
    { location: [40.7128,   -74.0060], size: 0.10, label: 'NY Distribution'   }, // NYC
    { location: [51.5074,    -0.1278], size: 0.09, label: 'London Hub'        }, // London
    { location: [48.8566,     2.3522], size: 0.07, label: 'Paris Warehouse'   }, // Paris
    { location: [52.5200,    13.4050], size: 0.07, label: 'Berlin Facility'   }, // Berlin
    { location: [55.7558,    37.6173], size: 0.06, label: 'Moscow Node'       }, // Moscow
    { location: [35.6762,   139.6503], size: 0.10, label: 'Tokyo Mega-DC'     }, // Tokyo
    { location: [31.2304,   121.4737], size: 0.11, label: 'Shanghai Port'     }, // Shanghai
    { location: [22.3193,   114.1694], size: 0.08, label: 'Hong Kong Depot'   }, // HK
    { location: [1.3521,    103.8198], size: 0.08, label: 'Singapore Cross'   }, // SGP
    { location: [19.0760,    72.8777], size: 0.07, label: 'Mumbai Branch'     }, // Mumbai
    { location: [-33.8688,  151.2093], size: 0.07, label: 'Sydney Outpost'    }, // Sydney
    { location: [-23.5505,  -46.6333], size: 0.07, label: 'Sao Paulo Hub'     }, // SP
    { location: [19.4326,   -99.1332], size: 0.06, label: 'Mexico City'       }, // CDMX
    { location: [25.276987, 55.296249], size: 0.06, label: 'Dubai Logistics'  }, // Dubai
]

// Mode -> marker color (matches strategicModes palette)
const MODE_COLORS = {
    0: [0.38, 0.65, 0.98],  // Conservative blue
    1: [0.66, 0.55, 0.98],  // Balanced purple
    2: [0.96, 0.62, 0.04],  // Aggressive orange
}

export default function DragGlobe({ mode = 1, size = 560 }) {
    const canvasRef = useRef(null)
    const pointerInteracting = useRef(null)
    const pointerInteractionMovement = useRef(0)
    const [r, setR] = useState(0)

    useEffect(() => {
        let phi = 0
        let width = size
        const markerColor = MODE_COLORS[mode] || MODE_COLORS[1]

        const onResize = () => {
            if (canvasRef.current) width = canvasRef.current.offsetWidth
        }
        window.addEventListener('resize', onResize)
        onResize()

        const globe = createGlobe(canvasRef.current, {
            devicePixelRatio: 2,
            width: size * 2,
            height: size * 2,
            phi: 0,
            theta: 0.3,
            dark: 1,
            diffuse: 1.2,
            mapSamples: 16000,
            mapBrightness: 6,
            baseColor: [0.08, 0.1, 0.18],
            markerColor,
            glowColor: [markerColor[0] * 0.6, markerColor[1] * 0.6, markerColor[2] * 0.8],
            markers: NODES.map(n => ({ location: n.location, size: n.size })),
            onRender: (state) => {
                // auto-rotate if user is not dragging
                if (!pointerInteracting.current) phi += 0.004
                state.phi = phi + r
                state.width = size * 2
                state.height = size * 2
            },
        })

        return () => {
            globe.destroy()
            window.removeEventListener('resize', onResize)
        }
    }, [mode, size, r])

    return (
        <div
            className="relative"
            style={{
                width: '100%', maxWidth: size, aspectRatio: '1 / 1',
                margin: '0 auto',
            }}
        >
            {/* Atmospheric glow behind the globe */}
            <div
                aria-hidden
                className="absolute inset-0 pointer-events-none"
                style={{
                    background:
                        'radial-gradient(circle at 50% 50%, rgba(99,102,241,0.25) 0%, rgba(139,92,246,0.12) 30%, transparent 65%)',
                    filter: 'blur(40px)',
                }}
            />
            <canvas
                ref={canvasRef}
                style={{
                    width: '100%',
                    height: '100%',
                    contain: 'layout paint size',
                    cursor: 'grab',
                    opacity: 0,
                    transition: 'opacity 1.5s ease',
                }}
                onLoad={e => { e.target.style.opacity = 1 }}
                onPointerDown={e => {
                    pointerInteracting.current = e.clientX - pointerInteractionMovement.current
                    e.currentTarget.style.cursor = 'grabbing'
                }}
                onPointerUp={() => {
                    pointerInteracting.current = null
                    if (canvasRef.current) canvasRef.current.style.cursor = 'grab'
                }}
                onPointerOut={() => {
                    pointerInteracting.current = null
                    if (canvasRef.current) canvasRef.current.style.cursor = 'grab'
                }}
                onMouseMove={e => {
                    if (pointerInteracting.current !== null) {
                        const delta = e.clientX - pointerInteracting.current
                        pointerInteractionMovement.current = delta
                        setR(delta / 200)
                    }
                }}
                onTouchMove={e => {
                    if (pointerInteracting.current !== null && e.touches[0]) {
                        const delta = e.touches[0].clientX - pointerInteracting.current
                        pointerInteractionMovement.current = delta
                        setR(delta / 100)
                    }
                }}
            />
            {/* Fake opacity unlock since onLoad on canvas doesn't fire */}
            <style>{`canvas { opacity: 1 !important; }`}</style>
        </div>
    )
}
