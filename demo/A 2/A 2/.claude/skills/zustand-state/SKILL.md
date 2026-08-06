---
name: zustand-state
description: "Zustand state management library. Create stores, manage global state, actions, async operations, middleware. Simple hooks-based state management. Actions: create store, manage state, add actions, async handling, persist state."
---

# Zustand - Lightweight State Management

Simple, unopinionated state management for React. Create stores with hooks in just a few lines of code.

## When to Use

Use Zustand when:
- Managing global application state
- Sharing state across components
- Need simple, performant state management
- Want to avoid prop drilling
- Building medium to large apps
- Need lightweight alternative to Redux

## Why Zustand?

✓ Minimal boilerplate
✓ Hooks-based API
✓ Tiny bundle size (~2KB)
✓ No providers needed
✓ TypeScript support
✓ Middleware support
✓ DevTools integration

## Basic Store

### Simple Counter
```javascript
import { create } from 'zustand';

const useCountStore = create((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
  decrement: () => set((state) => ({ count: state.count - 1 })),
  reset: () => set({ count: 0 })
}));

// Usage
function Counter() {
  const { count, increment, decrement } = useCountStore();

  return (
    <div>
      <p>Count: {count}</p>
      <button onClick={increment}>+</button>
      <button onClick={decrement}>-</button>
    </div>
  );
}
```

### User Store
```javascript
const useUserStore = create((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  logout: () => set({ user: null }),
  updateProfile: (profile) => set((state) => ({
    user: { ...state.user, ...profile }
  }))
}));

// Usage
function Profile() {
  const { user, updateProfile } = useUserStore();

  return (
    <div>
      <h1>{user?.name}</h1>
      <button onClick={() => updateProfile({ name: 'John' })}>
        Update Name
      </button>
    </div>
  );
}
```

## Store Patterns

### Separate State & Actions
```javascript
const useAuthStore = create((set) => ({
  // State
  isAuthenticated: false,
  user: null,
  isLoading: false,

  // Actions
  login: async (email, password) => {
    set({ isLoading: true });
    try {
      const response = await api.login(email, password);
      set({
        isAuthenticated: true,
        user: response.user,
        isLoading: false
      });
    } catch (error) {
      set({ isLoading: false });
    }
  },

  logout: () => set({
    isAuthenticated: false,
    user: null
  })
}));
```

### Multiple Stores
```javascript
// Auth Store
const useAuthStore = create((set) => ({
  user: null,
  setUser: (user) => set({ user })
}));

// Todo Store
const useTodoStore = create((set) => ({
  todos: [],
  addTodo: (todo) => set((state) => ({
    todos: [...state.todos, todo]
  }))
}));

// Usage in component
function App() {
  const user = useAuthStore((state) => state.user);
  const todos = useTodoStore((state) => state.todos);

  return (
    <div>
      <h1>Welcome, {user?.name}</h1>
      <TodoList todos={todos} />
    </div>
  );
}
```

## Advanced Features

### Selectors (Optimize Re-renders)
```javascript
const useCountStore = create((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 }))
}));

// Only re-render when count changes
function Counter() {
  const count = useCountStore((state) => state.count);
  return <div>Count: {count}</div>;
}

// Entire store subscribes
function CounterDebug() {
  const store = useCountStore();
  return <div>{JSON.stringify(store)}</div>;
}
```

### Async Operations
```javascript
const useDataStore = create((set) => ({
  data: [],
  isLoading: false,
  error: null,

  fetchData: async () => {
    set({ isLoading: true });
    try {
      const response = await fetch('/api/data');
      const data = await response.json();
      set({ data, isLoading: false });
    } catch (error) {
      set({ error: error.message, isLoading: false });
    }
  }
}));

// Usage
function DataComponent() {
  const { data, isLoading, error, fetchData } = useDataStore();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (isLoading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return <div>{data.map(item => <div key={item.id}>{item.name}</div>)}</div>;
}
```

### Persist Middleware
```javascript
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const useAuthStore = create(
  persist(
    (set) => ({
      user: null,
      setUser: (user) => set({ user }),
      logout: () => set({ user: null })
    }),
    {
      name: 'auth-store' // Key for localStorage
    }
  )
);

// State persists across page reloads!
```

### DevTools Middleware
```javascript
import { devtools } from 'zustand/middleware';

const useStore = create(
  devtools((set) => ({
    count: 0,
    increment: () => set((state) => ({ count: state.count + 1 }))
  }))
);

// Debug in Redux DevTools
```

