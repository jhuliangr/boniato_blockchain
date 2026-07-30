import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_ORIGIN, api, errorMessage } from './api/client.ts'
import type { ActionIntent, Plot } from './api/types.ts'
import { AccountPanel } from './components/AccountPanel.tsx'
import { ActionPanel } from './components/ActionPanel.tsx'
import { ActivityFeed } from './components/ActivityFeed.tsx'
import { FarmGrid } from './components/FarmGrid.tsx'
import { Header } from './components/Header.tsx'
import { Larder } from './components/Larder.tsx'
import { PlotDetail } from './components/PlotDetail.tsx'
import { RecentBlocks } from './components/RecentBlocks.tsx'
import { SupplyPanel } from './components/SupplyPanel.tsx'
import { Toasts } from './components/Toasts.tsx'
import { WalletPanel } from './components/WalletPanel.tsx'
import { useChain } from './hooks/useChain.ts'
import { useResource } from './hooks/useResource.ts'
import { useStoredValue } from './hooks/useStoredValue.ts'
import { useToasts } from './hooks/useToasts.ts'
import { displayName, plural, shortHash } from './lib/format.ts'

const POLL_MS = 1000
const ACTIVITY_LIMIT = 40
const BLOCK_LIMIT = 12
const ACTIVE_KEY_STORAGE = 'boniato.activeKey'

/**
 * Composition root.
 *
 * One poller (`useChain`) is the app's clock; everything else refetches when
 * the height changes, which is the only moment anything else can change. A
 * manual `tick` covers the cases the height cannot see (a new wallet, a fresh
 * mempool) without turning the client into a busy-polling machine.
 */
