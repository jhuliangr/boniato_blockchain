/**
 * Coercion layer between "whatever JSON arrived" and our types.
 *
 * The node is being written in parallel and may be an older or newer build, so
 * nothing here trusts a field to exist. Every reader takes `unknown` and always
 * returns a complete, well-typed value. A missing field becomes a default; it
 * never throws and it never leaks `undefined` into the UI.
 */

import type {
  Account,
  BlockSummary,
  ChainInfo,
  Economy,
  FarmMap,
  LeaderboardEntry,
  Lot,
  MineResult,
  Plot,
  Receipt,
  Supply,
  Wallet,
} from './types.ts'

type Dict = Record<string, unknown>

export function obj(v: unknown): Dict {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : {}
}

export function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

export function str(v: unknown, fallback = ''): string {
  if (typeof v === 'string') return v
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return fallback
}

export function bool(v: unknown, fallback = false): boolean {
  return typeof v === 'boolean' ? v : fallback
}

/** An integer amount in base units. Anything unusable becomes `fallback`. */
export function int(v: unknown, fallback = 0): number {
  if (typeof v === 'number' && Number.isFinite(v)) return Math.trunc(v)
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    if (Number.isFinite(n)) return Math.trunc(n)
  }
  return fallback
}

/** Like `int` but never below zero — for amounts, heights and counters. */
export function uint(v: unknown, fallback = 0): number {
  return Math.max(0, int(v, fallback))
}

function intList(v: unknown): number[] {
  return arr(v).map((x) => int(x, -1)).filter((n) => n >= 0)
}

export function economy(v: unknown): Economy {
  const e = obj(v)
  return {
    grid_width: Math.max(1, uint(e.grid_width, 8)),
    blocks_per_day: Math.max(1, uint(e.blocks_per_day, 144)),
    rot_days: uint(e.rot_days, 10),
    rot_blocks: Math.max(1, uint(e.rot_blocks, 1440)),
    growth_blocks: Math.max(1, uint(e.growth_blocks, 20)),
    gas_fee: uint(e.gas_fee),
    seed_cost: uint(e.seed_cost),
    starter_balance: uint(e.starter_balance),
    relief_balance: uint(e.relief_balance),
    next_land_price: uint(e.next_land_price),
    // -1 means "the node did not say", so the grid can fall back to its own
    // guess at which parcel is next instead of pointing at plot 0.
    next_land_id: int(e.next_land_id, -1),
    adjacency_bonus_bp: uint(e.adjacency_bonus_bp),
    blight_interval: uint(e.blight_interval),
    blight_penalty_bp: uint(e.blight_penalty_bp),
    rot_fertilizer_bp: uint(e.rot_fertilizer_bp),
    growth_blocks_per_fertilizer: uint(e.growth_blocks_per_fertilizer),
    fertilizer_min_growth_bp: uint(e.fertilizer_min_growth_bp, 5000),
    base_yield_min: uint(e.base_yield_min),
    base_yield_max: uint(e.base_yield_max),
  }
}

export function supply(v: unknown): Supply {
  const s = obj(v)
  return {
    circulating: uint(s.circulating),
    minted: uint(s.minted),
    burned: uint(s.burned),
    rotted: uint(s.rotted),
    fertilizer_minted: uint(s.fertilizer_minted),
  }
}

export function chainInfo(v: unknown): ChainInfo {
  const c = obj(v)
  return {
    height: uint(c.height),
    head_hash: str(c.head_hash),
    state_root: str(c.state_root),
    difficulty: uint(c.difficulty),
    // Guarded: a zero would make every formatted amount a division by zero.
    base_units: Math.max(1, uint(c.base_units, 1000)),
    bp: Math.max(1, uint(c.bp, 10000)),
    economy: economy(c.economy),
    supply: supply(c.supply),
    mempool: uint(c.mempool),
  }
}

