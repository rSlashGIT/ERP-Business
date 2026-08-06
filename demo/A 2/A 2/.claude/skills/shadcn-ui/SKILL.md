---
name: shadcn-ui
description: "shadcn/ui component library. Copy-paste React components built on Radix UI and Tailwind CSS. Components: Button, Card, Dialog, Dropdown, Form, Input, Label, Select, Textarea, Toggle, Popover, Tabs, Tooltip, Sheet, Sidebar, Scroll Area, Combobox, Data Table, Calendar, Command Palette, Sonner Toast."
---

# shadcn/ui - Premium React Components

Beautiful, accessible React components built on Radix UI and styled with Tailwind CSS. Copy-paste components that you own completely.

## When to Use

Use shadcn/ui components when:
- Building professional web applications
- Need accessible, unstyled components
- Want complete control over styling
- Building dashboards and admin panels
- Creating form-heavy applications
- Need high-quality, tested components

## Available Components

### Forms & Input
- **Button** - Base button component with variants
- **Input** - Text input with validation states
- **Label** - Form labels with accessibility
- **Textarea** - Multi-line text input
- **Select** - Dropdown select component
- **Checkbox** - Checkbox with variants
- **Radio Group** - Radio button groups
- **Toggle** - Toggle switch component
- **Form** - Form wrapper with validation

### Containers & Layouts
- **Card** - Container with flexible content
- **Dialog** - Modal dialog component
- **Sheet** - Side drawer component
- **Popover** - Floating popover menu
- **Dropdown Menu** - Dropdown with keyboard nav
- **Sidebar** - Application sidebar layout
- **Tabs** - Tab navigation component
- **Scroll Area** - Custom scrollbar wrapper

### Data Display
- **Table** - Data table with sorting/filtering
- **Badge** - Status and category badges
- **Tooltip** - Hover tooltips
- **Progress** - Progress bars and indicators
- **Alert** - Alert messages and callouts
- **Calendar** - Date picker calendar

### Advanced
- **Combobox** - Searchable select component
- **Command** - Command palette/command menu
- **Date Picker** - Date selection component
- **Sonner Toast** - Toast notifications

## Installation

### Install shadcn/ui CLI
```bash
npx shadcn-ui@latest init
```

### Then add components as needed
```bash
npx shadcn-ui@latest add button
npx shadcn-ui@latest add card
npx shadcn-ui@latest add form
# Add more as needed
```

## Core Components Usage

### Button
```jsx
import { Button } from "@/components/ui/button"

export function Demo() {
  return (
    <div className="flex gap-2">
      <Button>Default</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="destructive">Delete</Button>
      <Button variant="outline">Outline</Button>
      <Button variant="ghost">Ghost</Button>
    </div>
  )
}
```

### Card
```jsx
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

export function Demo() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Card Title</CardTitle>
        <CardDescription>Card description</CardDescription>
      </CardHeader>
      <CardContent>
        Card content goes here
      </CardContent>
    </Card>
  )
}
```

### Form
```jsx
import { useForm } from "react-hook-form"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage } from "@/components/ui/form"

export function Demo() {
  const form = useForm({ defaultValues: { email: "" } })

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input placeholder="Enter email" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Submit</Button>
      </form>
    </Form>
  )
}
```

### Dialog
```jsx
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"

export function Demo() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button>Open Dialog</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dialog Title</DialogTitle>
          <DialogDescription>Dialog description</DialogDescription>
        </DialogHeader>
        Dialog content here
      </DialogContent>
    </Dialog>
  )
}
```

### Dropdown Menu
```jsx
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Button } from "@/components/ui/button"

export function Demo() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">Menu</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Profile</DropdownMenuItem>
        <DropdownMenuItem>Settings</DropdownMenuItem>
        <DropdownMenuItem>Logout</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
```

### Data Table
```jsx
import { DataTable } from "@/components/ui/data-table"

const columns = [
  {
    accessorKey: "email",
    header: "Email",
  },
  {
    accessorKey: "amount",
    header: "Amount",
  }
]

export function Demo() {
  return <DataTable columns={columns} data={data} />
}
```

### Tabs
```jsx
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export function Demo() {
  return (
    <Tabs defaultValue="tab1">
      <TabsList>
        <TabsTrigger value="tab1">Tab 1</TabsTrigger>
        <TabsTrigger value="tab2">Tab 2</TabsTrigger>
      </TabsList>
      <TabsContent value="tab1">Content 1</TabsContent>
      <TabsContent value="tab2">Content 2</TabsContent>
    </Tabs>
  )
}
```