export default function App() {
  const { chain, online, error: chainError, failures, refresh } = useChain(POLL_MS)
  const { toasts, push, dismiss } = useToasts()

  const [tick, setTick] = useState(0)
  const [activeKey, setActiveKey] = useStoredValue(ACTIVE_KEY_STORAGE)
  const [selectedLandId, setSelectedLandId] = useState<number | null>(null)
  const [mining, setMining] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [creatingWallet, setCreatingWallet] = useState(false)
  const [lastMinedIndex, setLastMinedIndex] = useState<number | null>(null)

  const height = chain?.height ?? -1
  const gridWidth = chain?.economy.grid_width ?? 8
  /** Every resource keys off this: a new block, or an explicit refresh. */
  const revision = `${height}:${tick}`

  const wallets = useResource((signal) => api.wallets(signal), [revision])
  const farmMap = useResource((signal) => api.map(gridWidth, signal), [revision, gridWidth])
  const account = useResource(
    (signal) => api.account(activeKey, signal),
    [revision, activeKey],
    activeKey !== '',
  )
  const activity = useResource((signal) => api.activity(ACTIVITY_LIMIT, signal), [revision])
  const blocks = useResource((signal) => api.blocks(BLOCK_LIMIT, signal), [revision])

  const refreshAll = useCallback(() => {
    setTick((value) => value + 1)
    refresh()
  }, [refresh])

  /* Pick a wallet automatically: the stored one if it still exists, else the
     first on the node's keyring. */
  useEffect(() => {
    const list = wallets.data
    if (!list || list.length === 0) return
    const stillThere = list.some((wallet) => wallet.public_key === activeKey)
    if (stillThere) return
    const first = list[0]
    if (first) setActiveKey(first.public_key)
  }, [wallets.data, activeKey, setActiveKey])

  /* Intents submitted but not yet seen in the activity feed: tx_id -> label. */
  const pending = useRef(new Map<string, string>())

  useEffect(() => {
    const rows = activity.data
    if (!rows || pending.current.size === 0) return
    for (const receipt of rows) {
      if (!receipt.tx_id) continue
      const label = pending.current.get(receipt.tx_id)
      if (label === undefined) continue
      pending.current.delete(receipt.tx_id)
      if (receipt.ok) {
        push('ok', `${label} confirmed`, `Executed in block #${receipt.height}.`)
      } else {
        push('error', `${label} rejected`, receipt.reason || 'no reason given — gas was still burned')
      }
    }
  }, [activity.data, push])

  const submitAction = useCallback(
    async (intent: ActionIntent, description: string) => {
      setSubmitting(true)
      try {
        const accepted = await api.submit(intent)
        if (accepted.tx_id) pending.current.set(accepted.tx_id, description)
        push(
          'info',
          `${description} queued`,
          `tx ${shortHash(accepted.tx_id, 8, 4)} · ${accepted.mempool} ${plural(
            accepted.mempool,
            'intent',
          )} in the mempool. Mine a block to apply it.`,
        )
        refreshAll()
      } catch (cause) {
        push('error', `${description} was not accepted`, errorMessage(cause))
      } finally {
        setSubmitting(false)
      }
    },
    [push, refreshAll],
  )

  const mineBlock = useCallback(async () => {
    setMining(true)
    try {
      const result = await api.mine()
      setLastMinedIndex(result.index)
      const ok = result.receipts.filter((receipt) => receipt.ok).length
      const bad = result.receipts.length - ok
      const events = result.events.length
      push(
        bad > 0 ? 'warn' : 'ok',
        `Block #${result.index} mined`,
        [
          `${result.tx_count} ${plural(result.tx_count, 'tx', 'txs')}`,
          `${ok} ok`,
          `${bad} rejected`,
          events > 0 ? `${events} system ${plural(events, 'event')}` : null,
        ]
          .filter(Boolean)
          .join(' · '),
      )
      refreshAll()
    } catch (cause) {
      push('error', 'Mining failed', errorMessage(cause))
    } finally {
      setMining(false)
    }
  }, [push, refreshAll])

  const createWallet = useCallback(
    async (label: string) => {
      setCreatingWallet(true)
      try {
        const wallet = await api.createWallet(label)
        setActiveKey(wallet.public_key)
        push(
          'ok',
          'Wallet created',
          `${displayName(wallet.public_key, wallet.label)} · key ends ${wallet.public_key.slice(-8)}`,
        )
        refreshAll()
      } catch (cause) {
        push('error', 'Could not create a wallet', errorMessage(cause))
      } finally {
        setCreatingWallet(false)
      }
    },
    [push, refreshAll, setActiveKey],
  )

  const walletList = wallets.data ?? []
  const plotsById = useMemo(() => {
    const index = new Map<number, Plot>()
    for (const plot of farmMap.data?.plots ?? []) index.set(plot.land_id, plot)
    return index
  }, [farmMap.data])

  const selectedPlot = selectedLandId === null ? null : plotsById.get(selectedLandId) ?? null
  const growingPlots = useMemo(
    () =>
      (farmMap.data?.plots ?? []).filter(
        (plot) => plot.owner === activeKey && plot.is_planted && !plot.is_ready,
      ),
    [farmMap.data, activeKey],
  )

  /**
   * One name per key, everywhere: the node's label when this node holds the
   * key, otherwise the key's tail (never its head — every IPv8 public key
   * starts with the same long ASN.1 curve header).
   */
  const labelFor = useCallback(
    (publicKey: string) => {
      if (publicKey === '') return ''
      const known = walletList.find((wallet) => wallet.public_key === publicKey)
      return displayName(publicKey, known?.label)
    },
    [walletList],
  )

  return (
    <>
      <div className="shell">
        <Header
          chain={chain}
          online={online}
          mining={mining}
          onMine={mineBlock}
          lastMinedIndex={lastMinedIndex}
        />

        {!online ? (
          <div className="banner" role="alert">
            <b>Node offline.</b>
            <span>
              {chainError ?? 'no response'} — retrying every second ({failures}{' '}
              {plural(failures, 'attempt')}).
            </span>
            <span className="grow" />
            <span className="mono tiny">start the node on :8000 · target: {API_ORIGIN}</span>
          </div>
        ) : null}

        <div className="supply-strip">
          <SupplyPanel chain={chain} />
        </div>

        <div className="columns">
          <div className="col col--left">
            <WalletPanel
              wallets={walletList}
              activeKey={activeKey}
              error={wallets.error}
              creating={creatingWallet}
              onSelect={setActiveKey}
              onCreate={createWallet}
            />
            <AccountPanel
              account={account.data}
              chain={chain}
              activeKey={activeKey}
              error={account.error}
              onSelectPlot={setSelectedLandId}
            />
            <ActionPanel
              chain={chain}
              account={account.data}
              wallets={walletList}
              activeKey={activeKey}
              growingPlots={growingPlots}
              busy={submitting}
              onSubmit={submitAction}
            />
          </div>

          <div className="col col--center">
            <FarmGrid
              map={farmMap.data}
              chain={chain}
              wallets={walletList}
              activeKey={activeKey}
              selectedLandId={selectedLandId}
              error={farmMap.error}
              onSelect={setSelectedLandId}
            />
            <PlotDetail
              landId={selectedLandId}
              plot={selectedPlot}
              chain={chain}
              account={account.data}
              activeKey={activeKey}
              ownerLabel={selectedPlot ? labelFor(selectedPlot.owner) : ''}
              busy={submitting}
              onSubmit={submitAction}
              onClear={() => setSelectedLandId(null)}
            />
            <Larder account={account.data} chain={chain} error={account.error} />
          </div>

          <div className="col col--right">
            <ActivityFeed
              receipts={activity.data ?? []}
              chain={chain}
              activeKey={activeKey}
              wallets={walletList}
              error={activity.error}
            />
            <RecentBlocks blocks={blocks.data ?? []} error={blocks.error} />
          </div>
        </div>
      </div>

      <Toasts toasts={toasts} onDismiss={dismiss} />
    </>
  )
}
