# Boniato Chain — web client

The **application layer** of the stack (infrastructure → consensus → execution →
access → **application**): a React client for the crop-to-earn DApp running on
our ledger. It reads one node's view of the world over HTTP and submits player
intents. Everything it knows comes from the contract in
[`../docs/api.md`](../docs/api.md) — no other source of truth, no invented
endpoints.

React + Vite + TypeScript, and nothing else. No component library, no state
manager, no charting library; the farm, the crops and the boniatos are drawn
with plain CSS.

---

## Running it

The node must be up first, because the client is useless without a chain (it
will show a *node offline* banner and keep retrying until one appears).

```bash
# terminal 1 — the node, from the repo root
python scripts/run_api.py            # serves http://127.0.0.1:8000

# terminal 2 — the client
cd web
npm install
npm run dev                          # http://127.0.0.1:5173
```

`vite.config.ts` proxies `/api/*` to `http://127.0.0.1:8000`, so the browser only
ever talks to the Vite origin and CORS never comes up in development.

| script            | what it does                            |
| ----------------- | --------------------------------------- |
| `npm run dev`     | dev server with HMR on :5173            |
| `npm run build`   | `tsc --noEmit` then a production build  |
| `npm run preview` | serves `dist/`                          |
| `npm run typecheck` | types only                            |

**Overrides**

- `VITE_API_BASE` — point the client at a node directly instead of using the
  proxy (e.g. `VITE_API_BASE=http://127.0.0.1:8000` for a built `dist/` served
  from somewhere else). Empty by default. See `.env.example`.
- `API_PROXY_TARGET` — change where the *dev proxy* forwards, for a node on
  another host or port.

---

## What each panel shows

**Top bar** — height, head hash, state root, difficulty and mempool size, plus
the **Mine block** button (`POST /api/mine`). Mining is the only thing that
moves this world: crops mature and boniatos age in *block height*, not in
seconds, so nothing at all happens until a block is mined. The pill on the right
is the node's reachability.

**$BONI supply** — circulating / minted / burned / rotted / fertilizer minted,
followed by the economic constants the node is actually running (gas, seed cost,
current land price, growth time, rot window, adjacency bonus, blight schedule).

**Wallets** — the node's demo keyring (`GET /api/wallets`), plus a box to create
another (`POST /api/wallets`). The node holds every private key; picking a
wallet just chooses which public key the client acts as. The choice is
remembered in `localStorage`.

Keys are shortened from the **tail**, never the head: a serialised IPv8 public
key starts with a 48-character ASN.1 curve header
(`307e301006072a8648ce3d020106052b81040024036a0004…`) that is identical for every
farmer, so a head truncation renders every wallet the same. Where the API
supplies a `label` — wallets, leaderboard entries, receipts — the label wins. One
key always renders as the same name everywhere in the UI, and the full key is in
the `title` of anything that shows a short form.

**Active farmer** — balance, fertilizer, whether the starter kit has been
claimed, and the plot ids owned. Click an id to select that plot on the map.

**The farm** — `grid_width` columns of land, ids filling row by row, so plot `n`
sits at `(n % grid_width, n // grid_width)`. Each minted plot is drawn side-on:
soil below, air above, and a plant whose size tracks `progress_bp`. Ready crops
glow gold and push a tuber out of the ground; blighted crops are hatched red,
wilted, and labelled with the share of the crop lost. Fertility sits in one
corner and the adjacency bonus in the other, as a real percentage
(`adjacent_owned × adjacency_bonus_bp`, since the bonus is per neighbour). The
plots you own get a gold ring. Tiles the map does not mention are unclaimed land,
and the parcel `buy_land` will mint next — `economy.next_land_id` — is marked
**next parcel**. Clicking a plot selects it.

**Plot #n** — the selected plot in detail, with the actions its state allows:
*plant* on fallow land, *harvest* on a ready crop, *fertilize* on a growing one.
Land owned by someone else is read-only. A growing crop shows its **fertilizer
headroom**: the blocks `fertilize` could still remove and what that costs, both
quoted by the node.

**The larder** — the mechanic worth staring at. A balance here is not a number,
it is a queue of **lots**, each with its own expiry height, ordered soonest to
rot first. Every lot shows what it holds, how many blocks (and roughly how many
days) it has left, and a freshness bar that empties as it ages: green, then
amber, then red and wobbling when it is nearly compost. Spending always drains
the top lot first, so this panel tells you what to spend before the chain
destroys it.

**Actions** — claim, transfer, buy land (naming the parcel and price the chain is
currently offering), fertilize. The amount fields take whole `$BONI` and are
converted to integer base units; the fertilize form previews how many blocks the
crop would jump forward, and its **max** button fills in the node's quoted
headroom cost — the amount the node's own test says buys the quoted blocks with
nothing refunded.

**Activity** — `GET /api/activity`, newest first. Every receipt names its signer
with a coloured chip, so the feed can be filtered to the active wallet with the
toggle in the panel header. Successful transactions are green-edged, rejections
are red, struck through and carry the chain's reason — which is the point,
because a rejected action still burns gas. `detail` is rendered per action from
the table in the contract: amounts as `$BONI`, rates as percentages, fertility as
a multiplier, heights as `#n`. System events (`blight`, `rot`) appear in the same
feed in their own colours, marked `chain`, though `rot` names the account it
happened to.

