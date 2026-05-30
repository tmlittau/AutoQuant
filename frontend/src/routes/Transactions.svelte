<script lang="ts">
  /**
   * Ledger view: filterable table of every Transaction. Inline edit on note/fee,
   * delete with confirm, CSV export, and "Add Investment" launches the modal.
   * Reloads automatically whenever ``transactionsRevision`` bumps.
   */
  import { onMount } from 'svelte';
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import AddInvestmentModal from '../components/AddInvestmentModal.svelte';
  import ImportCsvModal from '../components/ImportCsvModal.svelte';
  import { fmtEUR, fmtDate, fmtLocal, fmtNum, fmtCoin } from '../lib/format';

  type Tx = {
    id: number;
    date: string;
    ticker: string;
    group: string;
    action: string;
    amount_eur: number;
    price_local: number;
    listing_currency: string;
    eur_per_local: number;
    shares: number;
    fee_eur: number;
    note: string;
    swap_group_id?: string | null;
  };

  // A ledger row is either a single transaction or a collapsed swap pair.
  type LedgerRow =
    | { kind: 'single'; tx: Tx }
    | { kind: 'swap'; id: string; sell: Tx; buy: Tx; date: string };

  type Holding = { ticker: string; name: string; currency: string; group: string };

  let rows = $state<Tx[]>([]);
  let holdings = $state<Holding[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);
  let importOpen = $state(false);

  // Inline edit state
  let editingId = $state<number | null>(null);
  let editNote = $state('');
  let editFee = $state(0);

  // Filters
  let filterTicker = $state('');
  let filterFrom = $state('');
  let filterTo = $state('');

  async function load() {
    loading = true;
    error = null;
    try {
      const query: Record<string, string> = {};
      if (filterTicker.trim()) query.ticker = filterTicker.trim();
      if (filterFrom) query.from = filterFrom;
      if (filterTo) query.to = filterTo;

      const [tx, stocks, etfs] = await Promise.all([
        apiGet('/api/transactions', { params: { query } }) as Promise<Tx[]>,
        apiGet('/api/portfolio', { params: { query: { asset_class: 'stocks' } } }),
        apiGet('/api/portfolio', { params: { query: { asset_class: 'etfs' } } }),
      ]);
      rows = tx;
      // Holdings list across both sleeves for the modal's dropdown.
      holdings = [
        ...((stocks as any).positions ?? []).map((p: any) => ({
          ticker: p.ticker,
          name: p.name,
          currency: p.currency,
          group: p.group,
        })),
        ...((etfs as any).positions ?? []).map((p: any) => ({
          ticker: p.ticker,
          name: p.name,
          currency: p.currency,
          group: p.group,
        })),
      ];
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Reload whenever a mutation bumps the revision store.
  $effect(() => {
    const rev = $transactionsRevision;
    if (rev > 0) load();
  });

  function startEdit(r: Tx) {
    editingId = r.id;
    editNote = r.note;
    editFee = r.fee_eur;
  }
  function cancelEdit() {
    editingId = null;
  }

  async function saveEdit(id: number) {
    try {
      const { error: e } = await api.PATCH('/api/transactions/{tx_id}', {
        params: { path: { tx_id: id } },
        body: { note: editNote, fee_eur: editFee },
      });
      if (e) throw new Error((e as any).detail ?? 'Update failed');
      editingId = null;
      transactionsRevision.update((n) => n + 1);
    } catch (err: any) {
      error = err?.message ?? String(err);
    }
  }

  async function deleteRow(r: Tx) {
    // Deleting either leg of a swap removes the whole swap (both legs), so the
    // ledger never ends up with a dangling half.
    const isSwap = !!r.swap_group_id;
    const legs = isSwap
      ? rows.filter((x) => x.swap_group_id === r.swap_group_id)
      : [r];
    const msg = isSwap
      ? `Delete this swap (both legs)?`
      : `Delete ${r.action} ${r.ticker} on ${fmtDate(r.date)} for ${fmtEUR(r.amount_eur)}?`;
    if (!confirm(msg)) return;
    try {
      for (const leg of legs) {
        const { error: e } = await api.DELETE('/api/transactions/{tx_id}', {
          params: { path: { tx_id: leg.id } },
        });
        if (e) throw new Error((e as any).detail ?? 'Delete failed');
      }
      transactionsRevision.update((n) => n + 1);
    } catch (err: any) {
      error = err?.message ?? String(err);
    }
  }

  function clearFilters() {
    filterTicker = '';
    filterFrom = '';
    filterTo = '';
    load();
  }

  // Track which swap rows are expanded to show their two underlying legs.
  let expandedSwaps = $state<Record<string, boolean>>({});
  function toggleSwap(id: string) {
    expandedSwaps[id] = !expandedSwaps[id];
  }

  // Collapse swap pairs (rows sharing a swap_group_id) into one LedgerRow.
  // Order is preserved by the first-seen position of each group.
  let ledgerRows = $derived.by<LedgerRow[]>(() => {
    const out: LedgerRow[] = [];
    const swapAccum: Record<string, { sell?: Tx; buy?: Tx; date: string }> = {};
    const swapIndex: Record<string, number> = {};
    for (const r of rows) {
      if (r.swap_group_id) {
        const id = r.swap_group_id;
        if (!(id in swapAccum)) {
          swapAccum[id] = { date: r.date };
          swapIndex[id] = out.length;
          out.push({ kind: 'swap', id, sell: r, buy: r, date: r.date } as LedgerRow);
        }
        if (r.action === 'sell') swapAccum[id].sell = r;
        else swapAccum[id].buy = r;
        const acc = swapAccum[id];
        out[swapIndex[id]] = {
          kind: 'swap',
          id,
          sell: acc.sell ?? r,
          buy: acc.buy ?? r,
          date: acc.date,
        };
      } else {
        out.push({ kind: 'single', tx: r });
      }
    }
    return out;
  });
</script>

<div class="space-y-4">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Transactions</h1>
    <span class="text-sm text-slate-500">{rows.length} rows</span>
    <div class="ml-auto flex flex-wrap gap-2">
      <a
        href="/api/transactions/export"
        target="_blank"
        rel="noopener"
        class="inline-flex items-center px-4 py-2 min-h-[44px] text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >
        Export CSV
      </a>
      <button
        type="button"
        onclick={() => (importOpen = true)}
        class="inline-flex items-center px-4 py-2 min-h-[44px] text-sm border border-slate-300 rounded-md hover:bg-slate-50"
        title="Restore from a CSV (same shape as the Export download)"
      >
        Import CSV
      </button>
      <button
        type="button"
        onclick={() => (modalOpen = true)}
        class="px-4 py-2 min-h-[44px] text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >
        + Add Investment
      </button>
    </div>
  </header>

  <!-- Filters -->
  <section class="bg-white border border-slate-200 rounded-xl p-3 flex flex-wrap items-end gap-3 text-sm">
    <label class="flex flex-col">
      <span class="text-xs font-medium text-slate-600 mb-0.5">Ticker</span>
      <input
        type="text"
        bind:value={filterTicker}
        placeholder="AAPL"
        class="px-2 py-1 border border-slate-300 rounded w-32"
      />
    </label>
    <label class="flex flex-col">
      <span class="text-xs font-medium text-slate-600 mb-0.5">From</span>
      <input
        type="date"
        bind:value={filterFrom}
        class="px-2 py-1 border border-slate-300 rounded"
      />
    </label>
    <label class="flex flex-col">
      <span class="text-xs font-medium text-slate-600 mb-0.5">To</span>
      <input
        type="date"
        bind:value={filterTo}
        class="px-2 py-1 border border-slate-300 rounded"
      />
    </label>
    <button
      type="button"
      onclick={load}
      class="ml-auto px-4 py-2 min-h-[40px] bg-slate-900 text-white rounded hover:bg-slate-700"
    >Apply</button>
    <button
      type="button"
      onclick={clearFilters}
      class="px-4 py-2 min-h-[40px] text-slate-600 hover:text-slate-900"
    >Clear</button>
  </section>

  {#if loading && rows.length === 0}
    <p class="text-sm text-slate-500">Loading…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      {error}
    </div>
  {:else}
    <!-- Mobile: card-stack list of transactions. -->
    <ul class="md:hidden space-y-2">
      {#each ledgerRows as row (row.kind === 'swap' ? `swap-${row.id}` : `tx-${row.tx.id}`)}
        {#if row.kind === 'swap'}
          {@const sell = row.sell}
          {@const buy = row.buy}
          <li class="border border-indigo-200 rounded-lg p-3 bg-indigo-50/40">
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="px-2 py-0.5 rounded text-xs font-medium bg-indigo-100 text-indigo-700">
                    swap
                  </span>
                  <span class="font-mono font-semibold text-sm">
                    {fmtCoin(Math.abs(sell.shares), sell.ticker)} {sell.ticker}
                    → {fmtCoin(Math.abs(buy.shares), buy.ticker)} {buy.ticker}
                  </span>
                </div>
                <div class="text-xs text-slate-500 mt-0.5">{fmtDate(row.date)}</div>
              </div>
              <div class="text-right font-mono shrink-0 text-base font-semibold">
                {fmtEUR(Math.abs(buy.amount_eur))}
              </div>
            </div>
            <div class="mt-2 flex gap-2">
              <button
                type="button"
                onclick={() => deleteRow(sell)}
                class="flex-1 px-3 py-2 min-h-[44px] text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50"
              >Delete swap</button>
            </div>
          </li>
        {:else}
          {@const r = row.tx}
          <li class="border border-slate-200 rounded-lg p-3 bg-white">
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span
                    class="px-2 py-0.5 rounded text-xs font-medium {r.action === 'buy'
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-red-100 text-red-700'}"
                  >
                    {r.action}
                  </span>
                  <span class="font-mono font-semibold">{r.ticker}</span>
                </div>
                <div class="text-xs text-slate-500 mt-0.5">
                  {fmtDate(r.date)} · {r.group}
                </div>
              </div>
              <div class="text-right font-mono shrink-0">
                <div class="text-base font-semibold">{fmtEUR(r.amount_eur)}</div>
              </div>
            </div>
            <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt class="text-slate-500">Shares</dt>
              <dd class="text-right font-mono">{fmtCoin(r.shares, r.ticker)}</dd>
              <dt class="text-slate-500">Price (local)</dt>
              <dd class="text-right font-mono text-slate-600">
                {fmtLocal(r.price_local, r.listing_currency)}
              </dd>
              <dt class="text-slate-500">EUR / {r.listing_currency}</dt>
              <dd class="text-right font-mono text-slate-500">{r.eur_per_local.toFixed(4)}</dd>
              <dt class="text-slate-500">Fee</dt>
              <dd class="text-right font-mono">
                {#if editingId === r.id}
                  <input
                    type="number"
                    step="0.01"
                    bind:value={editFee}
                    class="w-24 px-2 py-1 border border-slate-300 rounded text-right text-sm"
                  />
                {:else}
                  {fmtEUR(r.fee_eur)}
                {/if}
              </dd>
            </dl>
            {#if editingId === r.id}
              <label class="block mt-3 text-xs">
                <span class="block text-slate-500 mb-1">Note</span>
                <input
                  type="text"
                  bind:value={editNote}
                  class="w-full px-3 py-2 border border-slate-300 rounded text-base"
                />
              </label>
            {:else if r.note}
              <div class="mt-3 text-xs text-slate-600 break-words">
                <span class="text-slate-500">Note:</span> {r.note}
              </div>
            {/if}
            <div class="mt-3 flex gap-2">
              {#if editingId === r.id}
                <button
                  type="button"
                  onclick={() => saveEdit(r.id)}
                  class="flex-1 px-3 py-2 min-h-[44px] text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
                >Save</button>
                <button
                  type="button"
                  onclick={cancelEdit}
                  class="flex-1 px-3 py-2 min-h-[44px] text-sm border border-slate-300 text-slate-600 rounded-md hover:bg-slate-50"
                >Cancel</button>
              {:else}
                <button
                  type="button"
                  onclick={() => startEdit(r)}
                  class="flex-1 px-3 py-2 min-h-[44px] text-sm border border-slate-300 text-blue-600 rounded-md hover:bg-blue-50"
                >Edit</button>
                <button
                  type="button"
                  onclick={() => deleteRow(r)}
                  class="flex-1 px-3 py-2 min-h-[44px] text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50"
                >Delete</button>
              {/if}
            </div>
          </li>
        {/if}
      {/each}
      {#if rows.length === 0}
        <li class="py-8 text-center text-sm text-slate-500">No transactions matched.</li>
      {/if}
    </ul>

    <!-- Desktop: full table. -->
    <section
      class="hidden md:block bg-white border border-slate-200 rounded-xl shadow-sm overflow-x-auto"
    >
      <table class="w-full text-sm">
        <thead>
          <tr
            class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200 bg-slate-50"
          >
            <th class="py-2 px-3">Date</th>
            <th class="py-2 px-3">Ticker</th>
            <th class="py-2 px-3">Group</th>
            <th class="py-2 px-3">Action</th>
            <th class="py-2 px-3 text-right">Amount</th>
            <th class="py-2 px-3 text-right">Price</th>
            <th class="py-2 px-3 text-right">EUR / local</th>
            <th class="py-2 px-3 text-right">Shares</th>
            <th class="py-2 px-3 text-right">Fee</th>
            <th class="py-2 px-3">Note</th>
            <th class="py-2 px-3 w-32"></th>
          </tr>
        </thead>
        <tbody>
          {#each ledgerRows as row (row.kind === 'swap' ? `swap-${row.id}` : `tx-${row.tx.id}`)}
            {#if row.kind === 'swap'}
              {@const sell = row.sell}
              {@const buy = row.buy}
              <!-- Collapsed swap summary row -->
              <tr class="border-b border-slate-100 bg-indigo-50/40 hover:bg-indigo-50">
                <td class="py-2 px-3 font-mono text-xs whitespace-nowrap">{fmtDate(row.date)}</td>
                <td class="py-2 px-3 font-mono font-medium" colspan="2">
                  <button
                    type="button"
                    onclick={() => toggleSwap(row.id)}
                    class="inline-flex items-center gap-1 hover:underline"
                    title="Show / hide both legs"
                  >
                    <span class="text-slate-400">{expandedSwaps[row.id] ? '▾' : '▸'}</span>
                    {fmtCoin(Math.abs(sell.shares), sell.ticker)} {sell.ticker}
                    → {fmtCoin(Math.abs(buy.shares), buy.ticker)} {buy.ticker}
                  </button>
                </td>
                <td class="py-2 px-3">
                  <span class="px-2 py-0.5 rounded text-xs font-medium bg-indigo-100 text-indigo-700">
                    swap
                  </span>
                </td>
                <td class="py-2 px-3 text-right font-mono">{fmtEUR(Math.abs(buy.amount_eur))}</td>
                <td class="py-2 px-3" colspan="3"></td>
                <td class="py-2 px-3 text-right font-mono">{fmtEUR(sell.fee_eur)}</td>
                <td class="py-2 px-3 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onclick={() => deleteRow(sell)}
                    class="text-red-600 hover:underline text-xs px-1"
                  >Delete</button>
                </td>
              </tr>
              {#if expandedSwaps[row.id]}
                {#each [sell, buy] as leg (leg.id)}
                  <tr class="border-b border-slate-100 bg-slate-50/60 text-slate-500">
                    <td class="py-1.5 px-3"></td>
                    <td class="py-1.5 px-3 pl-6 font-mono">{leg.ticker}</td>
                    <td class="py-1.5 px-3">{leg.group}</td>
                    <td class="py-1.5 px-3">
                      <span class="px-2 py-0.5 rounded text-xs {leg.action === 'buy'
                        ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-red-100 text-red-700'}">{leg.action}</span>
                    </td>
                    <td class="py-1.5 px-3 text-right font-mono">{fmtEUR(leg.amount_eur)}</td>
                    <td class="py-1.5 px-3 text-right font-mono">
                      {fmtLocal(leg.price_local, leg.listing_currency)}
                    </td>
                    <td class="py-1.5 px-3 text-right font-mono text-xs">
                      {leg.eur_per_local.toFixed(4)}
                    </td>
                    <td class="py-1.5 px-3 text-right font-mono">{fmtCoin(leg.shares, leg.ticker)}</td>
                    <td class="py-1.5 px-3 text-right font-mono">{fmtEUR(leg.fee_eur)}</td>
                    <td class="py-1.5 px-3 text-xs truncate max-w-[14rem]" title={leg.note}>
                      {leg.note || '–'}
                    </td>
                    <td class="py-1.5 px-3"></td>
                  </tr>
                {/each}
              {/if}
            {:else}
              {@const r = row.tx}
              <tr class="border-b border-slate-100 hover:bg-slate-50">
                <td class="py-2 px-3 font-mono text-xs whitespace-nowrap">{fmtDate(r.date)}</td>
                <td class="py-2 px-3 font-mono font-medium">{r.ticker}</td>
                <td class="py-2 px-3 text-slate-500">{r.group}</td>
                <td class="py-2 px-3">
                  <span
                    class="px-2 py-0.5 rounded text-xs font-medium {r.action === 'buy'
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-red-100 text-red-700'}"
                  >
                    {r.action}
                  </span>
                </td>
                <td class="py-2 px-3 text-right font-mono">{fmtEUR(r.amount_eur)}</td>
                <td class="py-2 px-3 text-right font-mono text-slate-600">
                  {fmtLocal(r.price_local, r.listing_currency)}
                </td>
                <td class="py-2 px-3 text-right font-mono text-xs text-slate-500">
                  {r.eur_per_local.toFixed(4)}
                </td>
                <td class="py-2 px-3 text-right font-mono">{fmtCoin(r.shares, r.ticker)}</td>
                <td class="py-2 px-3 text-right font-mono">
                  {#if editingId === r.id}
                    <input
                      type="number"
                      step="0.01"
                      bind:value={editFee}
                      class="w-20 px-1 py-0.5 border border-slate-300 rounded text-right"
                    />
                  {:else}
                    {fmtEUR(r.fee_eur)}
                  {/if}
                </td>
                <td
                  class="py-2 px-3 max-w-[14rem] truncate text-xs text-slate-600"
                  title={r.note}
                >
                  {#if editingId === r.id}
                    <input
                      type="text"
                      bind:value={editNote}
                      class="w-full px-1 py-0.5 border border-slate-300 rounded"
                    />
                  {:else}
                    {r.note || '–'}
                  {/if}
                </td>
                <td class="py-2 px-3 text-right whitespace-nowrap">
                  {#if editingId === r.id}
                    <button
                      type="button"
                      onclick={() => saveEdit(r.id)}
                      class="text-blue-600 hover:underline text-xs px-1"
                    >Save</button>
                    <button
                      type="button"
                      onclick={cancelEdit}
                      class="text-slate-500 hover:underline text-xs px-1"
                    >Cancel</button>
                  {:else}
                    <button
                      type="button"
                      onclick={() => startEdit(r)}
                      class="text-blue-600 hover:underline text-xs px-1"
                    >Edit</button>
                    <button
                      type="button"
                      onclick={() => deleteRow(r)}
                      class="text-red-600 hover:underline text-xs px-1"
                    >Delete</button>
                  {/if}
                </td>
              </tr>
            {/if}
          {/each}
          {#if rows.length === 0}
            <tr>
              <td colspan="11" class="py-8 text-center text-sm text-slate-500">
                No transactions matched.
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </section>
  {/if}
</div>

<AddInvestmentModal
  open={modalOpen}
  onClose={() => (modalOpen = false)}
  {holdings}
/>

<ImportCsvModal
  open={importOpen}
  onClose={() => (importOpen = false)}
/>
