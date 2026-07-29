import type { BlockSummary } from '../api/types.ts'
import { formatClock, plural, shortHash } from '../lib/format.ts'
import { Panel } from './Panel.tsx'

interface RecentBlocksProps {
  blocks: BlockSummary[]
  error: string | null
}

/** `GET /api/blocks`, newest first — the proof-of-work trail. */
export function RecentBlocks({ blocks, error }: RecentBlocksProps) {
  return (
    <Panel title="Recent blocks" aside={`${blocks.length} shown`} error={error} flush>
      <div className="blocks scroll-y">
        {blocks.length === 0 ? (
          <p className="empty-note">No blocks fetched yet.</p>
        ) : (
          blocks.map((block) => (
            <div className="block" key={`${block.index}-${block.hash}`}>
              <span className="block__index">#{block.index}</span>
              <span className="block__hash truncate" title={block.hash}>
                {shortHash(block.hash, 12, 6)}
              </span>
              <span className="block__txs">
                {block.tx_count} {plural(block.tx_count, 'tx', 'txs')}
              </span>
              <span className="block__meta">
                <span title="nonce found by the miner">nonce {block.nonce}</span>
                <span title={`merkle root ${block.merkle_root}`}>
                  merkle {shortHash(block.merkle_root, 6, 4)}
                </span>
                <span title={`state root ${block.state_root}`}>
                  state {shortHash(block.state_root, 6, 4)}
                </span>
                <span className="grow" />
                <span>{formatClock(block.timestamp)}</span>
              </span>
            </div>
          ))
        )}
      </div>
    </Panel>
  )
}