### Combobox (Searchable Select)
```jsx
import { Combobox } from "@/components/ui/combobox"

const frameworks = [
  { value: "react", label: "React" },
  { value: "vue", label: "Vue" },
  { value: "next", label: "Next.js" }
]

export function Demo() {
  return <Combobox items={frameworks} />
}
```

## Component Variants

Most shadcn/ui components have variants:

### Button Variants
- `default` - Primary button
- `secondary` - Secondary button
- `destructive` - Red danger button
- `outline` - Outlined button
- `ghost` - Minimal button
- `link` - Link-styled button

### Button Sizes
- `default` - Medium
- `sm` - Small
- `lg` - Large
- `icon` - Square icon button

## Styling & Customization

### Tailwind Configuration
shadcn/ui uses CSS variables for theming:

```css
@layer base {
  :root {
    --primary: 222.2 47.6% 11.2%;
    --primary-foreground: 210 40% 98%;
    --secondary: 217.2 91.2% 59.8%;
    --secondary-foreground: 222.2 47.6% 11.2%;
    /* More colors... */
  }
}
```

### Custom Theming
Modify `globals.css` to change colors across all components.

## Integration with Other Tools

### + Framer Motion
Add animations to shadcn/ui components:
```jsx
import { Button } from "@/components/ui/button"
import { motion } from "framer-motion"

const MotionButton = motion(Button)

<MotionButton whileHover={{ scale: 1.05 }} />
```

### + React Hook Form
Form validation with shadcn/ui:
```jsx
import { Form, FormField } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useForm } from "react-hook-form"
```

### + Tailwind CSS
Fully integrated with Tailwind for styling.

### + TypeScript
Full TypeScript support with proper types.

## Best Practices

### 1. Import Only What You Need
```jsx
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
```

### 2. Use Semantic HTML
shadcn/ui components are built with proper HTML semantics.

### 3. Accessibility First
All components are accessible by default (WCAG compliant).

### 4. Responsive Design
Components work great on all screen sizes.

### 5. Composition Over Complexity
Combine simple components to build complex UIs:
```jsx
<Card>
  <CardHeader>
    <CardTitle>Title</CardTitle>
  </CardHeader>
  <CardContent>
    <Form>
      <FormField>
        <Input />
      </FormField>
      <Button>Submit</Button>
    </Form>
  </CardContent>
</Card>
```

## Common Patterns

### Loading State
```jsx
<Button disabled>
  {isLoading ? "Loading..." : "Submit"}
</Button>
```

### Form with Validation
```jsx
<Form>
  <FormField name="email">
    <Input type="email" required />
  </FormField>
  <Button type="submit">Submit</Button>
</Form>
```

### Data Table
```jsx
<DataTable
  columns={columns}
  data={data}
  onRowClick={handleRowClick}
/>
```

### Modal Confirmation
```jsx
<Dialog>
  <DialogTrigger>Delete</DialogTrigger>
  <DialogContent>
    <DialogTitle>Confirm Delete?</DialogTitle>
    <Button onClick={handleDelete}>Delete</Button>
  </DialogContent>
</Dialog>
```

## Quick Setup

1. **Initialize** - `npx shadcn-ui@latest init`
2. **Add Components** - `npx shadcn-ui@latest add [component]`
3. **Use** - Import and use in your components
4. **Customize** - Modify colors and styling in CSS

## Pro Tips

✓ Start with basic components (Button, Card, Input)
✓ Use variants for different states
✓ Combine components for complex UIs
✓ Customize via Tailwind/CSS variables
✓ Check documentation for each component
✓ Review accessibility features in docs

## Resources

- **Official Site**: https://ui.shadcn.com/
- **Component Docs**: Full documentation for each component
- **GitHub**: https://github.com/shadcn-ui/ui
- **Examples**: Real-world examples on the site

---

## Quick Prompts for Claude

- "Add a shadcn/ui button to this component"
- "Create a form with shadcn/ui components"
- "Build a data table with shadcn/ui"
- "Add a dialog/modal using shadcn/ui"
- "Create a responsive card layout with shadcn/ui"
- "Build an admin dashboard with shadcn/ui components"
