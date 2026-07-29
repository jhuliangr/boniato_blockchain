import type { Toast } from '../hooks/useToasts.ts'

/** Bottom-right notices: intents queued, outcomes read back, mining results. */
export function Toasts({
  toasts,
  onDismiss,
}: {
  toasts: Toast[]
  onDismiss: (id: number) => void
}) {
  if (toasts.length === 0) return null
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast--${toast.kind}`}>
          <div className="grow">
            <p className="toast__title">{toast.title}</p>
            {toast.body ? <p className="toast__body">{toast.body}</p> : null}
          </div>
          <button
            type="button"
            className="toast__close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
