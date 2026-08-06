---
name: vitest-testing
description: "Vitest testing framework. Unit tests, component tests, integration tests. Testing utilities: describe, it, expect, beforeEach, afterEach. Matchers: toBe, toEqual, toContain, toThrow. React Testing: render, fireEvent, waitFor, userEvent. Setup and configuration for fast testing."
---

# Vitest - Lightning Fast Unit Testing

Blazing fast unit testing framework powered by Vite. Perfect for testing React components, functions, and integrations.

## When to Use

Use Vitest when:
- Writing unit tests for functions
- Testing React components
- Testing async code
- Need fast, modern testing
- Want Vite integration
- Building production-grade apps

## Why Vitest?

✓ Super fast (10-100x faster than Jest)
✓ Vite-powered (instant feedback)
✓ Jest-compatible API
✓ Native ESM support
✓ Built-in code coverage
✓ Great debugging experience
✓ Minimal configuration

## Installation

Already installed! You have:
```bash
npm install vitest @testing-library/react @testing-library/jest-dom -D
```

## Basic Test Structure

### Simple Function Test
```javascript
import { describe, it, expect } from 'vitest';
import { add } from './math';

describe('Math utilities', () => {
  it('should add two numbers', () => {
    expect(add(2, 3)).toBe(5);
  });

  it('should handle negative numbers', () => {
    expect(add(-2, -3)).toBe(-5);
  });
});
```

### Component Test
```javascript
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Button } from './Button';

describe('Button component', () => {
  it('should render button with text', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByText('Click me')).toBeInTheDocument();
  });

  it('should call onClick when clicked', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Click</Button>);
    screen.getByRole('button').click();
    expect(handleClick).toHaveBeenCalled();
  });
});
```

## Common Matchers

### Equality
```javascript
expect(value).toBe(expected);           // ===
expect(value).toEqual(expected);        // Deep equality
expect(value).toStrictEqual(expected);  // Strict deep equality
```

### Truthiness
```javascript
expect(value).toBeTruthy();   // Truthy value
expect(value).toBeFalsy();    // Falsy value
expect(value).toBeNull();     // null
expect(value).toBeUndefined();// undefined
expect(value).toBeDefined();  // Not undefined
```

### Numbers
```javascript
expect(value).toBeGreaterThan(5);
expect(value).toBeGreaterThanOrEqual(5);
expect(value).toBeLessThan(5);
expect(value).toBeLessThanOrEqual(5);
```

### Strings
```javascript
expect(text).toContain('substring');
expect(text).toMatch(/regex/);
expect(text).toMatch('exact string');
```

### Arrays & Objects
```javascript
expect([1, 2, 3]).toContain(2);
expect([1, 2, 3]).toHaveLength(3);
expect({ a: 1 }).toHaveProperty('a');
expect({ a: 1 }).toHaveProperty('a', 1);
```

### Exceptions
```javascript
expect(() => throwFunction()).toThrow();
expect(() => throwFunction()).toThrow(Error);
expect(() => throwFunction()).toThrow('message');
```

### Functions
```javascript
expect(mockFn).toHaveBeenCalled();
expect(mockFn).toHaveBeenCalledWith(arg1, arg2);
expect(mockFn).toHaveBeenCalledTimes(2);
expect(mockFn).toHaveReturnedWith(value);
```

## Test Hooks

### Setup & Teardown
```javascript
describe('User operations', () => {
  let user;

  // Before each test
  beforeEach(() => {
    user = { id: 1, name: 'John' };
  });

  // After each test
  afterEach(() => {
    user = null;
  });

  // Before all tests
  beforeAll(() => {
    console.log('Setup once');
  });

  // After all tests
  afterAll(() => {
    console.log('Cleanup once');
  });

  it('should have user', () => {
    expect(user).toBeDefined();
  });
});
```

## React Component Testing

### Rendering Components
```javascript
import { render, screen } from '@testing-library/react';
import { Button } from './Button';

describe('Button', () => {
  it('should render', () => {
    render(<Button>Click</Button>);
    // Component is now in DOM
  });

  it('should find elements', () => {
    render(<Button>Click me</Button>);

    // By text
    screen.getByText('Click me');

    // By role
    screen.getByRole('button');

    // By label
    screen.getByLabelText('Username');

    // By placeholder
    screen.getByPlaceholderText('Enter name');
  });
});
```

### User Interactions
```javascript
import userEvent from '@testing-library/user-event';

describe('Form', () => {
  it('should handle form submission', async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn();
    render(<Form onSubmit={handleSubmit} />);

    // Type in input
    await user.type(screen.getByPlaceholderText('Email'), 'test@example.com');

    // Click button
    await user.click(screen.getByRole('button', { name: /submit/i }));

    expect(handleSubmit).toHaveBeenCalled();
  });

  it('should handle click', async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Click</Button>);

    await user.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalled();
  });
});
```

