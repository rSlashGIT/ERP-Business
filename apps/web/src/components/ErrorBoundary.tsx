import React from "react";

interface State { error: Error | null }

/**
 * Catches render-time exceptions so one broken screen does not blank the
 * whole app. Deliberately shows the message: an internal ERP tool used by
 * eight buyers benefits far more from a readable error than from a polished
 * "something went wrong" that gives support nothing to work with.
 */
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode; fallbackLabel?: string },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-6 rounded-lg bg-red-50 p-5 ring-1 ring-red-200">
        <h2 className="text-sm font-semibold text-red-800">
          {this.props.fallbackLabel ?? "This screen failed to render"}
        </h2>
        <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-red-700">
          {this.state.error.message}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          className="mt-3 rounded bg-red-600 px-3 py-1.5 text-sm text-white"
        >
          Try again
        </button>
      </div>
    );
  }
}
