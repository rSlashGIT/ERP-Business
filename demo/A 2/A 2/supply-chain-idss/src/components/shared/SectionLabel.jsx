export default function SectionLabel({ color = '#60a5fa', children }) {
    return (
        <span
            className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-xs font-bold uppercase tracking-widest mb-6"
            style={{
                background: `${color}15`,
                border: `1px solid ${color}30`,
                color,
                backdropFilter: 'blur(8px)',
            }}
        >
            <span
                className="w-1.5 h-1.5 rounded-full inline-block"
                style={{ background: color, boxShadow: `0 0 8px ${color}` }}
            />
            {children}
        </span>
    )
}
