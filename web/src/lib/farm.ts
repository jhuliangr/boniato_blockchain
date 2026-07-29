/**
 * Game-domain helpers, kept out of the components.
 *
 * All arithmetic here is integer arithmetic on base units / blocks / basis
 * points. Where the node quotes a figure — `freshness_bp`, `next_land_id`, a
 * plot's fertilizer headroom — that figure is used as given rather than
 * recomputed, so the client cannot drift away from consensus. What is left is
 * layout (which squares to draw) and presentation (what a preview says before
 * you commit to an action).
 */

import type { Account, ChainInfo, FarmMap, Lot, Plot } from '../api/types.ts'

export type PlotState = 'unminted' | 'fallow' | 'growing' | 'ready'

/** One square of the rendered grid — minted (`plot`) or unclaimed (`null`). */
export interface Cell {
  land_id: number
  x: number
  y: number
  plot: Plot | null
}

export interface Grid {
  width: number
  rows: number
  cells: Cell[]
  /** The parcel `buy_land` mints next, from `economy.next_land_id`. */
  nextLandId: number | null
}

/**
 * Lays out the board.
 *
 * The map is unbounded — ids are handed out sequentially forever and the grid
 * grows downwards — so how much empty land to draw is a client-side choice.
 * This draws one complete spare row past the last minted parcel (four rows
 * minimum), so the board grows with the world instead of showing a wall of
 * empty tiles on a fresh chain.
 *
 * `nextLandId` comes from `economy.next_land_id`: which square is up for sale
 * is a fact the chain publishes, not a guess. Falls back to the lowest unminted
 * id if a node does not report it.
 */
export function buildGrid(
  map: FarmMap | null,
  fallbackWidth: number,
  nextLandId = -1,
): Grid {
  const width = Math.max(1, map?.grid_width || fallbackWidth || 8)
  const plots = map?.plots ?? []
  const byId = new Map<number, Plot>()
  let maxId = -1
  for (const plot of plots) {
    byId.set(plot.land_id, plot)
    if (plot.land_id > maxId) maxId = plot.land_id
  }

  const next = nextLandId >= 0 ? nextLandId : null
  // Keep the parcel on sale on the board even if it sits past the spare row.
  const lastVisible = Math.max(maxId, next ?? -1)
  const rows = Math.max(4, Math.ceil((lastVisible + 1 + width) / width))
  const cells: Cell[] = []
  let lowestUnminted: number | null = null
  for (let id = 0; id < rows * width; id += 1) {
    const plot = byId.get(id) ?? null
    if (plot === null && lowestUnminted === null) lowestUnminted = id
    cells.push({ land_id: id, x: id % width, y: Math.floor(id / width), plot })
  }
  return { width, rows, cells, nextLandId: next ?? lowestUnminted }
}

export function plotState(plot: Plot | null): PlotState {
  if (!plot) return 'unminted'
  if (!plot.is_planted) return 'fallow'
  return plot.is_ready ? 'ready' : 'growing'
}

export function isOwnedBy(plot: Plot | null, publicKey: string): boolean {
  return !!plot && publicKey !== '' && plot.owner === publicKey
}

/** Blocks until a growing crop matures. Negative values clamp to zero. */
export function blocksUntilReady(plot: Plot, height: number): number {
  return Math.max(0, plot.ready_at - height)
}

/** Which actions the active wallet can meaningfully take on a plot. */
export interface PlotActions {
  canPlant: boolean
  canHarvest: boolean
  canFertilize: boolean
}

export function plotActions(plot: Plot | null, publicKey: string): PlotActions {
  const mine = isOwnedBy(plot, publicKey)
  const state = plotState(plot)
  return {
    canPlant: mine && state === 'fallow',
    canHarvest: mine && state === 'ready',
    canFertilize: mine && state === 'growing',
  }
}

/* ------------------------------------------------------------------ larder */

export type FreshnessTier = 'fresh' | 'aging' | 'warning' | 'critical'

/**
 * Share of shelf life a lot has left, in basis points: `bp` when it was
 * harvested, 0 when it rots.
 *
 * The node reports this as `freshness_bp`, which is what we use. The
 * `blocks_left / rot_blocks` derivation is kept only as a fallback for a node
 * that does not send the field (normalised to -1).
 */
export function lotFreshnessBp(lot: Lot, rotBlocks: number, bp: number): number {
  if (lot.freshness_bp >= 0) return Math.min(bp, lot.freshness_bp)
  const window = Math.max(1, rotBlocks)
  const left = Math.max(0, lot.blocks_left)
  return Math.min(bp, Math.round((left * bp) / window))
}

