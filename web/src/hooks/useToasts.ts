import { useCallback, useEffect, useRef, useState } from 'react'

export type ToastKind = 'info' | 'ok' | 'warn' | 'error'

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  body?: string
}

const LIFETIME_MS: Record<ToastKind, number> = {
  info: 5000,
  ok: 6000,
  warn: 8000,
  error: 10000,
}

export interface Toaster {
  toasts: Toast[]
  push: (kind: ToastKind, title: string, body?: string) => void
  dismiss: (id: number) => void
}

/** Transient notices: intents submitted, outcomes read back, mining results. */
export function useToasts(max = 5): Toaster {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (kind: ToastKind, title: string, body?: string) => {
      const id = nextId.current
      nextId.current += 1
      setToasts((current) => [...current.slice(-(max - 1)), { id, kind, title, ...(body ? { body } : {}) }])
      const timer = setTimeout(() => {
        timers.current.delete(timer)
        dismiss(id)
      }, LIFETIME_MS[kind])
      timers.current.add(timer)
    },
    [dismiss, max],
  )

  useEffect(
    () => () => {
      for (const timer of timers.current) clearTimeout(timer)
      timers.current.clear()
    },
    [],
  )

  return { toasts, push, dismiss }
}
