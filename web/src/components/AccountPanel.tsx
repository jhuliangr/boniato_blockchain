import type { Account, ChainInfo } from '../api/types.ts'
import { displayName, formatAmount, hueForKey, shortKey } from '../lib/format.ts'
import { cssVars } from '../lib/style.ts'
import { Panel } from './Panel.tsx'

interface AccountPanelProps {
  account: Account | null
  chain: ChainInfo | null
  activeKey: string
  error: string | null
  onSelectPlot: (landId: number) => void
}

/** Balance, fertilizer and land for the wallet currently being played. */
export function AccountPanel({ account, chain, activeKey, error, onSelectPlot }: AccountPanelProps) {
  const bu = chain?.base_units ?? 1000

  if (activeKey === '') {
    return (
      <Panel title="Active farmer">
        <p className="empty-note">Pick or create a wallet to start farming.</p>
      </Panel>
    )
  }

  return (
    <Panel
      title="Active farmer"
      aside={
        <span className="row">
          <span
            className="wallet__avatar"
            style={cssVars({ '--hue': hueForKey(activeKey), width: 14, height: 14 })}
            aria-hidden="true"
          />
          <span title={activeKey}>
            {displayName(activeKey, account?.label)}{' '}
            <span className="mono faint">{shortKey(activeKey)}</span>
          </span>
        </span>
      }
      error={error}
      flush
    >
      <div className="account">
        <div className="account__cell">
          <span className="account__key">balance</span>
          <div className="account__value">{account ? formatAmount(account.balance, bu) : '—'}</div>
          <span className="tiny faint">$BONI across all lots</span>
        </div>
        <div className="account__cell">
          <span className="account__key">fertilizer</span>
          <div className="account__value account__value--sub">
            {account ? formatAmount(account.fertilizer, bu) : '—'}
          </div>
          <span className="tiny faint">non-perishable, non-transferable</span>
        </div>

        <div className="account__cell" style={{ gridColumn: '1 / -1' }}>
          <span className="account__key">
            land {account ? `(${account.plots.length})` : ''} ·{' '}
            {account?.claimed ? (
              <span className="chip chip--ok">claimed</span>
            ) : (
              <span className="chip chip--warn">not claimed yet</span>
            )}
          </span>
        </div>

        {account && account.plots.length > 0 ? (
          <div className="account__plots">
            {account.plots.map((landId) => (
              <button
                key={landId}
                type="button"
                className="plot-tag"
                onClick={() => onSelectPlot(landId)}
                title={`Select plot ${landId}`}
              >
                #{landId}
              </button>
            ))}
          </div>
        ) : (
          <p className="account__plots tiny faint" style={{ margin: 0 }}>
            No plots yet — <b>claim</b> a starter kit or <b>buy land</b>.
          </p>
        )}
      </div>
    </Panel>
  )
}
