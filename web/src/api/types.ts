/**
 * TypeScript mirror of the HTTP contract in `docs/api.md`.
 *
 * Two rules from the contract are baked into these types:
 *  - every amount is an INTEGER in base units (1 $BONI = `ChainInfo.base_units`),
 *    never a float. Formatting happens at the edge, in `lib/format.ts`.
 *  - every rate is in basis points, where `ChainInfo.bp` (10000) is 1.0x.
 */

export type ActionType =
  | 'claim'
  | 'transfer'
  | 'buy_land'
  | 'plant'
  | 'harvest'
  | 'fertilize'

/** Actions the chain itself performs, which show up in the activity feed. */
export type SystemAction = 'blight' | 'rot'

/** A phase-2 dummy transaction: signed, valid, carries no intent. */
export type NoopAction = 'noop'

export interface Economy {
  grid_width: number
  blocks_per_day: number
  rot_days: number
  rot_blocks: number
  growth_blocks: number
  gas_fee: number
  seed_cost: number
  starter_balance: number
  relief_balance: number
  /** Live state, not a rule: what the next parcel costs. */
  next_land_price: number
  /**
   * Live state: the id `buy_land` will mint next. Plots are handed out
   * sequentially, so which parcel a buyer gets is the chain's decision.
   */
  next_land_id: number
  /** Per adjacent plot the owner also holds: `adjacent_owned × this`. */
  adjacency_bonus_bp: number
  blight_interval: number
  blight_penalty_bp: number
  rot_fertilizer_bp: number
  growth_blocks_per_fertilizer: number
  fertilizer_min_growth_bp: number
  base_yield_min: number
  base_yield_max: number
}

export interface Supply {
  circulating: number
  minted: number
  burned: number
  rotted: number
  fertilizer_minted: number
}

/** GET /api/chain */
export interface ChainInfo {
  height: number
  head_hash: string
  state_root: string
  difficulty: number
  base_units: number
  bp: number
  economy: Economy
  supply: Supply
  mempool: number
}

/** An element of GET /api/map `plots`. Only minted plots are returned. */
export interface Plot {
  land_id: number
  x: number
  y: number
  owner: string
  fertility_bp: number
  is_planted: boolean
  planted_at: number
  ready_at: number
  progress_bp: number
  is_ready: boolean
  blight_bp: number
  adjacent_owned: number
  /**
   * The most growing time `fertilize` could still remove from this crop, and
   * the fertilizer that would take. Quoted by the node — the floor is measured
   * against *nominal* growth, so a client must never recompute this or it will
   * eventually disagree with consensus. Both 0 on a fallow plot.
   */
  fertilizer_headroom_blocks: number
  fertilizer_headroom_cost: number
}

/** GET /api/map */
export interface FarmMap {
  grid_width: number
  plots: Plot[]
}

/** One perishable holding. `blocks_left` is `expires_at - height`. */
export interface Lot {
  amount: number
  expires_at: number
  blocks_left: number
  /**
   * Share of shelf life still ahead of the batch, in basis points: `bp` the
   * block it was harvested, 0 the block it rots. Ready-made for a meter.
   * Normalised to `-1` when a node did not report it, so the client can fall
   * back to `blocks_left / rot_blocks`.
   */
  freshness_bp: number
}

/** GET /api/accounts/{public_key} — unknown keys return a zeroed account. */
export interface Account {
  public_key: string
  label: string
  balance: number
  fertilizer: number
  claimed: boolean
  plots: number[]
  lots: Lot[]
}

/** GET /api/wallets, POST /api/wallets */
export interface Wallet {
  public_key: string
  label: string
}

/** GET /api/leaderboard */
export interface LeaderboardEntry {
  public_key: string
  label: string
  balance: number
  plots: number
  fertilizer: number
}

/** GET /api/blocks — newest first. */
export interface BlockSummary {
  index: number
  hash: string
  prev_hash: string
  timestamp: number
  nonce: number
  tx_count: number
  merkle_root: string
  state_root: string
}

/**
 * One row of GET /api/activity. Both transaction receipts and system events
 * (`blight`, `rot`) share this shape; system events carry an empty `tx_id`
 * and no gas.
 *
 * `public_key`/`label` identify the **signer**, which is what makes the feed
 * filterable by wallet. Nobody signs a system event, so both are empty there —
 * except `rot`, which happens *to* an account and therefore names it.
 *
 * `detail` is action-specific (see the table in `docs/api.md`) and only
 * populated on success; it stays loosely typed here and is rendered by
 * `components/detail.tsx`.
 */
export interface Receipt {
  height: number
  tx_id: string
  action: ActionType | SystemAction | NoopAction | string
  ok: boolean
  reason: string
  public_key: string
  label: string
  gas_burned: number
  minted: number
  burned: number
  detail: Record<string, unknown>
}

/** One element of `transfer.detail.lots`: a batch that changed hands. */
export interface MovedLot {
  amount: number
  expires_at: number
}

/** POST /api/mine */
export interface MineResult {
  index: number
  hash: string
  tx_count: number
  receipts: Receipt[]
  events: Receipt[]
}

/** POST /api/actions — 202 accepted is NOT success; read /api/activity. */
export interface ActionAccepted {
  accepted: boolean
  tx_id: string
  mempool: number
}

/** The body of POST /api/actions. Extra fields depend on `type`. */
export type ActionIntent =
  | { public_key: string; type: 'claim' }
  | { public_key: string; type: 'buy_land' }
  | { public_key: string; type: 'transfer'; to: string; amount: number }
  | { public_key: string; type: 'plant'; land_id: number }
  | { public_key: string; type: 'harvest'; land_id: number }
  | { public_key: string; type: 'fertilize'; land_id: number; amount: number }
