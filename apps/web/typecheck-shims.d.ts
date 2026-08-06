/**
 * Minimal ambient declarations so `tsc` can typecheck OUR source without
 * node_modules.
 *
 * THIS IS NOT THE PRODUCTION BUILD AND MUST NOT BE MISTAKEN FOR IT.
 * `npm install` is 403 in this sandbox, so `tsc --noEmit && vite build` has
 * never run. Without any types at all, tsc emits 379 errors — 327 of them a
 * single repeated complaint that `JSX.IntrinsicElements` does not exist — and
 * that wall of noise makes it impossible to tell whether the project's OWN code
 * is sound.
 *
 * These shims declare just enough of react, react-dom, react-router-dom,
 * @tanstack/react-query and vite/client for the compiler to resolve imports and
 * then check our components against our own types (`Urgency`,
 * `RecommendationRow`, prop shapes, member access). Anything that still errors
 * afterwards is a real defect in this repo, not a missing package.
 *
 * Deliberately loose: the goal is to surface OUR mistakes, not to reproduce
 * React's type system. Delete this file the moment a real `npm install` works —
 * `tsconfig.json` excludes it from the production build for exactly that reason.
 */

declare namespace React {
  type ReactNode = any;
  type FC<P = {}> = (props: P) => any;
  interface ErrorInfo { componentStack: string; }
  class Component<P = any, S = any> {
    constructor(props: P);
    props: Readonly<P> & { children?: any };
    state: Readonly<S>;
    setState(s: Partial<S> | ((prev: S) => Partial<S>), cb?: () => void): void;
    render(): any;
  }
  type ChangeEvent<T = any> = { target: T & { value: string; checked: boolean } };
  type MouseEvent<T = any> = { preventDefault(): void; currentTarget: T };
  type KeyboardEvent<T = any> = { key: string; preventDefault(): void };
  type FormEvent<T = any> = { preventDefault(): void };
}

declare namespace JSX {
  interface Element { }
  interface ElementClass { props: any; render(): any; }
  interface ElementAttributesProperty { props: {}; }
  interface ElementChildrenAttribute { children: {}; }
  interface IntrinsicElements { [tag: string]: any; }
}

declare module "react" {
  export type ReactNode = any;
  export type FC<P = {}> = (props: P) => any;
  export interface ErrorInfo { componentStack: string; }
  export class Component<P = any, S = any> {
    constructor(props: P);
    props: Readonly<P> & { children?: ReactNode };
    state: Readonly<S>;
    setState(s: Partial<S> | ((prev: S) => Partial<S>), cb?: () => void): void;
    forceUpdate(cb?: () => void): void;
    render(): ReactNode;
  }
  export const Fragment: any;
  export const StrictMode: any;
  export function useState<S>(init: S | (() => S)):
    [S, (v: S | ((prev: S) => S)) => void];
  export function useMemo<T>(f: () => T, deps: readonly any[]): T;
  export function useCallback<T>(f: T, deps: readonly any[]): T;
  export function useEffect(f: () => void | (() => void), deps?: readonly any[]): void;
  export function useRef<T>(init: T): { current: T };
  export function createElement(...args: any[]): any;
  // `import React from "react"` shadows the ambient namespace, so the default
  // export has to carry the members too or `React.Component` resolves to any
  // and every class component loses its `props`.
  const ReactDefault: {
    Component: typeof Component;
    Fragment: any;
    StrictMode: any;
    createElement: typeof createElement;
    useState: typeof useState;
    useMemo: typeof useMemo;
    useCallback: typeof useCallback;
    useEffect: typeof useEffect;
    useRef: typeof useRef;
  };
  export default ReactDefault;
}

declare module "react/jsx-runtime" {
  export const jsx: any;
  export const jsxs: any;
  export const Fragment: any;
}
declare module "react/jsx-dev-runtime" {
  export const jsxDEV: any;
  export const Fragment: any;
}

declare module "react-dom/client" {
  export function createRoot(el: any): { render(node: any): void };
}

declare module "react-router-dom" {
  export const BrowserRouter: any;
  export const Routes: any;
  export const Route: any;
  export const Navigate: any;
  export const Outlet: any;
  export const NavLink: any;
  export const Link: any;
  export function useNavigate(): (to: string) => void;
  export function useParams<T = Record<string, string>>(): T;
}

declare module "@tanstack/react-query" {
  export class QueryClient { constructor(opts?: any); }
  export const QueryClientProvider: any;
  export function useQuery<T = any>(opts: any): {
    data: T | undefined; isLoading: boolean; isError: boolean;
    error: any; refetch: () => void; isFetching: boolean;
  };
  export function useMutation<T = any, V = any>(opts: any): {
    mutate: (vars: V) => void; mutateAsync: (vars: V) => Promise<T>;
    isPending: boolean; isError: boolean; error: any; data: T | undefined;
  };
  export const keepPreviousData: any;
  export function useQueryClient(): {
    invalidateQueries: (o?: any) => void; setQueryData: (k: any, v: any) => void;
  };
}

interface ImportMetaEnv { readonly VITE_API_URL?: string; }
interface ImportMeta { readonly env: ImportMetaEnv; }

declare module "*.css";
declare module "*.svg";