### Combine Middleware
```javascript
const useStore = create(
  devtools(
    persist(
      (set) => ({
        count: 0,
        increment: () => set((state) => ({ count: state.count + 1 }))
      }),
      { name: 'store' }
    )
  )
);
```

## Real-World Examples

### Todo App Store
```javascript
const useTodoStore = create((set) => ({
  todos: [],
  filter: 'all', // 'all', 'active', 'completed'

  addTodo: (text) => set((state) => ({
    todos: [...state.todos, { id: Date.now(), text, completed: false }]
  })),

  toggleTodo: (id) => set((state) => ({
    todos: state.todos.map(todo =>
      todo.id === id ? { ...todo, completed: !todo.completed } : todo
    )
  })),

  deleteTodo: (id) => set((state) => ({
    todos: state.todos.filter(todo => todo.id !== id)
  })),

  setFilter: (filter) => set({ filter }),

  getFilteredTodos: (state) => {
    switch (state.filter) {
      case 'active':
        return state.todos.filter(t => !t.completed);
      case 'completed':
        return state.todos.filter(t => t.completed);
      default:
        return state.todos;
    }
  }
}));
```

### Shopping Cart Store
```javascript
const useCartStore = create((set, get) => ({
  items: [],
  total: 0,

  addItem: (product) => set((state) => {
    const newItems = [...state.items, product];
    return {
      items: newItems,
      total: newItems.reduce((sum, item) => sum + item.price, 0)
    };
  }),

  removeItem: (productId) => set((state) => {
    const newItems = state.items.filter(item => item.id !== productId);
    return {
      items: newItems,
      total: newItems.reduce((sum, item) => sum + item.price, 0)
    };
  }),

  clear: () => set({ items: [], total: 0 }),

  getItemCount: () => get().items.length
}));
```

### Authentication Store
```javascript
const useAuthStore = create(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,

      login: async (email, password) => {
        const response = await fetch('/api/login', {
          method: 'POST',
          body: JSON.stringify({ email, password })
        });
        const { token, user } = await response.json();
        set({
          token,
          user,
          isAuthenticated: true
        });
      },

      logout: () => set({
        token: null,
        user: null,
        isAuthenticated: false
      }),

      register: async (name, email, password) => {
        const response = await fetch('/api/register', {
          method: 'POST',
          body: JSON.stringify({ name, email, password })
        });
        const { token, user } = await response.json();
        set({
          token,
          user,
          isAuthenticated: true
        });
      }
    }),
    { name: 'auth-storage' }
  )
);
```

## Comparison with Redux

| Feature | Zustand | Redux |
|---------|---------|-------|
| Bundle Size | ~2KB | ~10KB |
| Boilerplate | Minimal | Lots |
| Learning Curve | Easy | Steep |
| DevTools | Yes | Yes |
| Middleware | Yes | Yes |
| Async | Simple | Complex (thunks/sagas) |

## Best Practices

### 1. One Store per Feature
```javascript
// ✓ Good
const useAuthStore = create(...);
const useCartStore = create(...);
const useNotificationsStore = create(...);

// ✗ Avoid
const useGlobalStore = create(...); // Everything in one
```

### 2. Use Selectors for Optimization
```javascript
// ✓ Good - Only re-render when count changes
const count = useStore((state) => state.count);

// ✗ Avoid - Re-renders on any change
const { count } = useStore();
```

### 3. Keep State Simple
```javascript
// ✓ Good
{
  count: 0,
  user: { id: 1, name: 'John' }
}

// ✗ Avoid - Computed values in state
{
  count: 0,
  doubleCount: 0, // Derive this instead
  user: { /* ... */ }
}
```

### 4. Immutable Updates
```javascript
// ✓ Good
set((state) => ({
  items: [...state.items, newItem]
}));

// ✗ Avoid - Mutating state
set((state) => {
  state.items.push(newItem);
  return state;
});
```

## Pro Tips

✓ Use TypeScript for type safety
✓ Combine multiple stores when needed
✓ Use persist for localStorage
✓ Debug with Redux DevTools
✓ Keep actions focused and simple
✓ Use selectors to prevent unnecessary re-renders

## Resources

- **Official Docs**: https://github.com/pmndrs/zustand
- **Examples**: GitHub repository has great examples
- **TypeScript**: Full TypeScript support
- **Middleware**: Explore persist, devtools, and custom middleware

---

## Quick Prompts for Claude

- "Create a Zustand store for [feature]"
- "Add state management to this app"
- "Create a shopping cart store with Zustand"
- "Implement authentication with Zustand"
- "Add localStorage persistence to this store"
