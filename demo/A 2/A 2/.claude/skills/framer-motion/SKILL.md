---
name: framer-motion
description: "Framer Motion animation library. Create smooth animations, transitions, gestures, drag, and complex motion sequences. Actions: animate, add motion, create transitions, smooth interactions. Elements: buttons, cards, modals, page transitions, micro-interactions, parallax, scroll animations. Properties: initial, animate, exit, whileHover, whileTap, whileDrag, transition timing, easing. Features: Variants, keyframes, SVG animations, gesture controls, layoutId for shared layout animations."
---

# Framer Motion - Advanced Animation Library

Production-ready animations and interactions for React. Create smooth, hardware-accelerated animations with simple declarative syntax.

## When to Use

Use Framer Motion for:
- Button hover and tap effects
- Page and component transitions
- Micro-interactions and feedback
- Drag and drop interactions
- Scroll-triggered animations
- Complex animation sequences
- SVG animations
- Parallax effects

## Core Concepts

### 1. Initial & Animate States
```jsx
<motion.div
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.5 }}
>
  Content
</motion.div>
```

### 2. Variants (Reusable Animation Sets)
```jsx
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1
    }
  }
};

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 }
};
```

### 3. Gesture Controls
```jsx
<motion.button
  whileHover={{ scale: 1.05 }}
  whileTap={{ scale: 0.95 }}
  onClick={() => {}}
>
  Click me
</motion.button>
```

### 4. Drag Interactions
```jsx
<motion.div
  drag
  dragConstraints={{ left: 0, right: 300 }}
  dragElastic={0.2}
  onDragEnd={(event, info) => {}}
>
  Drag me
</motion.div>
```

## Animation Properties

| Property | Use Case | Example |
|----------|----------|---------|
| `initial` | Starting state | `{ opacity: 0 }` |
| `animate` | Target state | `{ opacity: 1 }` |
| `exit` | Unmount animation | `{ opacity: 0 }` |
| `whileHover` | Mouse over effect | `{ scale: 1.1 }` |
| `whileTap` | Click effect | `{ scale: 0.95 }` |
| `whileDrag` | While dragging | `{ opacity: 0.8 }` |
| `transition` | Timing & easing | `{ duration: 0.5 }` |

## Common Animations

### Button Hover Effect
```jsx
<motion.button
  whileHover={{
    scale: 1.05,
    boxShadow: "0 8px 16px rgba(0,0,0,0.2)"
  }}
  whileTap={{ scale: 0.95 }}
  transition={{ type: "spring", stiffness: 400 }}
>
  Hover me
</motion.button>
```

### Card Entrance
```jsx
<motion.div
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.5, ease: "easeOut" }}
>
  Card content
</motion.div>
```

### Staggered List
```jsx
<motion.div
  initial="hidden"
  animate="visible"
  variants={{
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1
      }
    }
  }}
>
  {items.map(item => (
    <motion.div
      key={item.id}
      variants={{
        hidden: { opacity: 0 },
        visible: { opacity: 1 }
      }}
    >
      {item.name}
    </motion.div>
  ))}
</motion.div>
```

### Drag to Delete
```jsx
<motion.div
  drag
  dragElastic={0.2}
  onDragEnd={(event, info) => {
    if (info.offset.x > 100) {
      // Delete logic
    }
  }}
>
  Drag to delete
</motion.div>
```

### Page Transition
```jsx
<AnimatePresence mode="wait">
  <motion.div
    key={page}
    initial={{ opacity: 0, x: 100 }}
    animate={{ opacity: 1, x: 0 }}
    exit={{ opacity: 0, x: -100 }}
  >
    Page content
  </motion.div>
</AnimatePresence>
```

## Transition Types

### Spring (Physics-based)
```jsx
transition={{
  type: "spring",
  stiffness: 300,  // Bounciness (0-600)
  damping: 30,     // How quickly it settles
  mass: 1
}}
```

### Tween (Time-based)
```jsx
transition={{
  type: "tween",
  duration: 0.5,
  ease: "easeInOut"
}}
```

