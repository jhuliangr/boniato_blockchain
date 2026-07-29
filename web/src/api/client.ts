/**
 * The one place that talks HTTP.
 *
 * Base URL: empty by default, so every request is same-origin (`/api/...`) and
 * the Vite dev proxy forwards it to the node on 127.0.0.1:8000 — no CORS in the
 * picture. Set `VITE_API_BASE` to point a build at a node directly.
 */

import * as N from './normalize.ts'
import type {
  Account,
  ActionAccepted,
  ActionIntent,
  BlockSummary,
  ChainInfo,
  FarmMap,
  LeaderboardEntry,
  MineResult,
  Receipt,
  Wallet,
} from './types.ts'

const RAW_BASE = (import.meta.env.VITE_API_BASE ?? '').trim()
export const API_BASE = RAW_BASE.replace(/\/+$/, '')

/** Where requests are actually going — shown in the UI's offline state. */
export const API_ORIGIN = API_BASE || 'same origin (/api → vite proxy)'

const DEFAULT_TIMEOUT_MS = 8000
/** Mining does proof-of-work synchronously, so it gets a longer leash. */
const MINE_TIMEOUT_MS = 120000

export class ApiError extends Error {
  readonly status: number
  /** True when the node could not be reached at all (vs. answering with 4xx/5xx). */
  readonly offline: boolean

  constructor(message: string, status: number, offline: boolean) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.offline = offline
  }
}

function url(path: string, query?: Record<string, string | number | undefined>): string {
  const qs = Object.entries(query ?? {})
    .filter((entry): entry is [string, string | number] => entry[1] !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&')
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  timeoutMs?: number
  signal?: AbortSignal
}

/** Performs the request and returns parsed JSON as `unknown`. Never returns undefined. */
async function request(path: string, options: RequestOptions = {}): Promise<unknown> {
  const { method = 'GET', body, timeoutMs = DEFAULT_TIMEOUT_MS, signal } = options

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new Error('timeout')), timeoutMs)
  const onOuterAbort = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', onOuterAbort, { once: true })

  let response: Response
  try {
    response = await fetch(path, {
      method,
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (cause) {
    // The caller aborted deliberately: propagate so callers can ignore it.
    if (signal?.aborted) throw cause
    const detail = cause instanceof Error && cause.message ? cause.message : 'network error'
    throw new ApiError(`node unreachable (${detail})`, 0, true)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onOuterAbort)
  }

  const text = await response.text().catch(() => '')
  let parsed: unknown = null
  if (text !== '') {
    try {
      parsed = JSON.parse(text) as unknown
    } catch {
      parsed = null
    }
  }

  if (!response.ok) {
    const asError = N.obj(parsed)
    // 502/503/504 come from the Vite proxy when the node is not listening:
    // that is an outage, not a rejection by the chain.
    const gateway = response.status >= 502 && response.status <= 504
    const message = gateway
      ? `node unreachable (HTTP ${response.status} from the dev proxy)`
      : N.str(asError.error) || `HTTP ${response.status}`
    throw new ApiError(message, response.status, gateway)
  }
  return parsed
}

function get(path: string, query?: Record<string, string | number | undefined>, signal?: AbortSignal) {
  return request(url(path, query), { signal })
}

export const api = {
  async health(signal?: AbortSignal): Promise<{ ok: boolean; height: number }> {
    const raw = N.obj(await get('/api/health', undefined, signal))
    return { ok: N.bool(raw.ok, true), height: N.uint(raw.height) }
  },

  async chain(signal?: AbortSignal): Promise<ChainInfo> {
    return N.chainInfo(await get('/api/chain', undefined, signal))
  },

  async map(fallbackWidth: number, signal?: AbortSignal): Promise<FarmMap> {
    return N.farmMap(await get('/api/map', undefined, signal), fallbackWidth)
  },

  async account(publicKey: string, signal?: AbortSignal): Promise<Account> {
    const raw = await get(`/api/accounts/${encodeURIComponent(publicKey)}`, undefined, signal)
    return N.account(raw, publicKey)
  },

  async wallets(signal?: AbortSignal): Promise<Wallet[]> {
    return N.wallets(await get('/api/wallets', undefined, signal))
  },

  async createWallet(label?: string, signal?: AbortSignal): Promise<Wallet> {
    const trimmed = (label ?? '').trim()
    const raw = await request(url('/api/wallets'), {
      method: 'POST',
      // `label` is optional; the node generates one when it is absent.
      body: trimmed === '' ? {} : { label: trimmed },
      ...(signal ? { signal } : {}),
    })
    return N.wallet(raw)
  },

  async leaderboard(limit = 10, signal?: AbortSignal): Promise<LeaderboardEntry[]> {
    return N.leaderboard(await get('/api/leaderboard', { limit }, signal))
  },

  async blocks(limit = 10, signal?: AbortSignal): Promise<BlockSummary[]> {
    return N.blocks(await get('/api/blocks', { limit }, signal))
  },

  async activity(limit = 40, signal?: AbortSignal): Promise<Receipt[]> {
    return N.receipts(await get('/api/activity', { limit }, signal))
  },

  /**
   * Submits an intent. A 202 means "in the mempool", NOT "it worked" —
   * preconditions are checked when a block is mined, and a rejected action
   * still pays gas. Outcomes are read from `activity()`.
   */
  async submit(intent: ActionIntent, signal?: AbortSignal): Promise<ActionAccepted> {
    const raw = N.obj(
      await request(url('/api/actions'), {
        method: 'POST',
        body: intent,
        ...(signal ? { signal } : {}),
      }),
    )
    return {
      accepted: N.bool(raw.accepted, true),
      tx_id: N.str(raw.tx_id),
      mempool: N.uint(raw.mempool),
    }
  },

  async mine(signal?: AbortSignal): Promise<MineResult> {
    const raw = await request(url('/api/mine'), {
      method: 'POST',
      body: {},
      timeoutMs: MINE_TIMEOUT_MS,
      ...(signal ? { signal } : {}),
    })
    return N.mineResult(raw)
  },
}

/** Human-readable message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message || error.name
  return String(error)
}

export function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.offline
}
