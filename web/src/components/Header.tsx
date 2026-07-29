import type { ChainInfo } from '../api/types.ts'
import { formatCount, shortHash } from '../lib/format.ts'
import { Fact } from './Panel.tsx'

interface HeaderProps {
  chain: ChainInfo | null
  online: boolean
  mining: boolean
  /** Blocks the mine button while the node is unreachable. */
  onMine: () => void
  lastMinedIndex: number | null
}

/**
 * Sticky top bar: the chain's vitals plus the mine button.
 *
 * Mining is how this world advances — crops only grow and boniatos only age
 * when a block lands — so the button is the loudest thing on the page.
 */
export function Header({ chain, online, mining, onMine, lastMinedIndex }: HeaderProps) {
  const mempool = chain?.mempool ?? 0

  return (
    <div className="topbar">
      <div className="topbar__inner">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <span>
            <span className="brand__name">Boniato Chain</span>
            <br />
            <span className="brand__tag">crop to earn</span>
          </span>
        </div>

        <div className="headline">
          <span className="headline__height">{chain ? formatCount(chain.height) : '—'}</span>
          <span className="headline__label">height</span>
        </div>

        <div className="facts">
          <Fact
            label="head"
            value={<span className="mono">{shortHash(chain?.head_hash ?? '')}</span>}
            title={chain?.head_hash || 'unknown'}
            strong
          />
          <Fact
            label="state root"
            value={<span className="mono">{shortHash(chain?.state_root ?? '')}</span>}
            title={chain?.state_root || 'unknown'}
          />
          <Fact label="difficulty" value={chain ? formatCount(chain.difficulty) : '—'} />
          <Fact
            label="mempool"
            value={
              <span className={mempool > 0 ? 'chip chip--gold' : undefined}>
                {chain ? `${formatCount(mempool)} pending` : '—'}
              </span>
            }
            title="Intents waiting for a block. Nothing has taken effect yet."
          />
        </div>

        <div className="topbar__actions">
          <span className={`status ${online ? 'status--online' : 'status--offline'}`}>
            <span className="dot" />
            {online ? 'node online' : 'node offline'}
          </span>
          <button
            type="button"
            className={`btn btn--primary btn--mine${mining ? ' is-busy' : ''}`}
            onClick={onMine}
            disabled={mining || !online}
            title={
              online
                ? 'Mine every pending transaction into the next block (POST /api/mine)'
                : 'The node is unreachable'
            }
          >
            <span className="pick" aria-hidden="true" />
            {mining ? 'Mining…' : 'Mine block'}
          </button>
          {lastMinedIndex !== null ? (
            <span className="tiny faint mono nowrap">#{lastMinedIndex} mined</span>
          ) : null}
        </div>
      </div>
    </div>
  )
}