export function freshnessTier(freshnessBp: number, bp: number): FreshnessTier {
  const fraction = freshnessBp / Math.max(1, bp)
  if (fraction <= 0.08) return 'critical'
  if (fraction <= 0.2) return 'warning'
  if (fraction <= 0.45) return 'aging'
  return 'fresh'
}

export interface LarderSummary {
  total: number
  lots: Lot[]
  /** Amount sitting in lots that are in the `warning` or `critical` tier. */
  atRisk: number
  soonest: Lot | null
}

export function summariseLarder(
  account: Account | null,
  chain: ChainInfo | null,
): LarderSummary {
  const lots = account?.lots ?? []
  const bp = chain?.bp ?? 10000
  const rotBlocks = chain?.economy.rot_blocks ?? 1440
  let total = 0
  let atRisk = 0
  for (const lot of lots) {
    total += lot.amount
    const tier = freshnessTier(lotFreshnessBp(lot, rotBlocks, bp), bp)
    if (tier === 'warning' || tier === 'critical') atRisk += lot.amount
  }
  return { total, lots, atRisk, soonest: lots[0] ?? null }
}

/* ------------------------------------------------------------- fertilizer */

export interface FertilizerQuote {
  /** Blocks `fertilize` would remove from this crop's growing time. */
  blocksCut: number
  /** Fertilizer actually consumed, in base units. */
  consumed: number
  /** Fertilizer handed straight back, in base units. */
  refunded: number
  /** The whole crop's remaining headroom, as quoted by the node. */
  headroomBlocks: number
  /** What buying all of that headroom costs, as quoted by the node. */
  headroomCost: number
  /** True when the amount overshoots the headroom, so part is wasted. */
  cappedOut: boolean
  /** `ready_at` after the call. */
  readyAt: number
}

/**
 * Previews a `fertilize` call for an arbitrary amount.
 *
 * The cap comes from the node: `fertilizer_headroom_blocks` /
 * `fertilizer_headroom_cost` are quoted per plot precisely so a client does not
 * reimplement the floor (which is measured against *nominal* growth, not
 * remaining time) and drift away from consensus. The only arithmetic here is the
 * conversion the contract states outright — a whole unit (`base_units`) buys
 * `growth_blocks_per_fertilizer` blocks, flooring — clamped to the quote.
 *
 * Spending exactly `fertilizer_headroom_cost` is the exact path: the node has a
 * test asserting it buys the quoted blocks with zero refund.
 */
export function quoteFertilizer(plot: Plot, amount: number, chain: ChainInfo): FertilizerQuote {
  const baseUnits = Math.max(1, chain.base_units)
  const perUnit = chain.economy.growth_blocks_per_fertilizer
  const headroomBlocks = plot.fertilizer_headroom_blocks
  const headroomCost = plot.fertilizer_headroom_cost

  const wanted = perUnit > 0 ? Math.floor((amount * perUnit) / baseUnits) : 0
  const blocksCut = Math.min(wanted, headroomBlocks)
  // At or above the quoted cost the node's own figure is authoritative.
  const consumed = amount >= headroomCost && headroomCost > 0
    ? headroomCost
    : Math.min(amount, perUnit > 0 ? Math.ceil((blocksCut * baseUnits) / perUnit) : 0)

  return {
    blocksCut,
    consumed,
    refunded: Math.max(0, amount - consumed),
    headroomBlocks,
    headroomCost,
    cappedOut: wanted > headroomBlocks,
    readyAt: plot.ready_at - blocksCut,
  }
}

/**
 * Is there any point fertilizing this plot?
 *
 * The node only quotes headroom when its own engine would accept a `fertilize`,
 * so this is just "did it quote any". The `is_planted && !is_ready` conditions are
 * kept as belt-and-braces: they cost nothing and they document what a non-zero
 * quote implies.
 */
export function hasFertilizerHeadroom(plot: Plot | null): boolean {
  return !!plot && plot.is_planted && !plot.is_ready && plot.fertilizer_headroom_blocks > 0
}

/** The exact bonus a plot earns from neighbours it shares an owner with, in bp. */
export function adjacencyBonusBp(plot: Plot, chain: ChainInfo | null): number {
  return plot.adjacent_owned * (chain?.economy.adjacency_bonus_bp ?? 0)
}
