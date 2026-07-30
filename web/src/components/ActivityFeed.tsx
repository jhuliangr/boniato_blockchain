import { useCallback, useMemo, useState } from 'react'
import type { ChainInfo, Receipt, Wallet } from '../api/types.ts'
import { displayName, formatAmount, hueForKey, plural, shortHash } from '../lib/format.ts'
import { receiptChips } from '../lib/receipt.ts'
import { cssVars, cx } from '../lib/style.ts'
import { Panel } from './Panel.tsx'

interface ActivityFeedProps {
  receipts: Receipt[]
  chain: ChainInfo | null
  activeKey: string
  /** Used to name the accounts a receipt mentions by public key. */
  wallets: Wallet[]
  error: string | null
}

const SYSTEM_ACTIONS = new Set(['blight', 'rot'])

/**
 * The receipt feed, newest first.
 *
 * Rejections are the interesting part: they show the rule that stopped you and
 * they still cost gas. Receipts name their signer (`public_key` / `label`), so
 * the feed can be filtered to the wallet being played. System events (`blight`,
 * `rot`) arrive here too — nobody signs them, though `rot` names the account it
 * happened to.
 */
export function ActivityFeed({
  receipts,
  chain,
  activeKey,
  wallets,
  error,
}: ActivityFeedProps) {
  const bu = chain?.base_units ?? 1000
  const [mineOnly, setMineOnly] = useState(false)

  // Receipts carry full public keys, so naming one is an exact lookup against
  // the node's wallets, falling back to the key's tail for a stranger.
  const nameKey = useCallback(
    (key: string) => {
      const wallet = wallets.find((candidate) => candidate.public_key === key)
      return displayName(key, wallet?.label)
    },
    [wallets],
  )

  const mineCount = useMemo(
    () =>
      activeKey === '' ? 0 : receipts.filter((receipt) => receipt.public_key === activeKey).length,
    [receipts, activeKey],
  )

  const shown =
    mineOnly && activeKey !== ''
      ? receipts.filter((receipt) => receipt.public_key === activeKey)
      : receipts

  const rejected = shown.filter(
    (receipt) => !receipt.ok && !SYSTEM_ACTIONS.has(receipt.action),
  ).length
  const hidden = receipts.length - shown.length

  return (
    <Panel
      title="Activity"
      aside={
        <span className="row">
          {activeKey !== '' ? (
            <button
              type="button"
              className={cx('btn', 'btn--sm', !mineOnly && 'btn--ghost')}
              onClick={() => setMineOnly((value) => !value)}
              aria-pressed={mineOnly}
              title="Receipts name their signer, so the feed can be filtered by wallet"
            >
              {mineOnly ? 'mine only' : `all · ${mineCount} mine`}
            </button>
          ) : null}
          <span className="nowrap">{shown.length} receipts</span>
          {rejected > 0 ? <span className="chip chip--bad">{rejected} rejected</span> : null}
        </span>
      }
      error={error}
      flush
    >
      <div className="feed scroll-y">
        {shown.length === 0 ? (
          <p className="empty-note">
            {mineOnly
              ? 'This wallet has not done anything yet.'
              : 'Nothing has happened yet. Submit an action and mine a block.'}
          </p>
        ) : (
          shown.map((receipt, index) => {
            const system = SYSTEM_ACTIONS.has(receipt.action)
            const mine = activeKey !== '' && receipt.public_key === activeKey
            const chips = receiptChips(receipt, chain, nameKey)
            return (
              <article
                key={`${receipt.height}-${receipt.tx_id}-${receipt.action}-${index}`}
                className={cx(
                  'event',
                  system
                    ? `event--system event--${receipt.action}`
                    : receipt.ok
                      ? 'event--ok'
                      : 'event--bad',
                  mine && 'event--mine',
                )}
              >
                <span className="event__action">{receipt.action}</span>
                <span className="event__height">
                  #{receipt.height}
                  {receipt.tx_id ? ` · ${shortHash(receipt.tx_id, 6, 4)}` : ' · system'}
                </span>

                <div className="event__body">
                  {receipt.public_key !== '' ? (
                    <span
                      className={cx('signer', mine && 'signer--mine')}
                      style={cssVars({ '--hue': hueForKey(receipt.public_key) })}
                      title={receipt.public_key}
                    >
                      <i className="dot" />
                      {displayName(receipt.public_key, receipt.label)}
                    </span>
                  ) : (
                    <span className="chip" title="Performed by the chain itself">
                      chain
                    </span>
                  )}
                  {!receipt.ok && !system ? <span className="chip chip--bad">rejected</span> : null}
                  {receipt.minted > 0 ? (
                    <span className="kv">
                      minted <b>+{formatAmount(receipt.minted, bu)}</b>
                    </span>
                  ) : null}
                  {receipt.burned > 0 ? (
                    <span className="kv">
                      burned <b>−{formatAmount(receipt.burned, bu)}</b>
                    </span>
                  ) : null}
                  {receipt.gas_burned > 0 ? (
                    <span className="kv faint">gas {formatAmount(receipt.gas_burned, bu)}</span>
                  ) : null}
                  {chips.map((chip) => (
                    <span className="kv" key={chip.key} title={chip.title}>
                      {chip.label ? `${chip.label} ` : ''}
                      <b>{chip.value}</b>
                    </span>
                  ))}
                </div>

                {!receipt.ok && receipt.reason ? (
                  <p className="event__reason">↳ {receipt.reason}</p>
                ) : null}
              </article>
            )
          })
        )}
      </div>
      {mineOnly && hidden > 0 ? (
        <p className="hint" style={{ padding: '7px 12px' }}>
          {hidden} {plural(hidden, 'receipt')} from other farmers and the chain hidden.
        </p>
      ) : null}
    </Panel>
  )
}
