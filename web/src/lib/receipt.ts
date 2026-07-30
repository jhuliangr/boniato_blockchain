/**
 * Turns a receipt's `detail` into labelled chips for the activity feed.
 *
 * `docs/api.md` documents a shape per action, so the common cases are explicit
 * here: which fields to show, in which order, and how each one reads (an amount
 * in $BONI, a rate in basis points, a block height, an id). Anything the
 * contract gains later still renders, through the convention-based fallback at
 * the bottom — a new field shows up looking slightly generic instead of
 * vanishing.
 */

import type { ChainInfo, Receipt } from '../api/types.ts'
import { formatAmount, formatMultiplier, formatPercent, shortKey } from './format.ts'

export interface DetailChip {
  key: string
  label: string
  value: string
  /** Hover text, where the value alone would be misleading. */
  title?: string
}

/** Names a full hex public key, e.g. via the node's wallet labels. */
export type KeyNamer = (key: string) => string

/** Field order per action, from the table in `docs/api.md`. */
const ORDER: Record<string, string[]> = {
  claim: ['kind', 'balance', 'lands', 'expires_at'],
  transfer: ['to', 'amount', 'lots'],
  buy_land: ['land_id', 'coords', 'price', 'fertility_bp', 'next_price'],
  plant: ['land_id', 'ready_at'],
  harvest: ['land_id', 'amount', 'fertility_bp', 'adjacent_owned', 'blight_bp', 'expires_at'],
  fertilize: ['land_id', 'blocks_cut', 'consumed', 'refunded', 'ready_at'],
  blight: ['land_id', 'penalty_bp'],
  rot: ['rotted', 'fertilizer'],
  noop: [],
}

/**
 * Fields already shown elsewhere in the row and therefore skipped here: `rot`
 * names its account in `public_key`/`label`, which the signer chip renders.
 */
const HIDDEN: Record<string, string[]> = {
  rot: ['public_key', 'label'],
}

const AMOUNT_KEYS = new Set([
  'amount',
  'balance',
  'price',
  'next_price',
  'rotted',
  'fertilizer',
  'consumed',
  'refunded',
  'cost',
  'yield',
])
const HEIGHT_KEYS = new Set(['expires_at', 'ready_at', 'planted_at'])
const LABELS: Record<string, string> = {
  land_id: 'plot',
  fertility_bp: 'fertility',
  blight_bp: 'blight',
  penalty_bp: 'blight',
  adjacent_owned: 'neighbours',
  blocks_cut: 'brought forward',
  next_price: 'next price',
  expires_at: 'rots at',
  ready_at: 'ready at',
  lands: 'plots',
  kind: '',
  to: 'to',
}

export function receiptChips(
  receipt: Receipt,
  chain: ChainInfo | null,
  nameKey?: KeyNamer,
): DetailChip[] {
  const detail = receipt.detail
  const hidden = new Set(HIDDEN[receipt.action] ?? [])
  const ordered = ORDER[receipt.action] ?? []
  const keys = [
    ...ordered.filter((key) => key in detail),
    ...Object.keys(detail).filter((key) => !ordered.includes(key)),
  ].filter((key) => !hidden.has(key))

  const chips: DetailChip[] = []
  for (const key of keys) {
    // `transfer.detail.to` is the recipient's full public key, so it can be named
    // like any other account: the node's label when it knows one, the key's tail
    // otherwise. (It used to arrive head-truncated, which named nobody, since
    // every IPv8 key shares a long ASN.1 header.)
    if (key === 'to' && typeof detail[key] === 'string') {
      const recipient = detail[key] as string
      chips.push({
        key,
        label: 'to',
        value: nameKey?.(recipient) ?? shortKey(recipient),
        title: recipient,
      })
      continue
    }

    const value = renderValue(key, detail[key], chain)
    if (value === null) continue
    chips.push({ key, label: LABELS[key] ?? key.replace(/_bp$/, '').replace(/_/g, ' '), value })
  }
  return chips
}

function renderValue(key: string, value: unknown, chain: ChainInfo | null): string | null {
  const bu = chain?.base_units ?? 1000
  const bp = chain?.bp ?? 10000

  if (key === 'kind') {
    const kind = String(value)
    return kind === 'starter_kit' ? 'starter kit' : kind === 'relief' ? 'relief grant' : kind
  }

  if (key === 'coords' && Array.isArray(value)) {
    const [x, y] = value as unknown[]
    return `(${asInt(x)}, ${asInt(y)})`
  }

  if (key === 'lands' && Array.isArray(value)) {
    return value.length === 0 ? null : value.map((id) => `#${asInt(id)}`).join(' ')
  }

  // transfer.detail.lots: the batches that changed hands, each with its expiry.
  if (key === 'lots' && Array.isArray(value)) {
    if (value.length === 0) return null
    if (value.length <= 2) {
      return value
        .map((raw) => {
          const lot = (raw ?? {}) as Record<string, unknown>
          return `${formatAmount(asInt(lot.amount), bu)} → #${asInt(lot.expires_at)}`
        })
        .join(', ')
    }
    return `${value.length} lots`
  }

  if (typeof value === 'number') {
    if (key.endsWith('_bp')) {
      // Fertility is a multiplier; penalties and shares read better as a %.
      return key === 'fertility_bp' ? formatMultiplier(value, bp) : formatPercent(value, bp, 1)
    }
    if (AMOUNT_KEYS.has(key)) return formatAmount(value, bu)
    if (HEIGHT_KEYS.has(key)) return `#${value}`
    if (key === 'land_id') return `#${value}`
    if (key === 'blocks_cut') return `${value} ${value === 1 ? 'block' : 'blocks'}`
    return String(value)
  }

  if (typeof value === 'boolean') return value ? 'yes' : 'no'

  if (typeof value === 'string') {
    if (value === '') return null
    // Public keys arrive already truncated in `transfer.detail.to`; a full one
    // gets the same tail treatment as everywhere else in the UI.
    return value.length > 24 && /^[0-9a-f]+$/i.test(value) ? shortKey(value) : value
  }

  if (value === null || value === undefined) return null
  return JSON.stringify(value)
}

function asInt(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : 0
}
