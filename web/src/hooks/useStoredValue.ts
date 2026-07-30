import { useCallback, useState } from 'react'

/**
 * A string in `localStorage`, so the chosen wallet survives a reload.
 * Storage can throw (private mode, quota); every access is guarded.
 */
export function useStoredValue(key: string, initial = ''): [string, (value: string) => void] {
  const [value, setValue] = useState<string>(() => {
    try {
      return window.localStorage.getItem(key) ?? initial
    } catch {
      return initial
    }
  })

  const update = useCallback(
    (next: string) => {
      setValue(next)
      try {
        if (next === '') window.localStorage.removeItem(key)
        else window.localStorage.setItem(key, next)
      } catch {
        /* non-fatal: the value still lives in React state for this session */
      }
    },
    [key],
  )

  return [value, update]
}
