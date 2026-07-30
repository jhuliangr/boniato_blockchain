/**
 * Display helpers.
 *
 * Amounts stay integers in base units everywhere in this app; they only become
 * strings here, at the very edge. Nothing in this file feeds a value back into
 * a request except `parseAmount`, which goes the other way (text -> integer
 * base units) using integer arithmetic only.
 */

/** Digits after the decimal point implied by a base-unit scale (1000 -> 3). */
export function unitDigits(baseUnits: number): number {
  const scale = Math.max(1, Math.trunc(baseUnits))
  return String(scale).length - 1
}

const groups = new Intl.NumberFormat('en-US')

/**
 * Formats an integer amount in base units as a decimal string.
 * `formatAmount(425311, 1000)` -> `"425.311"`.
 */
export function formatAmount(
  amount: number,
  baseUnits: number,
  options: { trim?: boolean; group?: boolean } = {},
): string {
  const { trim = true, group = true } = options
  const scale = Math.max(1, Math.trunc(baseUnits))
  const value = Math.trunc(Number.isFinite(amount) ? amount : 0)
  const negative = value < 0
  const magnitude = Math.abs(value)

  // Integer division and remainder: exact for anything under 2^53.
  const whole = Math.floor(magnitude / scale)
  const rest = magnitude - whole * scale

  const digits = unitDigits(scale)
  let fraction = digits > 0 ? String(rest).padStart(digits, '0') : ''
  if (trim) fraction = fraction.replace(/0+$/, '')

  const head = group ? groups.format(whole) : String(whole)
  return `${negative ? '-' : ''}${head}${fraction ? `.${fraction}` : ''}`
}

/** As `formatAmount`, with the ticker appended. */
export function formatBoni(amount: number, baseUnits: number): string {
  return `${formatAmount(amount, baseUnits)} $BONI`
}

/**
 * Parses user input (in whole $BONI, e.g. `"12.5"`) into integer base units.
 * Returns `null` for anything unusable. Never uses floating point on the value.
 */
export function parseAmount(text: string, baseUnits: number): number | null {
  const scale = Math.max(1, Math.trunc(baseUnits))
  const digits = unitDigits(scale)
  const cleaned = text.trim().replace(/[_\s,]/g, '')
  if (cleaned === '') return null
  if (!/^\d*(\.\d*)?$/.test(cleaned)) return null

  const [wholeText = '', fractionText = ''] = cleaned.split('.')
  if (wholeText === '' && fractionText === '') return null

  const whole = wholeText === '' ? 0 : Number(wholeText)
  if (!Number.isSafeInteger(whole)) return null

  // Too many decimals for the chain's precision: reject rather than round.
  if (fractionText.length > digits) return null
  const fraction = fractionText === '' ? 0 : Number(fractionText.padEnd(digits, '0'))
  if (!Number.isFinite(fraction)) return null

  const total = whole * scale + fraction
  return Number.isSafeInteger(total) ? total : null
}

/** `11302 bp` -> `"1.13x"`. */
export function formatMultiplier(bp: number, bpScale: number, decimals = 2): string {
  const scale = Math.max(1, bpScale)
  return `${(bp / scale).toFixed(decimals)}x`
}

/** `6500 bp` -> `"65%"`. */
export function formatPercent(bp: number, bpScale: number, decimals = 0): string {
  const scale = Math.max(1, bpScale)
  return `${((bp * 100) / scale).toFixed(decimals)}%`
}

/** Clamps a bp value to a 0..1 fraction, for widths and CSS variables. */
export function bpFraction(bp: number, bpScale: number): number {
  const scale = Math.max(1, bpScale)
  const fraction = bp / scale
  if (!Number.isFinite(fraction)) return 0
  return Math.min(1, Math.max(0, fraction))
}

/** `1401` blocks at `144`/day -> `"9.7 days"`. */
export function formatDuration(blocks: number, blocksPerDay: number): string {
  const perDay = Math.max(1, blocksPerDay)
  const value = Math.max(0, Math.trunc(blocks))
  if (value < perDay) {
    const hours = (value * 24) / perDay
    if (hours < 1) return `${Math.round(hours * 60)} min`
    return `${hours.toFixed(1)} h`
  }
  const days = value / perDay
  return `${days.toFixed(days < 10 ? 1 : 0)} days`
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return count === 1 ? one : many
}

/** Middle-truncates a hash for display: `"0000a1b2…9f3c"`. */
export function shortHash(hash: string, head = 8, tail = 4): string {
  if (!hash) return '—'
  if (hash.length <= head + tail + 1) return hash
  return `${hash.slice(0, head)}…${hash.slice(-tail)}`
}

/**
 * Short form of a public key, taken from the **tail**.
 *
 * Serialised IPv8 keys begin with a long common ASN.1 curve header — every
 * farmer on this chain starts `307e301006072a8648ce3d020106052b81040024036a0004`
 * — so `slice(0, n)` renders every wallet identically. The tail is the part
 * that actually differs, and unlike a locally-invented hash it is real key
 * material the reader can check against the node's own output.
 *
 * Deterministic: the same key always yields the same string, everywhere in the
 * UI. Prefer `displayName` when the API supplies a `label`.
 */
export function shortKey(key: string, tail = 8): string {
  if (!key) return '—'
  return key.length <= tail ? key : `…${key.slice(-tail)}`
}

/**
 * What to call an account on screen: the node's label when there is one (the
 * API now supplies it on receipts, wallets and leaderboard entries), otherwise
 * the key's tail. One key, one name, everywhere.
 */
export function displayName(key: string, label?: string): string {
  const named = (label ?? '').trim()
  if (named !== '') return named
  return shortKey(key)
}

/** Stable hue per public key, so an owner keeps one colour across the UI. */
export function hueForKey(key: string): number {
  let hash = 2166136261
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return Math.abs(hash) % 360
}

export function formatClock(unixSeconds: number): string {
  if (!Number.isFinite(unixSeconds) || unixSeconds <= 0) return '—'
  const date = new Date(unixSeconds * 1000)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatCount(value: number): string {
  return groups.format(Math.trunc(Number.isFinite(value) ? value : 0))
}
