import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/**
 * Last line of defence: a render error shows a readable card instead of a
 * blank page. Reloading is enough to recover — all state lives on the node.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('UI crashed', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="shell" style={{ paddingTop: 40 }}>
        <section className="panel" style={{ maxWidth: 560 }}>
          <header className="panel__head">
            <h2 className="panel__title">The client hit an unexpected error</h2>
          </header>
          <div className="panel__body stack">
            <p className="mono tiny" style={{ color: 'var(--danger)' }}>
              {error.message || String(error)}
            </p>
            <p className="hint">
              Nothing was lost — the chain lives in the node. Reload to carry on.
            </p>
            <button type="button" className="btn btn--primary" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </section>
      </div>
    )
  }
}