### Async Testing
```javascript
import { waitFor } from '@testing-library/react';

describe('Async component', () => {
  it('should load data', async () => {
    render(<DataComponent />);

    // Wait for element to appear
    const element = await screen.findByText('Loaded');
    expect(element).toBeInTheDocument();
  });

  it('should handle loading state', async () => {
    render(<DataComponent />);

    // Wait for condition
    await waitFor(() => {
      expect(screen.getByText('Data')).toBeInTheDocument();
    });
  });
});
```

## Mocking

### Mock Functions
```javascript
import { vi } from 'vitest';

const mockFn = vi.fn();
mockFn('arg');

expect(mockFn).toHaveBeenCalledWith('arg');
expect(mockFn).toHaveBeenCalledTimes(1);
```

### Mock Return Values
```javascript
const mockFn = vi.fn()
  .mockReturnValue(42)
  .mockReturnValueOnce(100); // First call returns 100

expect(mockFn()).toBe(100);  // First call
expect(mockFn()).toBe(42);   // Subsequent calls
```

### Mock Modules
```javascript
// Original: api.js exports { fetchUser }
vi.mock('./api', () => ({
  fetchUser: vi.fn(() => Promise.resolve({ id: 1, name: 'John' }))
}));

import { fetchUser } from './api';

it('should fetch user', async () => {
  const user = await fetchUser();
  expect(user).toEqual({ id: 1, name: 'John' });
});
```

### Mock Implementations
```javascript
const mockFn = vi.fn((x) => x * 2);
expect(mockFn(5)).toBe(10);

// Change implementation
mockFn.mockImplementation((x) => x * 3);
expect(mockFn(5)).toBe(15);
```

## Test Examples

### Button Component
```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from './Button';

describe('Button', () => {
  it('should render with text', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole('button')).toHaveTextContent('Click me');
  });

  it('should call onClick', async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Click</Button>);

    await user.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('should be disabled when disabled prop is true', () => {
    render(<Button disabled>Click</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('should show loading state', () => {
    render(<Button isLoading>Loading</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });
});
```

### Form Component
```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoginForm } from './LoginForm';

describe('LoginForm', () => {
  it('should submit form with values', async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn();
    render(<LoginForm onSubmit={handleSubmit} />);

    await user.type(screen.getByPlaceholderText('Email'), 'test@example.com');
    await user.type(screen.getByPlaceholderText('Password'), 'password123');
    await user.click(screen.getByRole('button', { name: /login/i }));

    expect(handleSubmit).toHaveBeenCalledWith({
      email: 'test@example.com',
      password: 'password123'
    });
  });

  it('should show validation errors', async () => {
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.click(screen.getByRole('button', { name: /login/i }));

    expect(screen.getByText(/email required/i)).toBeInTheDocument();
    expect(screen.getByText(/password required/i)).toBeInTheDocument();
  });
});
```

### API Mocking
```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { DataComponent } from './DataComponent';

vi.mock('./api', () => ({
  fetchData: vi.fn(() => Promise.resolve([
    { id: 1, name: 'Item 1' },
    { id: 2, name: 'Item 2' }
  ]))
}));

describe('DataComponent', () => {
  it('should display data after loading', async () => {
    render(<DataComponent />);

    expect(screen.getByText(/loading/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Item 1')).toBeInTheDocument();
      expect(screen.getByText('Item 2')).toBeInTheDocument();
    });
  });
});
```

## Running Tests

### CLI Commands
```bash
# Run all tests
npm test

# Watch mode (rerun on changes)
npm test -- --watch

# Run specific file
npm test -- button.test.js

# Coverage report
npm test -- --coverage

# UI mode (visual interface)
npm test -- --ui
```

### Configuration (vitest.config.js)
```javascript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './test/setup.js'
  }
});
```

## Best Practices

✓ Test behavior, not implementation
✓ Use descriptive test names
✓ Keep tests focused and simple
✓ Mock external dependencies
✓ Test error cases
✓ Use beforeEach for setup
✓ Test user interactions
✓ Aim for good coverage (>80%)

## Pro Tips

✓ Use `userEvent` instead of `fireEvent`
✓ Test accessibility (roles, labels)
✓ Mock API calls, not implementations
✓ Use `findBy` for async queries
✓ Keep tests independent
✓ Use `vi.mock()` for module mocking

## Resources

- **Vitest Docs**: https://vitest.dev/
- **Testing Library**: https://testing-library.com/
- **Best Practices**: Learn from testing examples
- **Community**: Discord and GitHub discussions

---

## Quick Prompts for Claude

- "Write tests for this component"
- "Create unit tests for this function"
- "Test this async operation"
- "Add form validation tests"
- "Create integration tests"
- "Mock this API call in tests"
