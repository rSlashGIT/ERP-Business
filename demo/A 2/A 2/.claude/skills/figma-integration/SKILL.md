---
name: figma-integration
description: "Figma design-to-code integration. Convert designs to React components, generate CSS from design tokens, extract colors and typography, create responsive layouts. Actions: convert, generate, extract, design-to-code, implement designs. Projects: website, app, dashboard, landing page. Elements: components, layouts, design tokens, color systems, typography scales. Integrations: Design tokens export, component generation, responsive design implementation."
---

# Figma Integration - Design-to-Code

Seamlessly convert Figma designs into production-ready React components and CSS. Extract design tokens, colors, typography, and spacing directly from your Figma files.

## When to Use

Use Figma designs when:
- Converting design mockups to code
- Extracting design tokens and color systems
- Building responsive layouts from designs
- Implementing typography systems
- Creating consistent spacing/padding systems
- Converting design systems to code

## Workflow: From Figma to Code

### 1. Extract Design Tokens
When you have a Figma design, extract:
- **Colors** - Primary, secondary, accent, neutral palette
- **Typography** - Font families, sizes, weights, line heights
- **Spacing** - Padding, margin, gap units
- **Shadows** - Drop shadows, elevation system
- **Radius** - Border radius scale
- **Breakpoints** - Responsive design breakpoints

### 2. Generate Components
From Figma designs, generate:
- React component structure
- TypeScript interfaces for props
- Tailwind CSS classes
- Responsive variants
- Accessibility attributes
- Storybook stories

### 3. Implement in Code
Apply designs by:
- Creating component files matching design structure
- Using extracted design tokens
- Implementing responsive layouts
- Adding interactivity with Framer Motion
- Testing with Vitest

## How to Use in Claude

### Method 1: Share Design Link
```
"Convert this Figma design to React: [figma-link]"
"Generate components from: [figma-link]"
"Extract design tokens from: [figma-link]"
```

### Method 2: Describe Design
```
"I have a Figma design with [description], convert it to React"
"Build components based on my Figma design system"
"Extract colors and fonts from my Figma design"
```

### Method 3: Export from Figma & Share Code
1. Export components or frames from Figma
2. Share the design structure/description
3. Ask Claude to generate React code

## Design Token Extraction

### Colors
```javascript
// Extract from Figma color styles
const colors = {
  primary: '#3B82F6',      // Blue
  secondary: '#8B5CF6',    // Purple
  success: '#10B981',      // Green
  warning: '#F59E0B',      // Amber
  error: '#EF4444',        // Red
  neutral: {
    50: '#F9FAFB',
    100: '#F3F4F6',
    200: '#E5E7EB',
    ...
  }
}
```

### Typography
```javascript
// Extract from Figma text styles
const typography = {
  heading1: {
    fontFamily: 'Inter',
    fontSize: '32px',
    fontWeight: '700',
    lineHeight: '1.2'
  },
  body: {
    fontFamily: 'Inter',
    fontSize: '16px',
    fontWeight: '400',
    lineHeight: '1.5'
  }
}
```

### Spacing
```javascript
// Extract spacing system
const spacing = {
  xs: '4px',
  sm: '8px',
  md: '16px',
  lg: '24px',
  xl: '32px',
  '2xl': '48px'
}
```

## Component Generation Pattern

### From Figma Frame → React Component

```typescript
// Figma: Button Frame
// → Generate:

interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'outline';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  children: React.ReactNode;
  onClick?: () => void;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  disabled = false,
  children,
  onClick
}) => {
  // Implementation using Tailwind + design tokens
}
```

## Best Practices

### 1. Component Hierarchy
- Match Figma component hierarchy in code
- Use composition for variants
- Keep components DRY

### 2. Design Tokens
- Use CSS variables or JS objects
- Maintain consistency across project
- Version your design tokens

### 3. Responsive Design
- Extract breakpoints from Figma
- Implement mobile-first approach
- Test on actual devices

### 4. Accessibility
- Ensure color contrast (WCAG AA minimum)
- Include ARIA labels in exported components
- Test keyboard navigation

### 5. Reusability
- Create base components first
- Build complex components from base
- Document component APIs in Storybook

## Figma Best Practices for Code Export

### File Organization
- Organize components in logical groups
- Use clear naming conventions
- Document component purpose

### Component Setup
- Create main component with variants
- Use component sets for related items
- Add documentation in Figma

### Design Token Management
- Create separate design token file in Figma
- Use consistent naming (semantic names)
- Version your design system

## Tools for Figma Export

### Automated Export
- **Figma Tokens Studio** - Export design tokens as JSON
- **Penpot** - Open-source Figma alternative
- **Plasmic** - Design-to-code platform

### Manual Export
- Export designs as SVG/PNG
- Export design system documentation
- Share Figma file link for reference

## Integration with Other Tools

### + Tailwind CSS
Use Figma colors/spacing with Tailwind config:
```javascript
// tailwind.config.js
module.exports = {
  theme: {
    colors: { /* from Figma */ },
    spacing: { /* from Figma */ }
  }
}
```

### + TypeScript
Strong typing for design token usage:
```typescript
type ColorToken = keyof typeof colors;
type SpacingToken = keyof typeof spacing;
```

### + Storybook
Document components with Storybook stories:
```typescript
export default {
  title: 'Components/Button',
  component: Button,
  argTypes: { /* from Figma */ }
}
```

### + Framer Motion
Add animations to Figma-designed components:
```typescript
<motion.button
  whileHover={{ scale: 1.05 }}
  whileTap={{ scale: 0.95 }}
>
  Click me
</motion.button>
```

## Quick Workflow

1. **Design in Figma** → Create components and design system
2. **Extract Tokens** → Export colors, typography, spacing
3. **Generate Components** → Create React files with structure
4. **Implement Design** → Add styling and interactions
5. **Test & Deploy** → Verify responsive design and accessibility

## Resources

- **Figma Plugins**: Browse plugin marketplace for export tools
- **Design Tokens**: Learn about design token standards
- **Component Documentation**: Use Storybook for component docs
- **Accessibility**: WCAG guidelines for color contrast and interactions

---

## Pro Tips

✓ Keep Figma file as source of truth
✓ Version both design and code together
✓ Use design tokens for consistency
✓ Automate exports where possible
✓ Document component decisions
✓ Test accessibility during implementation
