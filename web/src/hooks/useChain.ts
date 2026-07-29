import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../api/client.ts'
import type { ChainInfo } from '../api/types.ts'

export interface ChainPoll {
  chain: ChainInfo | null
  /** The node answered the most recent poll. */
  online: boolean
  error: string | null
  failures: number
  /** `Date.now()` of the last successful poll. */
  updatedAt: number | null
  /** Poll immediately instead of waiting for the next tick. */
  refresh: () => void
}

/**
 * Polls `GET /api/chain` forever — the app's clock.
 *
 * There are no websockets, so this is the only thing that discovers new blocks.
 * A self-scheduling timeout (rather than an interval) keeps requests from
 * stacking up, and failures back off gently instead of hammering a dead node.
 * The last good `ChainInfo` is kept through an outage so the UI stays legible.
 */
export function useChain(intervalMs = 1000): ChainPoll {
  const [chain, setChain] = useState<ChainInfo | null>(null)
  const [online, setOnline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failures, setFailures] = useState(0)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const wakeRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let consecutiveFailures = 0

    const schedule = (delay: number) => {
      if (cancelled) return
      timer = setTimeout(tick, delay)
    }

    const tick = async () => {
      try {
        const info = await api.chain(controller.signal)
        if (cancelled) return
        consecutiveFailures = 0
        setChain(info)
        setOnline(true)
        setError(null)
        setFailures(0)
        setUpdatedAt(Date.now())
        schedule(intervalMs)
      } catch (cause) {
        if (cancelled || controller.signal.aborted) return
        consecutiveFailures += 1
        setOnline(false)
        setError(errorMessage(cause))
        setFailures(consecutiveFailures)
        // Back off to at most 5s while the node is down, then keep trying.
        schedule(Math.min(5000, intervalMs * Math.min(5, consecutiveFailures)))
      }
    }

    wakeRef.current = () => {
      if (timer !== undefined) clearTimeout(timer)
      void tick()
    }
    void tick()

    return () => {
      cancelled = true
      wakeRef.current = null
      controller.abort()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [intervalMs])

  return {
    chain,
    online,
    error,
    failures,
    updatedAt,
    refresh: () => wakeRef.current?.(),
  }
}
