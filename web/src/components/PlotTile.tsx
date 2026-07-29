import type { Plot } from '../api/types.ts'
import { plotState } from '../lib/farm.ts'
import { bpFraction, formatMultiplier, formatPercent, hueForKey } from '../lib/format.ts'
import { cssVars, cx } from '../lib/style.ts'

interface PlotTileProps {
  landId: number
  plot: Plot | null
  /** Public key of the wallet being played, for ownership highlighting. */
  activeKey: string
  ownerLabel: string
  bp: number
  /** `adjacency_bonus_bp`, to turn `adjacent_owned` into a real number. */
  adjacencyBonusBp: number
  selected: boolean
  /** True for `economy.next_land_id` — the parcel `buy_land` mints next. */
  isNext: boolean
  onSelect: (landId: number) => void
}

/**
 * One square of land, drawn entirely in CSS: soil below the line, air above,
 * and a plant whose size follows `progress_bp`. Ready crops glow gold and push
 * a tuber out of the ground; blighted crops are hatched red and wilted.
 */
export function PlotTile({
  landId,
  plot,
  activeKey,
  ownerLabel,
  bp,
  adjacencyBonusBp,
  selected,
  isNext,
  onSelect,
}: PlotTileProps) {
  const state = plotState(plot)
  const mine = !!plot && plot.owner === activeKey && activeKey !== ''
  const progress = plot ? bpFraction(plot.progress_bp, bp) : 0
  const blighted = !!plot && plot.blight_bp > 0

  // The plant's scale: never invisible, never bursting out of the tile.
  const scale = state === 'ready' ? 1 : 0.34 + 0.66 * progress

  // Adjacency is per neighbour: adjacent_owned × adjacency_bonus_bp.
  const adjacencyBp = plot ? plot.adjacent_owned * adjacencyBonusBp : 0
  const label = describe(plot, state, landId, ownerLabel, bp, adjacencyBp)

  return (
    <button
      type="button"
      className={cx(
        'plot',
        `plot--${state}`,
        progress >= 0.5 && state === 'growing' && 'plot--half',
        blighted && 'plot--blighted',
        mine && 'is-mine',
        selected && 'is-selected',
        isNext && state === 'unminted' && 'plot--next',
        'is-interactive',
        !mine && state !== 'unminted' && 'is-dim',
      )}
      style={cssVars({
        '--p': progress.toFixed(3),
        '--g': scale.toFixed(3),
        '--hue': plot ? hueForKey(plot.owner) : 0,
      })}
      onClick={() => onSelect(landId)}
      title={label}
      aria-label={label}
    >
      <span className="plot__field" aria-hidden="true" />
      {blighted ? <span className="plot__overlay" aria-hidden="true" /> : null}

      <span className="plot__id">{landId}</span>

      {plot ? (
        <>
          <span className="plot__fert" title={`fertility ${formatMultiplier(plot.fertility_bp, bp)}`}>
            {formatMultiplier(plot.fertility_bp, bp)}
          </span>
          {plot.adjacent_owned > 0 ? (
            <span
              className="plot__adj"
              title={`${plot.adjacent_owned} adjacent ${
                plot.adjacent_owned === 1 ? 'plot' : 'plots'
              } share this owner: +${formatPercent(adjacencyBp, bp)} yield`}
            >
              +{formatPercent(adjacencyBp, bp)}
            </span>
          ) : null}

          {plot.is_planted ? (
            <span className="crop" aria-hidden="true">
              <i className="crop__stem" />
              <i className="crop__leaf crop__leaf--a" />
              <i className="crop__leaf crop__leaf--b" />
              <i className="crop__leaf crop__leaf--c" />
              <i className="crop__leaf crop__leaf--d" />
              <i className="crop__tuber" />
            </span>
          ) : null}

          <span className="plot__footer">
            <span className="plot__owner">
              <i className="dot" />
              {ownerLabel}
            </span>
            <span className="plot__state">
              {state === 'ready'
                ? 'READY'
                : state === 'growing'
                  ? `${Math.round(progress * 100)}%`
                  : 'fallow'}
            </span>
          </span>

          {plot.is_planted ? (
            <span className="plot__meter" aria-hidden="true">
              <i />
            </span>
          ) : null}

          {blighted ? (
            <span className="plot__blight-badge">blight −{formatPercent(plot.blight_bp, bp)}</span>
          ) : null}
        </>
      ) : (
        <span className="plot__unminted-label">{isNext ? 'next parcel' : 'unclaimed'}</span>
      )}
    </button>
  )
}

function describe(
  plot: Plot | null,
  state: string,
  landId: number,
  ownerLabel: string,
  bp: number,
  adjacencyBp: number,
): string {
  if (!plot) return `Plot ${landId} — unclaimed land, mintable with buy_land`
  const parts = [
    `Plot ${landId} (${plot.x},${plot.y})`,
    `owner ${ownerLabel}`,
    `fertility ${formatMultiplier(plot.fertility_bp, bp)}`,
  ]
  if (state === 'ready') parts.push('crop ready to harvest')
  else if (state === 'growing') parts.push(`growing, ready at block ${plot.ready_at}`)
  else parts.push('fallow, nothing planted')
  if (plot.blight_bp > 0) parts.push(`blighted −${formatPercent(plot.blight_bp, bp)}`)
  if (plot.adjacent_owned > 0) {
    parts.push(`${plot.adjacent_owned} adjacent owned, +${formatPercent(adjacencyBp, bp)} yield`)
  }
  return parts.join(' · ')
}