export function plot(v: unknown, gridWidth: number): Plot {
  const p = obj(v)
  const landId = uint(p.land_id)
  // x/y are derivable from land_id; the contract guarantees they agree, but we
  // fall back to the derivation rather than rendering a plot at (0,0).
  const hasXY = typeof p.x === 'number' && typeof p.y === 'number'
  return {
    land_id: landId,
    x: hasXY ? uint(p.x) : landId % gridWidth,
    y: hasXY ? uint(p.y) : Math.floor(landId / gridWidth),
    owner: str(p.owner),
    fertility_bp: uint(p.fertility_bp),
    is_planted: bool(p.is_planted),
    planted_at: uint(p.planted_at),
    ready_at: uint(p.ready_at),
    progress_bp: uint(p.progress_bp),
    is_ready: bool(p.is_ready),
    blight_bp: uint(p.blight_bp),
    adjacent_owned: uint(p.adjacent_owned),
    fertilizer_headroom_blocks: uint(p.fertilizer_headroom_blocks),
    fertilizer_headroom_cost: uint(p.fertilizer_headroom_cost),
  }
}

export function farmMap(v: unknown, fallbackWidth: number): FarmMap {
  const m = obj(v)
  const gridWidth = Math.max(1, uint(m.grid_width, fallbackWidth))
  return {
    grid_width: gridWidth,
    plots: arr(m.plots)
      .map((p) => plot(p, gridWidth))
      .sort((a, b) => a.land_id - b.land_id),
  }
}

export function lot(v: unknown): Lot {
  const l = obj(v)
  return {
    amount: uint(l.amount),
    expires_at: uint(l.expires_at),
    blocks_left: int(l.blocks_left),
    // -1 = not reported; 0 is a real value (the block it rots), so the sentinel
    // has to sit outside the valid range.
    freshness_bp: int(l.freshness_bp, -1),
  }
}

export function account(v: unknown, requestedKey = ''): Account {
  const a = obj(v)
  return {
    public_key: str(a.public_key, requestedKey),
    label: str(a.label),
    balance: uint(a.balance),
    fertilizer: uint(a.fertilizer),
    claimed: bool(a.claimed),
    plots: intList(a.plots).sort((x, y) => x - y),
    // Contract says soonest-to-rot first; sort anyway so the larder is right
    // even if the node's ordering ever changes.
    lots: arr(a.lots)
      .map(lot)
      .sort((x, y) => x.expires_at - y.expires_at),
  }
}

export function wallet(v: unknown): Wallet {
  const w = obj(v)
  const key = str(w.public_key)
  return { public_key: key, label: str(w.label, key.slice(0, 8)) }
}

export function wallets(v: unknown): Wallet[] {
  return arr(v)
    .map(wallet)
    .filter((w) => w.public_key !== '')
}

export function leaderboard(v: unknown): LeaderboardEntry[] {
  return arr(v).map((raw) => {
    const e = obj(raw)
    return {
      public_key: str(e.public_key),
      label: str(e.label),
      balance: uint(e.balance),
      plots: uint(e.plots),
      fertilizer: uint(e.fertilizer),
    }
  })
}

export function blocks(v: unknown): BlockSummary[] {
  return arr(v).map((raw) => {
    const b = obj(raw)
    return {
      index: uint(b.index),
      hash: str(b.hash),
      prev_hash: str(b.prev_hash),
      timestamp: uint(b.timestamp),
      nonce: uint(b.nonce),
      tx_count: uint(b.tx_count),
      merkle_root: str(b.merkle_root),
      state_root: str(b.state_root),
    }
  })
}

export function receipt(v: unknown): Receipt {
  const r = obj(v)
  return {
    height: uint(r.height),
    tx_id: str(r.tx_id),
    action: str(r.action, 'unknown'),
    // A missing `ok` is treated as success: system events (blight/rot) are
    // things that happened, not things that failed.
    ok: bool(r.ok, true),
    reason: str(r.reason),
    // The signer. Empty on system events, except `rot`, which names the
    // account it happened to.
    public_key: str(r.public_key),
    label: str(r.label),
    gas_burned: uint(r.gas_burned),
    minted: uint(r.minted),
    burned: uint(r.burned),
    detail: obj(r.detail),
  }
}

export function receipts(v: unknown): Receipt[] {
  return arr(v).map(receipt)
}

export function mineResult(v: unknown): MineResult {
  const m = obj(v)
  return {
    index: uint(m.index),
    hash: str(m.hash),
    tx_count: uint(m.tx_count),
    receipts: receipts(m.receipts),
    events: receipts(m.events),
  }
}
