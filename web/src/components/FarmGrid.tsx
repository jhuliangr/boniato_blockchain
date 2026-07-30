import type { ChainInfo, FarmMap, Wallet } from '../api/types.ts'
import { buildGrid } from '../lib/farm.ts'
import { displayName, formatAmount } from '../lib/format.ts'
import { cssVars } from '../lib/style.ts'
import { Panel } from './Panel.tsx'
import { PlotTile } from './PlotTile.tsx'

interface FarmGridProps {
  map: FarmMap | null
  chain: ChainInfo | null
  wallets: Wallet[]
  activeKey: string
  selectedLandId: number | null
  error: string | null
  onSelect: (landId: number) => void
}

/**
 * The board. `GET /api/map` only returns minted plots, so the rest of the grid
 * is filled in as unclaimed land — plot `n` always sits at
 * `(n % grid_width, n // grid_width)`.
 */
export function FarmGrid({
  map,
  chain,
  wallets,
  activeKey,
  selectedLandId,
  error,
  onSelect,
}: FarmGridProps) {
  const bp = chain?.bp ?? 10000
  const bu = chain?.base_units ?? 1000
  const grid = buildGrid(map, chain?.economy.grid_width ?? 8, chain?.economy.next_land_id ?? -1)
  const labels = new Map(wallets.map((wallet) => [wallet.public_key, wallet.label]))
  const minted = map?.plots.length ?? 0
  const mine = map?.plots.filter((plot) => plot.owner === activeKey).length ?? 0

  return (
    <Panel
      title="The farm"
      aside={
        <span className="mono">
          {grid.width}×{grid.rows} · {minted} minted{activeKey ? ` · ${mine} yours` : ''}
        </span>
      }
      error={error}
      flush
    >
      <div className="farm__toolbar">
        <Legend kind="growing" text="growing" />
        <Legend kind="ready" text="ready to harvest" />
        <Legend kind="blight" text="blighted" />
        <Legend kind="mine" text="yours" />
        <Legend kind="unminted" text="unclaimed" />
        <span className="grow" />
        <span className="tiny faint">
          next parcel{' '}
          {grid.nextLandId !== null ? <b className="mono">#{grid.nextLandId}</b> : '—'} for{' '}
          {chain ? formatAmount(chain.economy.next_land_price, bu) : '—'} $BONI
        </span>
      </div>

      <div className="grid-wrap">
        {map === null && error ? (
          <p className="empty-note">Map unavailable while the node is unreachable.</p>
        ) : (
          <div className="grid" style={cssVars({ '--gw': grid.width })}>
            {grid.cells.map((cell) => (
              <PlotTile
                key={cell.land_id}
                landId={cell.land_id}
                plot={cell.plot}
                activeKey={activeKey}
                ownerLabel={
                  cell.plot ? displayName(cell.plot.owner, labels.get(cell.plot.owner)) : ''
                }
                bp={bp}
                adjacencyBonusBp={chain?.economy.adjacency_bonus_bp ?? 0}
                selected={selectedLandId === cell.land_id}
                isNext={grid.nextLandId === cell.land_id}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </div>
    </Panel>
  )
}

function Legend({ kind, text }: { kind: string; text: string }) {
  return (
    <span className="legend">
      <span className={`legend__swatch legend__swatch--${kind}`} aria-hidden="true" />
      {text}
    </span>
  )
}
