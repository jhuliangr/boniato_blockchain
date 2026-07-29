import type { ChainInfo } from '../api/types.ts'
import { formatAmount, formatDuration, formatPercent } from '../lib/format.ts'
import { Panel } from './Panel.tsx'

/**
 * Token supply plus the economic constants the node is running.
 *
 * Every figure is an integer in base units; `formatAmount` is the only place
 * it becomes a decimal string.
 */
export function SupplyPanel({ chain }: { chain: ChainInfo | null }) {
  const bu = chain?.base_units ?? 1000
  const supply = chain?.supply
  const economy = chain?.economy
  const bp = chain?.bp ?? 10000
  const amount = (value: number | undefined) =>
    chain && value !== undefined ? formatAmount(value, bu) : '—'

  return (
    <Panel
      title="$BONI supply"
      aside={chain ? <span className="mono">1 $BONI = {formatAmount(bu, 1)} base units</span> : null}
      flush
    >
      <div className="stats">
        <Stat
          className="stat--circulating"
          label="circulating"
          value={amount(supply?.circulating)}
          note="unrotted, unburned"
        />
        <Stat className="stat--minted" label="minted" value={amount(supply?.minted)} note="ever harvested" />
        <Stat className="stat--burned" label="burned" value={amount(supply?.burned)} note="gas + seeds + land" />
        <Stat className="stat--rotted" label="rotted" value={amount(supply?.rotted)} note="turned to compost" />
        <Stat
          className="stat--fertilizer"
          label="fertilizer minted"
          value={amount(supply?.fertilizer_minted)}
          note="from rot"
        />
      </div>

      {economy ? (
        <div className="farm__toolbar" style={{ borderTop: '1px solid var(--line-soft)', borderBottom: 'none' }}>
          <span className="eyebrow">rules</span>
          <span className="chip">gas {formatAmount(economy.gas_fee, bu)} $BONI</span>
          <span className="chip">seed {formatAmount(economy.seed_cost, bu)} $BONI</span>
          <span className="chip chip--gold">
            next plot {economy.next_land_id >= 0 ? `#${economy.next_land_id} ` : ''}for{' '}
            {formatAmount(economy.next_land_price, bu)} $BONI
          </span>
          <span className="chip">growth {economy.growth_blocks} blocks</span>
          <span className="chip">
            rot {economy.rot_blocks} blocks ({economy.rot_days}d)
          </span>
          <span className="chip" title="Per adjacent plot the same owner holds">
            adjacency +{formatPercent(economy.adjacency_bonus_bp, bp)} per neighbour
          </span>
          <span className="chip">
            blight every {economy.blight_interval} blocks, −{formatPercent(economy.blight_penalty_bp, bp)}
          </span>
          <span className="chip">
            1 day = {economy.blocks_per_day} blocks · growth ≈ {formatDuration(economy.growth_blocks, economy.blocks_per_day)}
          </span>
        </div>
      ) : null}
    </Panel>
  )
}

function Stat({
  label,
  value,
  note,
  className,
}: {
  label: string
  value: string
  note: string
  className: string
}) {
  return (
    <div className={`stat ${className}`}>
      <span className="stat__key">{label}</span>
      <span className="stat__value">{value}</span>
      <span className="stat__note">{note}</span>
    </div>
  )
}
