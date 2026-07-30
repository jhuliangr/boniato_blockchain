import { useState } from 'react'
import type { Wallet } from '../api/types.ts'
import { displayName, hueForKey, shortKey } from '../lib/format.ts'
import { cssVars, cx } from '../lib/style.ts'
import { Panel } from './Panel.tsx'

interface WalletPanelProps {
  wallets: Wallet[]
  activeKey: string
  error: string | null
  creating: boolean
  onSelect: (publicKey: string) => void
  onCreate: (label: string) => void
}

/**
 * The node's demo keyring (`GET /api/wallets`). This is a custodial wallet:
 * the node holds every key, so "switching wallet" is purely a client-side
 * choice of which public key to act as.
 */
export function WalletPanel({
  wallets,
  activeKey,
  error,
  creating,
  onSelect,
  onCreate,
}: WalletPanelProps) {
  const [label, setLabel] = useState('')

  const submit = () => {
    if (creating) return
    onCreate(label)
    setLabel('')
  }

  return (
    <Panel
      title="Wallets"
      aside={`${wallets.length} on this node`}
      error={error}
      flush
    >
      <div className="wallet-list scroll-y">
        {wallets.length === 0 ? (
          <p className="empty-note">
            No wallets yet. Create one, then <b>claim</b> a starter kit.
          </p>
        ) : (
          wallets.map((wallet) => {
            const active = wallet.public_key === activeKey
            return (
              <button
                key={wallet.public_key}
                type="button"
                className={cx('wallet', active && 'is-active')}
                onClick={() => onSelect(wallet.public_key)}
                aria-pressed={active}
                title={wallet.public_key}
              >
                <span
                  className="wallet__avatar"
                  style={cssVars({ '--hue': hueForKey(wallet.public_key) })}
                  aria-hidden="true"
                />
                <span className="grow">
                  <span className="wallet__name truncate">
                    {displayName(wallet.public_key, wallet.label)}
                  </span>
                  <br />
                  {/* Tail, not head: every IPv8 key shares a long ASN.1 prefix. */}
                  <span className="wallet__key">{shortKey(wallet.public_key, 12)}</span>
                </span>
                {active ? <span className="chip chip--gold">active</span> : null}
              </button>
            )
          })
        )}
      </div>

      <div className="panel__body">
        <div className="row">
          <input
            className="input"
            placeholder="new wallet label (optional)"
            value={label}
            maxLength={32}
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submit()
            }}
            aria-label="New wallet label"
          />
          <button type="button" className="btn btn--sm" onClick={submit} disabled={creating}>
            {creating ? '…' : 'Create'}
          </button>
        </div>
        <p className="hint" style={{ marginTop: 6 }}>
          Keys live in the node's memory only — restarting it loses every wallet.
        </p>
      </div>
    </Panel>
  )
}