**Recent blocks** — `GET /api/blocks`: index, hash, tx count, nonce, merkle and
state roots, timestamp.

Toasts in the corner report each intent being queued (with its `tx_id` and the
new mempool size), the outcome once it appears in the activity feed, and what a
mined block contained.

---

## How it talks to the node

- **One poller.** `useChain` polls `GET /api/chain` every second; that is the
  app's clock. When `height` changes, the map, the active account, the activity
  feed, the wallet list and the block list all refetch — per the contract,
  nothing else changes between blocks. There are no websockets.
- **A 202 is not a success.** `POST /api/actions` only puts a signed transaction
  in the mempool; preconditions are checked when a block is mined. The client
  keeps the returned `tx_id` and reports the real outcome when it shows up in
  `GET /api/activity`.
- **Integers only.** Amounts are integers in base units everywhere, with
  `base_units` read from `GET /api/chain` (never hardcoded). They become decimal
  strings only in `lib/format.ts`, using integer division; `parseAmount` goes the
  other way and refuses input with more decimals than the chain's precision.
  Rates are basis points, with `bp` also read from the chain.
- **Consensus maths stays on the node.** Anything the node quotes is used as
  quoted rather than recomputed: `freshness_bp` for the larder meters,
  `next_land_id` for the parcel on sale, and
  `fertilizer_headroom_blocks`/`fertilizer_headroom_cost` for the fertilize cap
  (whose floor is measured against *nominal* growth — a client reimplementing it
  would eventually disagree with consensus). The only local arithmetic left in
  the fertilize preview is the per-unit conversion the contract states outright,
  clamped to the node's quote.
- **Nothing throws.** `api/normalize.ts` coerces every response field with a
  default, so an older or newer node cannot crash a render. Failed requests keep
  the previous value on screen, show a strip in the affected panel, and retry.
  While the node is down the top banner says so and the poller backs off to a
  5-second retry. An error boundary catches anything else.

## Layout

```
src/
  api/       client.ts (fetch + errors)  types.ts (the contract)  normalize.ts (coercion)
  hooks/     useChain (the poller)  useResource (fetch-per-block + retry)
             useToasts  useStoredValue
  lib/       format.ts (base units, bp, keys)  farm.ts (grid, larder, fertilizer quote)
             receipt.ts (detail -> chips)  style.ts
  components/ Header SupplyPanel WalletPanel AccountPanel FarmGrid PlotTile
              PlotDetail Larder ActionPanel ActivityFeed RecentBlocks Toasts
              Panel ErrorBoundary
  styles/    tokens.css base.css layout.css components.css farm.css
```

---

## Assumptions and open questions

1. **How much land to draw.** The map is unbounded — ids are handed out
   sequentially forever and the grid grows downwards — so this is explicitly a
   client-side choice: `grid_width` columns, with one complete spare row past the
   last minted parcel (four rows minimum), plus whatever row holds
   `next_land_id`. The board grows as land is sold.
2. **Harvest yield is deliberately not predicted.** It depends on a hash of the
   next block's parent, so `base_yield_min`/`base_yield_max` are shown as a range
   next to Harvest and nothing more — as agreed with the node.
3. **`detail` fallback.** The documented shapes are rendered explicitly, but any
   field the contract gains later still appears, through a convention-based
   fallback (keys ending in `_bp` as percentages, amount-like keys as `$BONI`,
   long hex shortened). A new field looks slightly generic instead of vanishing.
4. **`freshness_bp` has a local fallback.** The node's value is what the larder
   meters use. If a build ever omits the field, the client falls back to
   `blocks_left / rot_blocks`; the sentinel for "not reported" is `-1`, because
   `0` is a real freshness (the block a lot rots).

### Closed by the node

Recorded because the client used to work around them. All are now used as given
rather than guessed at:

- receipts carry `public_key`/`label`, so the feed can be filtered by wallet;
- `rots_in_days_bp` became `freshness_bp`, which is what it always measured;
- `economy.next_land_id` says which parcel is on sale;
- each plot quotes its own `fertilizer_headroom_blocks`/`_cost`, and quotes them
  **only when the engine would accept a `fertilize`** — so a matured crop reports
  0 rather than offering time it cannot sell;
- `detail` shapes are documented per action;
- the adjacency bonus is stated to be per neighbour;
- `transfer.detail.to` carries the recipient's **full** key. It used to be
  head-truncated, which named nobody, since every IPv8 key shares a long ASN.1
  curve header. Same for the `label` a receipt falls back to for an unlabelled
  account: it is now the key's tail. The contract now states outright that public
  keys are never truncated in a payload, and that shortening for display is the
  client's job — from the tail.

## Deliberately left out

- `GET /api/leaderboard` is typed and wired in the client module but not shown;
  with a handful of demo wallets the wallet list and the map already say who is
  winning. The panel would be a few lines if we want it for the demo.
- `GET /api/health` likewise: `/api/chain` is polled every second and is a
  strictly better health check.
- No wallet deletion, no renaming, no signing in the browser — the node is
  custodial by design (see the demo affordances section of `docs/api.md`).
- No pagination anywhere: the feed asks for the newest 40 receipts and the block
  list for 12, which is all a live demo needs.
