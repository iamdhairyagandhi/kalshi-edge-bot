import React from "react";

type Props = { children: React.ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("dashboard render failed", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div style={{
        minHeight: "100vh",
        background: "var(--bg-0)",
        color: "var(--fg-0)",
        fontFamily: "var(--mono)",
        padding: 24,
      }}>
        <div style={{ color: "var(--amber)", fontSize: 18, marginBottom: 12 }}>EDGE dashboard render failed</div>
        <div style={{ color: "var(--fg-1)", marginBottom: 12 }}>
          The app caught a frontend error instead of showing a blank screen.
        </div>
        <pre style={{
          whiteSpace: "pre-wrap",
          color: "var(--red)",
          background: "var(--bg-1)",
          border: "1px solid var(--border)",
          padding: 12,
          borderRadius: 4,
        }}>
          {this.state.error.message}
        </pre>
      </div>
    );
  }
}
