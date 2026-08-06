---
name: tailwind-css
description: "Tailwind CSS utility-first CSS framework. Styling with utility classes: flexbox, grid, spacing, colors, typography, shadows, borders, responsive design, dark mode. Actions: style, add styling, make responsive, create layout, add colors. Properties: margins, padding, display, flex, grid, text, background, border, shadow, hover effects, dark mode variants."
---

# Tailwind CSS - Utility-First Styling

Production-ready CSS framework for rapidly building custom designs. Use utility classes to compose styles without leaving your HTML.

## When to Use

Use Tailwind CSS when:
- Building responsive layouts quickly
- Need consistent spacing and colors
- Creating component-based UIs
- Building dark mode interfaces
- Want customizable, modern designs
- Need utility-first approach

## Core Concepts

### Utility Classes
Instead of writing CSS, use classes:

```html
<!-- Traditional CSS -->
<style>
  .card { padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px; }
</style>
<div class="card">Card</div>

<!-- Tailwind -->
<div class="p-4 rounded-lg shadow">Card</div>
```

### Responsive Design (Mobile-First)
```html
<!-- Base: mobile, sm: 640px, md: 768px, lg: 1024px, xl: 1280px -->
<div class="w-full md:w-1/2 lg:w-1/3">
  Responsive width
</div>
```

### Dark Mode
```html
<div class="bg-white dark:bg-gray-900 text-gray-900 dark:text-white">
  Responsive to theme
</div>
```

## Essential Utilities

### Display & Layout

#### Flexbox
```html
<!-- Flex container with centering -->
<div class="flex items-center justify-center gap-4">
  <div>Item 1</div>
  <div>Item 2</div>
</div>

<!-- Column layout -->
<div class="flex flex-col gap-4">
  <div>Item 1</div>
  <div>Item 2</div>
</div>
```

#### Grid
```html
<!-- 3-column grid -->
<div class="grid grid-cols-3 gap-4">
  <div>Item 1</div>
  <div>Item 2</div>
  <div>Item 3</div>
</div>

<!-- Responsive grid -->
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
  Items
</div>
```

### Spacing

#### Padding
```html
<!-- All sides: p-4 (16px) -->
<div class="p-4">Padded</div>

<!-- Sides separately -->
<div class="px-4 py-2">Horizontal & vertical</div>

<!-- Top & bottom -->
<div class="pt-4 pb-2">Top and bottom</div>
```

#### Margin
```html
<div class="m-4">All margins</div>
<div class="mx-auto">Center horizontally</div>
<div class="mb-4">Bottom margin</div>
```

### Sizing

```html
<!-- Width -->
<div class="w-1/2">50% width</div>
<div class="w-64">16rem width</div>
<div class="w-full">100% width</div>
<div class="max-w-md">Max width</div>

<!-- Height -->
<div class="h-64">16rem height</div>
<div class="h-screen">Full screen height</div>
<div class="h-fit">Content height</div>
```

### Colors

#### Background
```html
<!-- Colors with opacity -->
<div class="bg-blue-500">Blue background</div>
<div class="bg-blue-500/50">50% opacity blue</div>

<!-- Gradients -->
<div class="bg-gradient-to-r from-blue-500 to-purple-500">
  Gradient background
</div>
```

#### Text
```html
<div class="text-gray-900">Text color</div>
<div class="text-blue-600">Blue text</div>
<div class="text-white/75">White with 75% opacity</div>
```

#### Border
```html
<div class="border border-gray-300">Border</div>
<div class="border-t-2 border-blue-500">Top border</div>
<div class="border rounded-lg border-red-500/50">Rounded border</div>
```

### Typography

```html
<!-- Size -->
<h1 class="text-4xl">Heading 1</h1>
<p class="text-base">Body text</p>
<span class="text-sm">Small text</span>

<!-- Weight -->
<div class="font-bold">Bold</div>
<div class="font-semibold">Semibold</div>
<div class="font-light">Light</div>

<!-- Style -->
<div class="italic">Italic</div>
<div class="underline">Underlined</div>
<div class="line-through">Strikethrough</div>

<!-- Alignment -->
<div class="text-center">Centered</div>
<div class="text-right">Right aligned</div>
<div class="text-justify">Justified</div>

<!-- Line Height -->
<p class="leading-relaxed">Good readability</p>
<p class="leading-tight">Compact</p>
```

### Shadows & Effects

```html
<!-- Shadow -->
<div class="shadow">Subtle shadow</div>
<div class="shadow-lg">Large shadow</div>
<div class="shadow-2xl">Extra large shadow</div>

<!-- Opacity -->
<div class="opacity-50">50% opacity</div>

<!-- Blur -->
<div class="backdrop-blur-md">Blurred background</div>
```

## Interactive States

