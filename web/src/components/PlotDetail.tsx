import { useEffect, useState } from 'react'
import type { Account, ActionIntent, ChainInfo, Plot } from '../api/types.ts'
import {
  adjacencyBonusBp,
  blocksUntilReady,
  plotActions,
  plotState,
  quoteFertilizer,
} from '../lib/farm.ts'
import {
  bpFraction,
  formatAmount,
  formatDuration,
  formatMultiplier,
  formatPercent,
  parseAmount,
} from '../lib/format.ts'
import { Fact, Panel } from './Panel.tsx'
import { PlotTile } from './PlotTile.tsx'

interface PlotDetailProps {
  landId: number | null
  plot: Plot | null
  chain: ChainInfo | null
  account: Account | null
  activeKey: string
  ownerLabel: string
  busy: boolean
  onSubmit: (intent: ActionIntent, description: string) => void
  onClear: () => void
}

/**
 * Details and actions for the selected plot. Which buttons appear is derived
 * from the plot's state (fallow -> plant, ready -> harvest, growing ->
 * fertilize), and only for land the active wallet owns.
 */
export function PlotDetail({
  landId,
  plot,
  chain,
  account,
  activeKey,
  ownerLabel,
  busy,
  onSubmit,
  onClear,
}: PlotDetailProps) {
  const [fertilizerText, setFertilizerText] = useState('')

  useEffect(() => {
    setFertilizerText('')
  }, [landId])

  if (landId === null) {
    return (
      <Panel title="Selected plot">
        <p className="empty-note">
          Click a plot on the map. Plots you own offer <b>plant</b>, <b>harvest</b> and{' '}
          <b>fertilize</b> depending on what is growing.
        </p>
      </Panel>
    )
  }

  const bu = chain?.base_units ?? 1000
  const bp = chain?.bp ?? 10000
  const height = chain?.height ?? 0
  const state = plotState(plot)
  const actions = plotActions(plot, activeKey)
  const fertilizerAmount = parseAmount(fertilizerText, bu)
  const fertilizerInvalid = fertilizerText.trim() !== '' && fertilizerAmount === null
  const available = account?.fertilizer ?? 0
  const overspend = fertilizerAmount !== null && fertilizerAmount > available
  // The cap in this preview is the node's own quote for this plot, not a
  // client-side reimplementation of the growth floor.
  const quote =
    plot && chain && fertilizerAmount !== null && fertilizerAmount > 0
      ? quoteFertilizer(plot, fertilizerAmount, chain)
      : null
  const headroomBlocks = plot?.fertilizer_headroom_blocks ?? 0
  const headroomCost = plot?.fertilizer_headroom_cost ?? 0
  const rawNextLandId = chain?.economy.next_land_id ?? -1
  const nextLandId = rawNextLandId >= 0 ? rawNextLandId : null
  const isNextParcel = plot === null && nextLandId === landId

  return (
    <Panel
      title={`Plot #${landId}`}
      aside={
        <button type="button" className="btn btn--sm btn--ghost" onClick={onClear}>
          clear
        </button>
      }
      flush
    >
      <div className="plot-detail">
        <div className="plot-detail__preview">
          <PlotTile
            landId={landId}
            plot={plot}
            activeKey={activeKey}
            ownerLabel={ownerLabel}
            bp={bp}
            adjacencyBonusBp={chain?.economy.adjacency_bonus_bp ?? 0}
            selected={false}
            isNext={isNextParcel}
            onSelect={() => undefined}
          />
        </div>

        <div className="plot-detail__facts">
          {plot ? (
            <>
              <Fact
                label="owner"
                value={ownerLabel}
                title={plot.owner}
                strong={plot.owner === activeKey}
              />
              <Fact label="position" value={`(${plot.x}, ${plot.y})`} />
              <Fact label="fertility" value={formatMultiplier(plot.fertility_bp, bp)} />
              <Fact
                label="adjacency"
                value={
                  plot.adjacent_owned > 0
                    ? `+${formatPercent(adjacencyBonusBp(plot, chain), bp)}`
                    : 'none'
                }
                title={
                  plot.adjacent_owned > 0
                    ? `${plot.adjacent_owned} neighbouring ${
                        plot.adjacent_owned === 1 ? 'plot has' : 'plots have'
                      } the same owner, at +${formatPercent(
                        chain?.economy.adjacency_bonus_bp ?? 0,
                        bp,
                      )} each`
                    : 'No neighbouring plot shares this owner.'
                }
              />
              {state === 'growing' ? (
                <>
                  <Fact
                    label="ready at"
                    value={`#${plot.ready_at}`}
                    title={`planted at #${plot.planted_at}`}
                  />
                  <Fact
                    label="blocks left"
                    value={`${blocksUntilReady(plot, height)} (${Math.round(
                      bpFraction(plot.progress_bp, bp) * 100,
                    )}% grown)`}
                  />
                </>
              ) : null}
              {state === 'ready' ? <Fact label="status" value="harvestable" strong /> : null}
              {state === 'fallow' ? (
                <Fact label="status" value="fallow" title="Nothing planted here yet" />
              ) : null}
              {plot.blight_bp > 0 ? (
                <Fact label="blight" value={`−${formatPercent(plot.blight_bp, bp)} of this crop`} />
              ) : null}
              {state === 'growing' && headroomBlocks > 0 ? (
                <Fact
                  label="fertilizer headroom"
                  value={`${headroomBlocks} ${headroomBlocks === 1 ? 'block' : 'blocks'}`}
                  title={`Quoted by the node: ${formatAmount(
                    headroomCost,
                    bu,
                  )} fertilizer would remove ${headroomBlocks} blocks, the most this crop can still be rushed.`}
                />
              ) : null}
            </>
          ) : (
            <>
              <Fact
                label="status"
                value={isNextParcel ? 'unclaimed · on sale now' : 'unclaimed land'}
                strong={isNextParcel}
              />
              <Fact
                label="mint price"
                value={chain ? `${formatAmount(chain.economy.next_land_price, bu)} $BONI` : '—'}
              />
              {nextLandId !== null && !isNextParcel ? (
                <Fact label="next on sale" value={`#${nextLandId}`} />
              ) : null}
              <p className="hint" style={{ gridColumn: '1 / -1' }}>
                <b>buy_land</b> takes no plot id: parcels are minted sequentially, so the chain
                decides which one you get — currently{' '}
                {nextLandId !== null ? <b className="mono">#{nextLandId}</b> : 'unknown'}. The price
                rises with every sale.
              </p>
            </>
          )}

          <div className="plot-detail__actions">
            {actions.canPlant ? (
              <>
                <button
                  type="button"
                  className="btn btn--leaf"
                  disabled={busy}
                  onClick={() => onSubmit({ public_key: activeKey, type: 'plant', land_id: landId }, `plant on #${landId}`)}
                >
                  Plant seed
                </button>
                <span className="cost-note">
                  <span>
                    burns {chain ? formatAmount(chain.economy.seed_cost, bu) : '—'} seed
                  </span>
                  <span>+ {chain ? formatAmount(chain.economy.gas_fee, bu) : '—'} gas</span>
                  <span>
                    matures in {chain?.economy.growth_blocks ?? '—'} blocks
                  </span>
                </span>
              </>
            ) : null}

            {actions.canHarvest ? (
              <>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy}
                  onClick={() =>
                    onSubmit({ public_key: activeKey, type: 'harvest', land_id: landId }, `harvest #${landId}`)
                  }
                >
                  Harvest crop
                </button>
                <span className="cost-note">
                  <span>
                    yield {chain ? formatAmount(chain.economy.base_yield_min, bu) : '—'}–
                    {chain ? formatAmount(chain.economy.base_yield_max, bu) : '—'} × fertility
                  </span>
                  <span>fresh lot, rots in {chain?.economy.rot_blocks ?? '—'} blocks</span>
                </span>
              </>
            ) : null}

            {actions.canFertilize && plot ? (
              <div className="stack grow" style={{ minWidth: 220 }}>
                <div className="row">
                  <input
                    className={`input input--amount${fertilizerInvalid ? ' is-invalid' : ''}`}
                    placeholder="fertilizer"
                    inputMode="decimal"
                    value={fertilizerText}
                    onChange={(event) => setFertilizerText(event.target.value)}
                    aria-label="Fertilizer amount"
                  />
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={headroomCost <= 0}
                    onClick={() => {
                      // The node's quote: exactly this buys the quoted blocks
                      // with nothing refunded. Clamped to what we actually hold.
                      const needed = Math.min(available, headroomCost)
                      setFertilizerText(formatAmount(needed, bu, { group: false }))
                    }}
                    title={
                      headroomCost > 0
                        ? `The node's quote: ${formatAmount(
                            headroomCost,
                            bu,
                          )} removes all ${headroomBlocks} remaining blocks, with no refund`
                        : 'This crop cannot be rushed any further'
                    }
                  >
                    max
                  </button>
                  <button
                    type="button"
                    className="btn btn--leaf btn--sm"
                    disabled={
                      busy ||
                      fertilizerAmount === null ||
                      fertilizerAmount <= 0 ||
                      overspend ||
                      headroomBlocks <= 0
                    }
                    onClick={() => {
                      if (fertilizerAmount === null) return
                      onSubmit(
                        { public_key: activeKey, type: 'fertilize', land_id: landId, amount: fertilizerAmount },
                        `fertilize #${landId}`,
                      )
                    }}
                  >
                    Fertilize
                  </button>
                </div>
                <p
                  className={`hint${overspend || headroomBlocks <= 0 ? ' hint--bad' : ''}${
                    quote?.cappedOut ? ' hint--warn' : ''
                  }`}
                >
                  {headroomBlocks <= 0
                    ? 'No headroom left: this crop is already as early as the rules allow.'
                    : overspend
                      ? `You only hold ${formatAmount(available, bu)} fertilizer.`
                      : quote
                        ? `${quote.blocksCut} ${
                            quote.blocksCut === 1 ? 'block' : 'blocks'
                          } earlier — ready at #${quote.readyAt}, in ${formatDuration(
                            quote.blocksCut,
                            chain?.economy.blocks_per_day ?? 144,
                          )} less. Spends ${formatAmount(quote.consumed, bu)}${
                            quote.refunded > 0
                              ? `, ${formatAmount(quote.refunded, bu)} refunded`
                              : ' with no refund'
                          }.`
                        : `You hold ${formatAmount(
                            available,
                            bu,
                          )} fertilizer. The node quotes ${formatAmount(
                            headroomCost,
                            bu,
                          )} to remove all ${headroomBlocks} remaining ${
                            headroomBlocks === 1 ? 'block' : 'blocks'
                          }.`}
                </p>
              </div>
            ) : null}

            {plot && plot.owner !== activeKey ? (
              <p className="hint">
                This land belongs to {ownerLabel} — you can only work your own plots.
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </Panel>
  )
}
