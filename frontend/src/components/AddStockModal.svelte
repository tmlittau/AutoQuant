<script lang="ts">
  /**
   * Add a new holding to either the portfolio (with optional initial buy) or the
   * watchlist. Backed by ``POST /api/holdings`` which atomically:
   *   1. get-or-creates the GroupConfig (with optional target_weight / desc)
   *   2. creates the Holding row
   *   3. if portfolio + initial_amount_eur > 0, runs estimate_shares + creates
   *      the first Transaction (rolls back the holding if the price fetch fails)
   *
   * Ticker / name / currency are filled by clicking a hit from the symbol search
   * dropdown -- which hits the live adapter (yfinance Search / AV SYMBOL_SEARCH)
   * so the user can explore the whole public stock universe.
   */
  import { untrack } from 'svelte';
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import Modal from './Modal.svelte';

  type Kind = 'portfolio' | 'watchlist';
  type AssetClass = 'stocks' | 'etfs';

  type SymbolHit = {
    symbol: string;
    name?: string | null;
    type?: string | null;
    region?: string | null;
    currency?: string | null;
  };

  type Props = {
    open: boolean;
    onClose: () => void;
    /** Pre-fill the form (e.g. when opened from Single-stock view). */
    initial?: {
      kind?: Kind;
      assetClass?: AssetClass;
      ticker?: string;
      name?: string;
      currency?: string;
    };
  };

  let { open, onClose, initial }: Props = $props();

  // -------------------------------------------------------------------------
  // Form state.
  // -------------------------------------------------------------------------
  let kind = $state<Kind>('portfolio');
  let assetClass = $state<AssetClass>('stocks');

  // Symbol search.
  let symbolQuery = $state('');
  let symbolHits = $state<SymbolHit[]>([]);
  let symbolLoading = $state(false);
  let showHits = $state(false);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  let ticker = $state('');
  let name = $state('');
  let currency = $state('USD');

  // Group selection.
  let availableGroups = $state<string[]>([]);
  let group = $state('');
  let creatingNewGroup = $state(false);
  let newGroupName = $state('');
  let newGroupTargetPct = $state<number | null>(null); // shown as %, sent as fraction
  let newGroupDescription = $state('');

  // Initial buy (portfolio only).
  let withInitialBuy = $state(true);
  let initialDate = $state(todayIso());
  let initialAmount = $state(50);
  let initialFee = $state(0);
  let initialNote = $state('');
  let preview = $state<{
    shares: number;
    price_local: number;
    listing_currency: string;
    eur_per_local: number;
    price_eur: number;
  } | null>(null);
  let previewing = $state(false);
  let previewError = $state<string | null>(null);
  let previewTimer: ReturnType<typeof setTimeout> | null = null;

  // Submit.
  let submitting = $state(false);
  let error = $state<string | null>(null);

  function todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  // -------------------------------------------------------------------------
  // Reset form on open (use initial to pre-fill if provided).
  // -------------------------------------------------------------------------
  $effect(() => {
    if (!open) return;
    kind = initial?.kind ?? 'portfolio';
    assetClass = initial?.assetClass ?? 'stocks';
    ticker = initial?.ticker ?? '';
    name = initial?.name ?? '';
    currency = (initial?.currency ?? 'USD').toUpperCase();
    symbolQuery = ticker;
    symbolHits = [];
    showHits = false;
    creatingNewGroup = false;
    newGroupName = '';
    newGroupTargetPct = null;
    newGroupDescription = '';
    withInitialBuy = true;
    initialDate = todayIso();
    initialAmount = 50;
    initialFee = 0;
    initialNote = '';
    preview = null;
    previewError = null;
    submitting = false;
    error = null;
  });

  // -------------------------------------------------------------------------
  // Load existing groups for the dropdown whenever the modal opens or asset
  // class changes. Union of groups used in portfolio + watchlist for the
  // matching asset_class; ETFs always have a single 'ETFs' group.
  // -------------------------------------------------------------------------
  $effect(() => {
    if (!open) return;
    const ac = assetClass;
    if (ac === 'etfs') {
      availableGroups = ['ETFs'];
      group = 'ETFs';
      return;
    }
    (async () => {
      try {
        const [pf, wl] = await Promise.all([
          apiGet('/api/portfolio', { params: { query: { asset_class: 'stocks' } } }),
          apiGet('/api/watchlist/signals'),
        ]);
        const set = new Set<string>();
        for (const g of ((pf as any).by_group ?? [])) set.add(g.group);
        for (const i of ((wl as any).items ?? [])) {
          if (i.group) set.add(i.group);
        }
        availableGroups = [...set].sort();
        if (!group && availableGroups.length > 0) group = availableGroups[0];
      } catch {
        availableGroups = [];
      }
    })();
  });

  // -------------------------------------------------------------------------
  // Symbol search (debounced 300 ms).
  // -------------------------------------------------------------------------
  function onSymbolInput(e: Event) {
    const v = (e.target as HTMLInputElement).value;
    symbolQuery = v;
    showHits = true;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    const q = symbolQuery.trim();
    if (q.length < 2) {
      symbolHits = [];
      return;
    }
    symbolLoading = true;
    try {
      // Filter hits by the current asset-class tab so the ETFs sleeve gets
      // only ETFs and the stocks sleeve only equities/funds.
      const hits = (await apiGet('/api/instruments/search', {
        params: { query: { q, type: assetClass } },
      })) as SymbolHit[];
      symbolHits = hits ?? [];
    } catch {
      symbolHits = [];
    } finally {
      symbolLoading = false;
    }
  }

  // Re-run the search when the user toggles asset class so the dropdown
  // refreshes its hits without forcing them to retype. We only want
  // assetClass changes to trigger this; symbolQuery is already covered by
  // the debounce in onSymbolInput, so we untrack the read here to avoid
  // re-firing on every keystroke.
  $effect(() => {
    const _ac = assetClass; // tracked dep
    if (!open) return;
    untrack(() => {
      if (symbolQuery.trim().length >= 2) runSearch();
    });
  });

  function pickHit(hit: SymbolHit) {
    ticker = hit.symbol;
    name = hit.name ?? hit.symbol;
    currency = (hit.currency || 'USD').toUpperCase();
    symbolQuery = hit.symbol;
    symbolHits = [];
    showHits = false;
  }

  // -------------------------------------------------------------------------
  // Live shares preview for the initial buy (portfolio only).
  // -------------------------------------------------------------------------
  $effect(() => {
    if (!open || kind !== 'portfolio' || !withInitialBuy) {
      preview = null;
      previewError = null;
      return;
    }
    const t = ticker.trim().toUpperCase(),
      d = initialDate,
      a = initialAmount,
      c = currency;
    if (!t || !d || a <= 0) {
      preview = null;
      return;
    }
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      previewing = true;
      previewError = null;
      try {
        const r = await apiGet('/api/instruments/{ticker}/estimate-shares', {
          params: {
            path: { ticker: t },
            query: { on: d, amount_eur: a, listing_currency: c },
          },
        });
        preview = r as any;
      } catch (e: any) {
        preview = null;
        previewError = e?.message ?? 'preview failed';
      } finally {
        previewing = false;
      }
    }, 400);
  });

  // -------------------------------------------------------------------------
  // Submit.
  // -------------------------------------------------------------------------
  async function submit() {
    error = null;
    if (!ticker.trim()) {
      error = 'Pick a ticker via the search';
      return;
    }
    if (!name.trim()) {
      error = 'Name is required';
      return;
    }
    const finalGroup =
      assetClass === 'etfs' ? 'ETFs' : creatingNewGroup ? newGroupName.trim() : group;
    if (!finalGroup) {
      error = 'Pick or enter a group';
      return;
    }

    submitting = true;
    try {
      const body: Record<string, any> = {
        kind,
        asset_class: assetClass,
        group: finalGroup,
        ticker: ticker.trim().toUpperCase(),
        name: name.trim(),
        currency: currency.trim().toUpperCase(),
      };
      if (
        assetClass === 'stocks' &&
        creatingNewGroup &&
        newGroupTargetPct != null &&
        Number.isFinite(newGroupTargetPct)
      ) {
        body.target_weight = newGroupTargetPct / 100;
      }
      if (creatingNewGroup && newGroupDescription.trim()) {
        body.group_description = newGroupDescription.trim();
      }
      if (
        kind === 'portfolio' &&
        withInitialBuy &&
        initialAmount > 0
      ) {
        body.initial_amount_eur = initialAmount;
        body.initial_date = initialDate;
        body.initial_fee_eur = initialFee || 0;
        body.initial_note = initialNote || '';
      }
      const { data, error: e } = await api.POST('/api/holdings', { body: body as any });
      if (e) throw new Error((e as any).detail ?? 'Create failed');
      transactionsRevision.update((n) => n + 1);
      onClose();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      submitting = false;
    }
  }

  let canSubmit = $derived(
    !submitting &&
      !!ticker.trim() &&
      !!name.trim() &&
      (assetClass === 'etfs' ||
        (creatingNewGroup ? !!newGroupName.trim() : !!group)),
  );