### Hover, Focus, Active
```html
<!-- Hover state -->
<button class="bg-blue-500 hover:bg-blue-600">
  Hover me
</button>

<!-- Focus state -->
<input class="border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-200" />

<!-- Active state -->
<button class="active:scale-95">Click me</button>

<!-- Group hover -->
<div class="group">
  <div class="group-hover:text-blue-500">Revealed on hover</div>
</div>
```

## Responsive Design

### Breakpoints
```html
<!-- Mobile first approach -->
<div class="text-sm md:text-base lg:text-lg xl:text-xl">
  Responsive text
</div>

<!-- Hide on mobile, show on desktop -->
<div class="hidden md:block">Desktop only</div>
<div class="md:hidden">Mobile only</div>

<!-- Responsive grid -->
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
  <!-- 1 column on mobile, 2 on small screens, 4 on large -->
</div>
```

## Dark Mode

### Class Strategy (Recommended)
```html
<html class="dark">
  <body class="bg-white dark:bg-gray-900">
    <div class="text-gray-900 dark:text-white">
      Dark mode aware
    </div>
  </body>
</html>
```

### Configuration
```javascript
// tailwind.config.js
module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#3B82F6',
      }
    }
  }
}
```

## Common Patterns

### Card Component
```html
<div class="bg-white rounded-lg shadow-md p-6 dark:bg-gray-800">
  <h3 class="text-lg font-semibold mb-2 dark:text-white">Title</h3>
  <p class="text-gray-600 dark:text-gray-300">Description</p>
</div>
```

### Button Variants
```html
<!-- Primary -->
<button class="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
  Primary
</button>

<!-- Secondary -->
<button class="px-4 py-2 bg-gray-200 text-gray-900 rounded hover:bg-gray-300">
  Secondary
</button>

<!-- Outline -->
<button class="px-4 py-2 border border-blue-500 text-blue-500 rounded hover:bg-blue-50">
  Outline
</button>
```

### Responsive Navigation
```html
<nav class="flex items-center justify-between p-4">
  <div class="text-2xl font-bold">Logo</div>
  <div class="hidden md:flex gap-4">
    <a href="#">Home</a>
    <a href="#">About</a>
  </div>
  <button class="md:hidden">Menu</button>
</nav>
```

### Responsive Grid
```html
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
  <div class="bg-white p-4 rounded-lg shadow">Card 1</div>
  <div class="bg-white p-4 rounded-lg shadow">Card 2</div>
  <div class="bg-white p-4 rounded-lg shadow">Card 3</div>
</div>
```

### Center Content
```html
<!-- Flexbox centering -->
<div class="flex items-center justify-center h-screen">
  <div>Centered content</div>
</div>

<!-- Grid centering -->
<div class="grid place-items-center h-screen">
  <div>Centered content</div>
</div>
```

## Configuration

### Custom Colors
```javascript
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f8f9ff',
          100: '#f0f3ff',
          500: '#3b82f6',
          900: '#1e3a8a',
        }
      }
    }
  }
}
```

### Custom Spacing
```javascript
module.exports = {
  theme: {
    extend: {
      spacing: {
        '128': '32rem',
      }
    }
  }
}
```

## Best Practices

### 1. Organize Classes
```html
<!-- Group related utilities -->
<div class="
  flex items-center justify-between
  p-4 bg-white rounded-lg shadow
  dark:bg-gray-800
">
  Content
</div>
```

### 2. Use @apply for Reusable Styles
```css
/* styles.css */
@layer components {
  @apply px-4 py-2 rounded bg-blue-500 text-white;
}
```

### 3. Responsive First
```html
<!-- Mobile first -->
<div class="w-full md:w-1/2 lg:w-1/3">
  Responsive width
</div>
```

### 4. Consistent Spacing
Use the spacing scale consistently:
- `p-2`, `p-4`, `p-6`, etc. (not random values)

### 5. Dark Mode Support
Always add dark mode variants for important elements.

## Performance Tips

✓ Tailwind purges unused styles in production
✓ Only CSS for used classes is included
✓ Very small file size (10-50KB gzipped)
✓ No runtime overhead

## Useful Tools

- **Tailwind UI** - Pre-built components
- **Headless UI** - Unstyled components
- **daisyUI** - Component library on Tailwind
- **Tailwind CSS IntelliSense** - VS Code extension

## Pro Tips

✓ Use responsive prefixes (md:, lg:, etc.)
✓ Master flexbox and grid layouts
✓ Use dark mode for better UX
✓ Create consistent color palette
✓ Use @apply for component styles
✓ Extract repeated patterns to components

## Resources

- **Official Docs**: https://tailwindcss.com/docs
- **Playground**: https://play.tailwindcss.com
- **Tailwind UI**: Pre-built components library
- **Community**: Active community with examples

---

## Quick Prompts for Claude

- "Make this layout responsive with Tailwind"
- "Add Tailwind styling to this component"
- "Create a card component with Tailwind"
- "Build a responsive navigation bar"
- "Add dark mode support with Tailwind"
- "Create a button with hover effects using Tailwind"
