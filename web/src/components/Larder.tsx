import type { Account, ChainInfo } from '../api/types.ts'
import { freshnessTier, lotFreshnessBp, summariseLarder } from '../lib/farm.ts'
import { bpFraction, formatAmount, formatDuration, formatPercent, plural } from '../lib/format.ts'
import { cx } from '../lib/style.ts'
import { Panel } from './Panel.tsx'

interface LarderProps {
  account: Account | null
  chain: ChainInfo | null
  error: string | null
}

/**
 * The perishable inventory — the mechanic that makes this chain unusual.
 *
 * A balance is not a number, it is a queue of lots each with its own expiry
 * height. Spending drains the soonest-to-rot lot first, and whatever rots is
 * destroyed and partly returned as fertilizer. So the larder is ordered by
 * expiry and shouts when something is about to turn to compost.
 */
export function Larder({ account, chain, error }: LarderProps) {
  const bu = chain?.base_units ?? 1000
  const bp = chain?.bp ?? 10000
  const rotBlocks = chain?.economy.rot_blocks ?? 1440
  const blocksPerDay = chain?.economy.blocks_per_day ?? 144
  const compostRate = chain?.economy.rot_fertilizer_bp ?? 0
  const { total, lots, atRisk, soonest } = summariseLarder(account, chain)

  return (
    <Panel
      title="The larder"
      aside={`${lots.length} ${plural(lots.length, 'lot')} · soonest to rot first`}
      error={error}
      accent={atRisk > 0}
      flush
    >
      <div className="larder__summary">
        <span className="larder__figure">
          <span className="fact__key">in store</span>
          <b>{account ? formatAmount(total, bu) : '—'}</b>
        </span>
        <span className="larder__figure">
          <span className="fact__key">at risk</span>
          <b style={{ color: atRisk > 0 ? 'var(--warn)' : 'var(--text-dim)' }}>
            {account ? formatAmount(atRisk, bu) : '—'}
          </b>
        </span>
        <span className="larder__figure">
          <span className="fact__key">next rot</span>
          <b
            style={{
              color:
                soonest && soonest.blocks_left * 10 <= rotBlocks ? 'var(--danger)' : 'var(--text)',
            }}
          >
            {soonest ? `${Math.max(0, soonest.blocks_left)} blocks` : '—'}
          </b>
        </span>
        <span className="larder__figure grow">
          <span className="fact__key">on rot</span>
          <span className="tiny muted">
            {chain
              ? `destroyed, ${formatPercent(compostRate, bp)} returned as fertilizer`
              : 'destroyed, part returned as fertilizer'}
          </span>
        </span>
      </div>

      <div className="lots scroll-y">
        {lots.length === 0 ? (
          <p className="empty-note">
            Nothing in store. Harvest a ready crop — or <b>claim</b> a starter kit — to fill the larder.
          </p>
        ) : (
          lots.map((lot, index) => {
            const freshness = lotFreshnessBp(lot, rotBlocks, bp)
            const tier = freshnessTier(freshness, bp)
            const left = Math.max(0, lot.blocks_left)
            return (
              <div
                key={`${lot.expires_at}-${index}`}
                className={cx('lot', `lot--${tier}`)}
                title={`expires at block ${lot.expires_at}`}
              >
                <span className="lot__tuber" aria-hidden="true" />
                <span className="lot__amount">{formatAmount(lot.amount, bu)}</span>
                <span className="lot__when">
                  {left === 0 ? (
                    <b style={{ color: 'var(--danger)' }}>rotting now</b>
                  ) : (
                    <>
                      <b className="mono">{left}</b> {plural(left, 'block')}
                      <br />
                      <span className="tiny faint">
                        ≈ {formatDuration(left, blocksPerDay)} · #{lot.expires_at}
                      </span>
                    </>
                  )}
                </span>
                <span className="meter lot__bar" aria-hidden="true">
                  <i style={{ width: `${(bpFraction(freshness, bp) * 100).toFixed(1)}%` }} />
                </span>
              </div>
            )
          })
        )}
      </div>

      <p className="hint" style={{ padding: '9px 13px' }}>
        Transfers and purchases spend the top lot first. A lot's freshness bar is its remaining share
        of the {chain ? `${rotBlocks}-block` : ''} rot window.
      </p>
    </Panel>
  )
}