</script>

<Modal {open} {onClose} title="Add Stock" sizeClass="max-w-2xl">
  <div class="space-y-5 text-sm">
    <!-- Kind + asset class toggles -->
    <div class="grid grid-cols-2 gap-3">
      <div>
        <span class="block text-xs font-medium text-slate-700 mb-1">Add to</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each ['portfolio', 'watchlist'] as k}
            <button
              type="button"
              onclick={() => (kind = k as Kind)}
              class="px-3 py-1 rounded text-sm capitalize transition {kind === k
                ? 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >{k}</button>
          {/each}
        </div>
      </div>
      <div>
        <span class="block text-xs font-medium text-slate-700 mb-1">Asset class</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each ['stocks', 'etfs'] as a}
            <button
              type="button"
              onclick={() => (assetClass = a as AssetClass)}
              class="px-3 py-1 rounded text-sm uppercase transition {assetClass === a
                ? 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >{a}</button>
          {/each}
        </div>
      </div>
    </div>

    <!-- Symbol search -->
    <div class="relative">
      <span class="block text-xs font-medium text-slate-700 mb-1">
        Ticker (search a public symbol)
      </span>
      <input
        type="text"
        value={symbolQuery}
        oninput={onSymbolInput}
        onfocus={() => (showHits = true)}
        onblur={() => setTimeout(() => (showHits = false), 150)}
        placeholder="e.g. ASML, Berkshire, NVO …"
        class="w-full px-3 py-1.5 border border-slate-300 rounded-md font-mono"
      />
      {#if showHits && (symbolLoading || symbolHits.length > 0)}
        <div
          class="absolute mt-1 w-full bg-white border border-slate-200 rounded-md shadow-lg max-h-60 overflow-y-auto z-10"
        >
          {#if symbolLoading}
            <div class="px-3 py-2 text-xs text-slate-500">searching…</div>
          {/if}
          {#each symbolHits as h (h.symbol)}
            <button
              type="button"
              onmousedown={() => pickHit(h)}
              class="w-full text-left px-3 py-2 hover:bg-slate-50 border-b border-slate-100 last:border-b-0"
            >
              <div class="text-sm font-medium font-mono">{h.symbol}</div>
              <div class="text-xs text-slate-500">
                {h.name ?? ''}
                {#if h.region}· {h.region}{/if}
                {#if h.currency}· {h.currency}{/if}
              </div>
            </button>
          {/each}
        </div>
      {/if}
      {#if ticker}
        <p class="mt-1 text-xs text-slate-500">
          → <span class="font-mono font-medium">{ticker}</span> · {name} · {currency}
        </p>
      {/if}
    </div>

    <!-- Group (stocks only; ETFs are pinned to "ETFs") -->
    {#if assetClass === 'stocks'}
      <div>
        <span class="block text-xs font-medium text-slate-700 mb-1">Group</span>
        {#if !creatingNewGroup}
          <div class="flex gap-2 items-center">
            <select
              bind:value={group}
              class="flex-1 px-3 py-1.5 border border-slate-300 rounded-md bg-white"
            >
              {#each availableGroups as g}
                <option value={g}>{g}</option>
              {/each}
              {#if availableGroups.length === 0}
                <option value="" disabled selected>(no groups yet)</option>
              {/if}
            </select>
            <button
              type="button"
              onclick={() => {
                creatingNewGroup = true;
                newGroupName = '';
              }}
              class="text-blue-600 hover:underline text-sm whitespace-nowrap"
            >+ New group</button>
          </div>
        {:else}
          <div class="space-y-2 p-3 border border-blue-200 rounded-md bg-blue-50/50">
            <div class="flex justify-between items-center">
              <span class="text-xs font-semibold text-blue-700">New group</span>
              <button
                type="button"
                onclick={() => (creatingNewGroup = false)}
                class="text-xs text-slate-500 hover:underline"
              >use existing instead</button>
            </div>
            <div class="grid grid-cols-2 gap-2">
              <input
                type="text"
                bind:value={newGroupName}
                placeholder="Group name (e.g. Energy)"
                class="px-2 py-1 border border-slate-300 rounded text-sm"
              />
              <input
                type="number"
                bind:value={newGroupTargetPct}
                placeholder="Target weight (%)"
                min="0"
                max="100"
                step="0.1"
                class="px-2 py-1 border border-slate-300 rounded text-sm font-mono"
              />
            </div>
            <input
              type="text"
              bind:value={newGroupDescription}
              placeholder="Description (optional)"
              class="w-full px-2 py-1 border border-slate-300 rounded text-sm"
            />
          </div>
        {/if}
      </div>
    {:else}
      <div class="text-xs text-slate-500">
        ETFs are tracked in their own flat sleeve (group =
        <span class="font-mono">ETFs</span>).
      </div>
    {/if}

    <!-- Initial buy (portfolio only) -->
    {#if kind === 'portfolio'}
      <div class="border-t border-slate-200 pt-4">
        <label class="inline-flex items-center gap-2 mb-3">
          <input type="checkbox" bind:checked={withInitialBuy} />
          <span class="text-sm font-medium text-slate-700">Log initial buy now</span>
        </label>
        {#if withInitialBuy}
          <div class="space-y-3 ml-5 border-l-2 border-slate-200 pl-4">
            <div class="grid grid-cols-2 gap-3">
              <label class="block">
                <span class="block text-xs font-medium text-slate-700 mb-0.5">Date</span>
                <input
                  type="date"
                  bind:value={initialDate}
                  class="w-full px-2 py-1 border border-slate-300 rounded text-sm"
                />
              </label>
              <label class="block">
                <span class="block text-xs font-medium text-slate-700 mb-0.5">
                  Amount (EUR)
                </span>
                <input
                  type="number"
                  bind:value={initialAmount}
                  min="0.01"
                  step="0.01"
                  class="w-full px-2 py-1 border border-slate-300 rounded text-sm font-mono"
                />
              </label>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <label class="block">
                <span class="block text-xs font-medium text-slate-700 mb-0.5">
                  Fee (EUR, optional)
                </span>
                <input
                  type="number"
                  bind:value={initialFee}
                  min="0"
                  step="0.01"
                  class="w-full px-2 py-1 border border-slate-300 rounded text-sm font-mono"
                />
              </label>
              <label class="block">
                <span class="block text-xs font-medium text-slate-700 mb-0.5">
                  Note (optional)
                </span>
                <input
                  type="text"
                  bind:value={initialNote}
                  class="w-full px-2 py-1 border border-slate-300 rounded text-sm"
                />
              </label>
            </div>
            <div class="bg-slate-50 border border-slate-200 rounded p-2 text-xs">
              <span class="font-medium text-slate-700">Preview:</span>
              {#if previewing}
                <span class="text-slate-500 ml-1">computing…</span>
              {:else if previewError}
                <span class="text-red-600 ml-1">{previewError}</span>
              {:else if preview}
                <span class="font-mono ml-1">
                  ≈ {preview.shares.toFixed(4)} shares @
                  {preview.listing_currency}
                  {preview.price_local.toFixed(2)}
                  · €/{preview.listing_currency.toLowerCase()} {preview.eur_per_local.toFixed(4)}
                </span>
              {:else}
                <span class="text-slate-400 ml-1">pick a ticker + amount</span>
              {/if}
            </div>
          </div>
        {/if}
      </div>
    {/if}

    {#if error}
      <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
        {error}
      </div>
    {/if}
  </div>

  {#snippet footer()}
    <button
      type="button"
      onclick={onClose}
      class="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900"
    >Cancel</button>
    <button
      type="button"
      onclick={submit}
      disabled={!canSubmit}
      class="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
    >
      {submitting ? 'Adding…' : `Add to ${kind}`}
    </button>
  {/snippet}
</Modal>