### Inertia (Momentum)
```jsx
transition={{
  type: "inertia",
  velocity: 50,
  power: 0.8
}}
```

## Easing Functions

- `"easeIn"` - Slow start
- `"easeOut"` - Slow end
- `"easeInOut"` - Slow start & end
- `"linear"` - Constant speed
- `"circIn"`, `"circOut"` - Circular easing
- `"backIn"`, `"backOut"` - Bounce back effect
- `"anticipate"` - Anticipatory movement

## Advanced Features

### AnimatePresence (Exit Animations)
```jsx
import { AnimatePresence } from 'framer-motion';

<AnimatePresence>
  {isVisible && (
    <motion.div
      exit={{ opacity: 0, scale: 0.8 }}
    >
      Content
    </motion.div>
  )}
</AnimatePresence>
```

### Shared Layout Animations
```jsx
<motion.div layoutId="frame">
  <motion.div layoutId="button-1">
    Button in first layout
  </motion.div>
</motion.div>

<motion.div layoutId="frame">
  <motion.div layoutId="button-1">
    Button moved to second layout
  </motion.div>
</motion.div>
```

### useMotionValue (Track Animation)
```jsx
const x = useMotionValue(0);
const opacity = useTransform(x, [0, 100], [1, 0]);

<motion.div style={{ x, opacity }}>
  Dynamic animation
</motion.div>
```

### useScroll (Scroll Animations)
```jsx
const { scrollY } = useScroll();
const opacity = useTransform(scrollY, [0, 300], [1, 0]);

<motion.div style={{ opacity }}>
  Fade on scroll
</motion.div>
```

## Best Practices

### 1. Performance
✓ Use `transform` and `opacity` (GPU-accelerated)
✗ Avoid animating `width`, `height`, `left`, `top`

```jsx
// Good
whileHover={{ scale: 1.1 }}

// Avoid
whileHover={{ width: 110 }}
```

### 2. Timing
✓ Keep animations 200-500ms for UI feedback
✓ Use spring animations for natural feel
✗ Don't make animations too slow

```jsx
// Good
transition={{ duration: 0.3 }}

// Avoid
transition={{ duration: 2 }}
```

### 3. Accessibility
```jsx
const prefersReducedMotion = useMotionTemplate();

<motion.div
  animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, y: 20 }}
>
  Respects user preferences
</motion.div>
```

### 4. Reusability
Create variant libraries for consistent animations:
```jsx
export const fadeInUpVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5 }
  }
};
```

## Common Use Cases

### Modal Pop-In
```jsx
<motion.div
  initial={{ opacity: 0, scale: 0.8 }}
  animate={{ opacity: 1, scale: 1 }}
  exit={{ opacity: 0, scale: 0.8 }}
  transition={{ type: "spring", stiffness: 300 }}
>
  Modal content
</motion.div>
```

### Slider Animation
```jsx
<motion.div
  drag="x"
  dragConstraints={{ left: -300, right: 0 }}
  dragElastic={0.2}
  onDragEnd={(event, info) => {
    // Handle slide
  }}
>
  Slide content
</motion.div>
```

### Loading Spinner
```jsx
<motion.div
  animate={{ rotate: 360 }}
  transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
>
  ⟳
</motion.div>
```

## Installation & Setup

Already installed! You have:
```bash
npm install framer-motion
```

## Pro Tips

✓ Test animations on real devices
✓ Use DevTools to inspect animations
✓ Prefer variants for complex animations
✓ Keep animations purposeful and subtle
✓ Use `AnimatePresence` for enter/exit animations
✓ Combine with Tailwind for full power

## Resources

- **Official Docs**: https://www.framer.com/motion/
- **API Reference**: https://www.framer.com/docs/
- **Examples**: Interactive examples on Framer website
- **Community**: Active Discord community for help

---

## Quick Prompts for Claude

- "Add a smooth hover animation to this button"
- "Create a staggered entrance animation for this list"
- "Add a drag-to-delete interaction"
- "Create smooth page transitions"
- "Add a loading spinner animation"
- "Create a parallax scroll effect"
