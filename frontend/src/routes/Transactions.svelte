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
  import { fmtEUR, fmtDate, fmtLocal, fmtNum } from '../lib/format';

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
  };

  type Holding = { ticker: string; name: string; currency: string; group: string };

  let rows = $state<Tx[]>([]);
  let holdings = $state<Holding[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);

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
    if (!confirm(`Delete ${r.action} ${r.ticker} on ${fmtDate(r.date)} for ${fmtEUR(r.amount_eur)}?`)) return;
    try {
      const { error: e } = await api.DELETE('/api/transactions/{tx_id}', {
        params: { path: { tx_id: r.id } },
      });
      if (e) throw new Error((e as any).detail ?? 'Delete failed');
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
</script>

<div class="space-y-4">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Transactions</h1>
    <span class="text-sm text-slate-500">{rows.length} rows</span>
    <div class="ml-auto flex gap-2">
      <a
        href="/api/transactions/export"
        target="_blank"
        rel="noopener"
        class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >
        Export CSV
      </a>
      <button
        type="button"
        onclick={() => (modalOpen = true)}
        class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
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
      class="ml-auto px-3 py-1 bg-slate-900 text-white rounded hover:bg-slate-700"
    >Apply</button>
    <button
      type="button"
      onclick={clearFilters}
      class="px-3 py-1 text-slate-600 hover:text-slate-900"
    >Clear</button>
  </section>

  {#if loading && rows.length === 0}
    <p class="text-sm text-slate-500">Loading…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      {error}
    </div>
  {:else}
    <section
      class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-x-auto"
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
          {#each rows as r (r.id)}
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
              <td class="py-2 px-3 text-right font-mono">{fmtNum(r.shares, 4)}</td>
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
