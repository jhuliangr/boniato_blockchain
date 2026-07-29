import { useMemo, useState } from 'react'
import type { Account, ActionIntent, ChainInfo, Plot, Wallet } from '../api/types.ts'
import { quoteFertilizer } from '../lib/farm.ts'
import { displayName, formatAmount, formatDuration, parseAmount, shortKey } from '../lib/format.ts'
import { Panel } from './Panel.tsx'

interface ActionPanelProps {
  chain: ChainInfo | null
  account: Account | null
  wallets: Wallet[]
  activeKey: string
  /** Plots owned by the active wallet with a crop still growing. */
  growingPlots: Plot[]
  busy: boolean
  onSubmit: (intent: ActionIntent, description: string) => void
}

const CUSTOM = '__custom__'

/**
 * Everything that is not tied to clicking a specific plot: claim, transfer,
 * buy land, fertilize. Each button POSTs an intent to `/api/actions`; a 202
 * only means "queued", so the copy keeps pointing at the activity feed.
 */
export function ActionPanel({
  chain,
  account,
  wallets,
  activeKey,
  growingPlots,
  busy,
  onSubmit,
}: ActionPanelProps) {
  const bu = chain?.base_units ?? 1000
  const gas = chain?.economy.gas_fee ?? 0

  const others = useMemo(
    () => wallets.filter((wallet) => wallet.public_key !== activeKey),
    [wallets, activeKey],
  )

  const [recipient, setRecipient] = useState('')
  const [customKey, setCustomKey] = useState('')
  const [amountText, setAmountText] = useState('')
  const [fertLand, setFertLand] = useState('')
  const [fertText, setFertText] = useState('')

  const disabled = busy || activeKey === ''
  const balance = account?.balance ?? 0
  const fertilizer = account?.fertilizer ?? 0

  const amount = parseAmount(amountText, bu)
  const amountInvalid = amountText.trim() !== '' && amount === null
  const to = recipient === CUSTOM ? customKey.trim().toLowerCase() : recipient
  const transferTooMuch = amount !== null && amount + gas > balance
  const canTransfer = !disabled && to !== '' && amount !== null && amount > 0 && !transferTooMuch

  const fertAmount = parseAmount(fertText, bu)
  const fertPlot = growingPlots.find((plot) => String(plot.land_id) === fertLand) ?? null
  const fertQuote =
    fertPlot && chain && fertAmount !== null && fertAmount > 0
      ? quoteFertilizer(fertPlot, fertAmount, chain)
      : null
  const canFertilize =
    !disabled &&
    fertPlot !== null &&
    fertPlot.fertilizer_headroom_blocks > 0 &&
    fertAmount !== null &&
    fertAmount > 0 &&
    fertAmount <= fertilizer

  const price = chain?.economy.next_land_price ?? 0
  const canBuy = !disabled && balance >= price + gas
  // The chain publishes which parcel is on sale; buy_land takes no argument.
  const rawNextLandId = chain?.economy.next_land_id ?? -1
  const nextLandId = rawNextLandId >= 0 ? rawNextLandId : null

  return (
    <Panel
      title="Actions"
      aside={<span className="tiny">gas {chain ? formatAmount(gas, bu) : '—'} each</span>}
    >
      {/* ------------------------------------------------------------ claim */}
      <div className="action-group">
        <div className="action-group__title">
          <span className="action-group__name">claim</span>
          {account?.claimed ? (
            <span className="chip">starter kit taken</span>
          ) : (
            <span className="chip chip--ok">starter kit available</span>
          )}
        </div>
        <button
          type="button"
          className="btn btn--leaf btn--block"
          disabled={disabled}
          onClick={() => onSubmit({ public_key: activeKey, type: 'claim' }, 'claim')}
        >
          {account?.claimed ? 'Claim relief grant' : 'Claim starter kit'}
        </button>
        <p className="hint" style={{ marginTop: 6 }}>
          {account?.claimed
            ? `Already claimed. A relief grant of ${formatAmount(
                chain?.economy.relief_balance ?? 0,
                bu,
              )} $BONI is only paid when the balance is zero.`
            : `One plot plus ${formatAmount(chain?.economy.starter_balance ?? 0, bu)} $BONI, once per account.`}
        </p>
      </div>

      {/* --------------------------------------------------------- transfer */}
      <div className="action-group">
        <div className="action-group__title">
          <span className="action-group__name">transfer</span>
          <span className="chip">oldest lots first</span>
        </div>
        <div className="stack">
          <div className="field">
            <label className="field__label" htmlFor="transfer-to">
              recipient
            </label>
            <select
              id="transfer-to"
              className="select"
              value={recipient}
              onChange={(event) => setRecipient(event.target.value)}
            >
              <option value="">select a wallet…</option>
              {others.map((wallet) => (
                <option key={wallet.public_key} value={wallet.public_key}>
                  {displayName(wallet.public_key, wallet.label)} — {shortKey(wallet.public_key)}
                </option>
              ))}
              <option value={CUSTOM}>paste a public key…</option>
            </select>
          </div>

          {recipient === CUSTOM ? (
            <input
              className="input mono"
              placeholder="recipient public key (hex)"
              value={customKey}
              onChange={(event) => setCustomKey(event.target.value)}
              aria-label="Recipient public key"
            />
          ) : null}

          <div className="field">
            <label className="field__label" htmlFor="transfer-amount">
              amount ($BONI)
            </label>
            <div className="row">
              <input
                id="transfer-amount"
                className={`input input--amount${amountInvalid ? ' is-invalid' : ''}`}
                placeholder="0.000"
                inputMode="decimal"
                value={amountText}
                onChange={(event) => setAmountText(event.target.value)}
              />
              <button
                type="button"
                className="btn btn--sm"
                onClick={() =>
                  setAmountText(formatAmount(Math.max(0, balance - gas), bu, { group: false }))
                }
                title="Everything except the gas fee"
              >
                max
              </button>
            </div>
          </div>

          <button
            type="button"
            className="btn btn--block"
            disabled={!canTransfer}
            onClick={() => {
              if (amount === null) return
              onSubmit(
                { public_key: activeKey, type: 'transfer', to, amount },
                `transfer ${formatAmount(amount, bu)} to ${shortKey(to)}`,
              )
            }}
          >
            Send $BONI
          </button>
          <p className={`hint${transferTooMuch ? ' hint--bad' : ''}`}>
            {amountInvalid
              ? `Use at most ${String(bu).length - 1} decimals.`
              : transferTooMuch
                ? `Balance ${formatAmount(balance, bu)} does not cover the amount plus gas.`
                : 'The recipient inherits each lot’s expiry — you cannot launder a rotting boniato.'}
          </p>
        </div>
      </div>

      {/* -------------------------------------------------------- buy land */}
      <div className="action-group">
        <div className="action-group__title">
          <span className="action-group__name">buy_land</span>
          <span className="chip chip--gold">{formatAmount(price, bu)} $BONI</span>
          {nextLandId !== null ? <span className="chip">plot #{nextLandId}</span> : null}
        </div>
        <button
          type="button"
          className="btn btn--block"
          disabled={!canBuy}
          onClick={() =>
            onSubmit(
              { public_key: activeKey, type: 'buy_land' },
              nextLandId !== null ? `buy plot #${nextLandId}` : 'buy land',
            )
          }
        >
          {nextLandId !== null ? `Buy parcel #${nextLandId}` : 'Buy the next parcel'}
        </button>
        <p className={`hint${!canBuy && activeKey !== '' ? ' hint--warn' : ''}`}>
          {activeKey !== '' && !canBuy
            ? `Needs ${formatAmount(price + gas, bu)} $BONI including gas; you hold ${formatAmount(balance, bu)}.`
            : 'Parcels are minted in order, so the chain picks which one. The price is read from state when the block is mined, and rises with every sale.'}
        </p>
      </div>

      {/* -------------------------------------------------------- fertilize */}
      <div className="action-group">
        <div className="action-group__title">
          <span className="action-group__name">fertilize</span>
          <span className="chip">{formatAmount(fertilizer, bu)} in store</span>
        </div>
        {growingPlots.length === 0 ? (
          <p className="hint">No growing crop to fertilize. Plant something first.</p>
        ) : (
          <div className="stack">
            <div className="field">
              <label className="field__label" htmlFor="fert-plot">
                plot
              </label>
              <select
                id="fert-plot"
                className="select"
                value={fertLand}
                onChange={(event) => setFertLand(event.target.value)}
              >
                <option value="">select a growing crop…</option>
                {growingPlots.map((plot) => (
                  <option key={plot.land_id} value={String(plot.land_id)}>
                    #{plot.land_id} — ready at block {plot.ready_at}
                    {plot.fertilizer_headroom_blocks > 0
                      ? ` (−${plot.fertilizer_headroom_blocks} max)`
                      : ' (no headroom)'}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field__label" htmlFor="fert-amount">
                fertilizer
              </label>
              <div className="row">
                <input
                  id="fert-amount"
                  className="input input--amount"
                  placeholder="0.000"
                  inputMode="decimal"
                  value={fertText}
                  onChange={(event) => setFertText(event.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={!fertPlot || fertPlot.fertilizer_headroom_cost <= 0}
                  onClick={() => {
                    if (!fertPlot) return
                    const needed = Math.min(fertilizer, fertPlot.fertilizer_headroom_cost)
                    setFertText(formatAmount(needed, bu, { group: false }))
                  }}
                  title="The node's quote for removing this crop's whole remaining headroom"
                >
                  max
                </button>
              </div>
            </div>
            <button
              type="button"
              className="btn btn--block"
              disabled={!canFertilize}
              onClick={() => {
                if (!fertPlot || fertAmount === null) return
                onSubmit(
                  {
                    public_key: activeKey,
                    type: 'fertilize',
                    land_id: fertPlot.land_id,
                    amount: fertAmount,
                  },
                  `fertilize #${fertPlot.land_id}`,
                )
              }}
            >
              Spread fertilizer
            </button>
            <p className={`hint${fertQuote?.cappedOut ? ' hint--warn' : ''}`}>
              {fertQuote
                ? `${fertQuote.blocksCut} ${
                    fertQuote.blocksCut === 1 ? 'block' : 'blocks'
                  } earlier — ready at #${fertQuote.readyAt}, ${formatDuration(
                    fertQuote.blocksCut,
                    chain?.economy.blocks_per_day ?? 144,
                  )} sooner. Spends ${formatAmount(fertQuote.consumed, bu)}${
                    fertQuote.refunded > 0
                      ? `, ${formatAmount(fertQuote.refunded, bu)} refunded`
                      : ''
                  }.`
                : fertPlot
                  ? `The node quotes ${formatAmount(
                      fertPlot.fertilizer_headroom_cost,
                      bu,
                    )} to remove all ${fertPlot.fertilizer_headroom_blocks} remaining blocks.`
                  : `Brings a crop forward, never below half its nominal growth time. ${
                      chain?.economy.growth_blocks_per_fertilizer ?? '—'
                    } blocks per whole unit.`}
            </p>
          </div>
        )}
      </div>

      <p className="hint" style={{ marginTop: 4 }}>
        Every action is queued in the mempool. Nothing happens until you <b>mine a block</b>, and a
        rejected action still pays gas — watch the activity feed.
      </p>
    </Panel>
  )
}
