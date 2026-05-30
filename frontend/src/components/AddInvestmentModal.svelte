<script lang="ts">
  /**
   * Log a transaction against existing holdings.
   *
   * For stocks / ETFs the action toggle is Buy / Sell. For crypto the same
   * underlying buy/sell actions are relabelled Deposit / Withdraw, plus a
   * third Swap mode that records a coin-for-coin exchange (e.g. 1000 USDC ->
   * 0.025 BTC) as a single linked pair via POST /api/transactions/swap.
   *
   *   - Buy / Deposit  -> EUR amount; backend computes share/coin count.
   *   - Sell / Withdraw -> EUR amount OR units (with a "Withdraw all"
   *                        shortcut sourced from /api/holdings/{t}/position).
   *   - Swap           -> from-coin + from-amount, to-coin + to-amount,
   *                        optional EUR value (else priced from the from leg).
   *
   * On submit, transactionsRevision is bumped so portfolio + dashboard views
   * refetch.
   */
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import { fmtCoin } from '../lib/format';
  import Modal from './Modal.svelte';

  type Holding = {
    ticker: string;
    name: string;
    currency: string;
    group: string;
  };

  type AssetClass = 'stocks' | 'etfs' | 'crypto';
  type Action = 'buy' | 'sell' | 'swap';
  type Mode = 'amount_eur' | 'shares';

  type Props = {
    open: boolean;
    onClose: () => void;
    holdings: Holding[];
    /** Pre-select a ticker (used when opened from a holding row). */
    initialTicker?: string;
    /** Pre-set the action; defaults to 'buy'. */
    initialAction?: Action;
    /** Drives crypto relabelling + the Swap option. Defaults to 'stocks'. */
    assetClass?: AssetClass;
  };

  let {
    open,
    onClose,
    holdings,
    initialTicker,
    initialAction,
    assetClass = 'stocks',
  }: Props = $props();

  let isCrypto = $derived(assetClass === 'crypto');

  // -------------------------------------------------------------------------
  // Form state
  // -------------------------------------------------------------------------
  let action = $state<Action>('buy');
  let mode = $state<Mode>('amount_eur');   // sells only; buys stay EUR.
  let ticker = $state('');
  let date = $state(new Date().toISOString().slice(0, 10));
  let amount = $state(50);                  // EUR
  let shares = $state(1);                   // units (sell+units mode)
  let fee = $state(0);
  let note = $state('');

  // Swap-only state.
  let fromTicker = $state('');
  let fromAmount = $state(0);
  let toTicker = $state('');
  let toAmount = $state(0);
  let eurValue = $state<number | null>(null);  // optional override

  let submitting = $state(false);
  let error = $state<string | null>(null);

  // Live preview (single-leg only).
  type EurPreview = {
    shares: number; price_local: number; listing_currency: string;
    eur_per_local: number; price_eur: number;
  };
  type UnitsPreview = EurPreview & { amount_eur: number };
  let preview = $state<EurPreview | UnitsPreview | null>(null);
  let previewing = $state(false);
  let previewError = $state<string | null>(null);

  // Current holdings position (for "Sell/Withdraw all").
  let position = $state<{ shares: number; cost_eur: number } | null>(null);

  let chosen = $derived(holdings.find((h) => h.ticker === ticker));
  let fromChosen = $derived(holdings.find((h) => h.ticker === fromTicker));
  let toChosen = $derived(holdings.find((h) => h.ticker === toTicker));

  // -------------------------------------------------------------------------
  // Reset form whenever the modal opens.
  // -------------------------------------------------------------------------
  $effect(() => {
    if (open) {
      action = initialAction ?? 'buy';
      mode = 'amount_eur';
      ticker = initialTicker || holdings[0]?.ticker || '';
      date = new Date().toISOString().slice(0, 10);
      amount = 50;
      shares = 1;
      fee = 0;
      note = '';
      error = null;
      preview = null;
      previewError = null;
      position = null;
      // Swap defaults: from = first holding, to = second (distinct).
      fromTicker = holdings[0]?.ticker || '';
      toTicker = holdings.find((h) => h.ticker !== fromTicker)?.ticker || '';
      fromAmount = 0;
      toAmount = 0;
      eurValue = null;
    }
  });

  // -------------------------------------------------------------------------
  // Fetch current position when ticker / action changes (single-leg sells).
  // -------------------------------------------------------------------------
  $effect(() => {
    if (!open || action === 'swap') return;
    const t = ticker;
    const _act = action;
    if (!t) {
      position = null;
      return;
    }
    apiGet('/api/holdings/{ticker}/position', { params: { path: { ticker: t } } })
      .then((r: any) => {
        position = { shares: r.shares ?? 0, cost_eur: r.cost_eur ?? 0 };
      })
      .catch(() => {
        position = null;
      });
  });

  function withdrawAll() {
    if (!position) return;
    mode = 'shares';
    shares = Number(position.shares.toFixed(8));
  }

  // -------------------------------------------------------------------------
  // Live preview (single-leg, debounced 400 ms).
  // -------------------------------------------------------------------------
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const t = ticker, d = date;
    const useUnits = action === 'sell' && mode === 'shares';
    const value = useUnits ? shares : amount;
    if (!open || action === 'swap' || !chosen || !t || !d || value <= 0) {
      preview = null;
      return;
    }
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      previewing = true;
      previewError = null;
      try {
        if (useUnits) {
          const r = await apiGet('/api/instruments/{ticker}/estimate-proceeds', {
            params: { path: { ticker: t },
              query: { on: d, shares: value, listing_currency: chosen!.currency } },
          });
          preview = r as any;
        } else {
          const r = await apiGet('/api/instruments/{ticker}/estimate-shares', {
            params: { path: { ticker: t },
              query: { on: d, amount_eur: value, listing_currency: chosen!.currency } },
          });
          preview = r as any;
        }
      } catch (e: any) {
        preview = null;
        previewError = e?.message ?? 'preview failed';
      } finally {
        previewing = false;
      }
    }, 400);
  });

  // -------------------------------------------------------------------------
  // Submit
  // -------------------------------------------------------------------------
  async function submit() {
    submitting = true;
    error = null;
    try {
      if (action === 'swap') {
        await submitSwap();
      } else {
        await submitSingle();
      }
      transactionsRevision.update((n) => n + 1);
      onClose();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      submitting = false;
    }
  }

  async function submitSingle() {
    if (!chosen) throw new Error('pick a holding');
    const body: Record<string, any> = {
      date,
      ticker,
      action,                              // buy | sell (deposit/withdraw map to these)
      listing_currency: chosen.currency,
      fee_eur: fee,
      note: note || '',
    };
    if (action === 'sell' && mode === 'shares') {
      body.shares = shares;
    } else {
      body.amount_eur = amount;
    }
    const { error: e } = await api.POST('/api/transactions', { body: body as any });
    if (e) throw new Error((e as any).detail ?? 'Create failed');
  }

  async function submitSwap() {
    if (!fromChosen || !toChosen) throw new Error('pick both coins');
    if (fromTicker === toTicker) throw new Error('from and to must differ');
    if (fromAmount <= 0 || toAmount <= 0) throw new Error('amounts must be > 0');
    const body: Record<string, any> = {
      date,
      from_ticker: fromTicker,
      from_amount: fromAmount,
      from_currency: fromChosen.currency,
      to_ticker: toTicker,
      to_amount: toAmount,
      to_currency: toChosen.currency,
      fee_eur: fee,
      note: note || '',
    };
    if (eurValue != null && Number.isFinite(eurValue) && eurValue > 0) {
      body.eur_value = eurValue;
    }
    const { error: e } = await api.POST('/api/transactions/swap', { body: body as any });
    if (e) throw new Error((e as any).detail ?? 'Swap failed');
  }

  let canSubmit = $derived.by(() => {
    if (submitting || !date) return false;
    if (action === 'swap') {
      return (
        !!fromTicker && !!toTicker && fromTicker !== toTicker &&
        fromAmount > 0 && toAmount > 0
      );
    }
    if (!chosen) return false;
    if (action === 'sell' && mode === 'shares') return shares > 0;
    return amount > 0;
  });

  // -------------------------------------------------------------------------
  // Display labels (crypto relabels buy/sell -> Deposit/Withdraw).
  // -------------------------------------------------------------------------
  const ACTIONS: { value: Action; cryptoOnly?: boolean }[] = [
    { value: 'buy' },
    { value: 'sell' },
    { value: 'swap', cryptoOnly: true },
  ];
  function actionLabel(a: Action): string {
    if (isCrypto) {
      if (a === 'buy') return 'Deposit';
      if (a === 'sell') return 'Withdraw';
      return 'Swap';
    }
    return a === 'buy' ? 'Buy' : 'Sell';
  }
  let visibleActions = $derived(
    ACTIONS.filter((a) => !a.cryptoOnly || isCrypto),
  );
  let modalTitle = $derived(
    action === 'swap' ? 'Swap coins' : `${actionLabel(action)} · ${isCrypto ? 'crypto' : assetClass}`,
  );
  let submitLabel = $derived.by(() => {
    if (submitting) return action === 'swap' ? 'Swapping…' : 'Logging…';
    if (action === 'swap') return 'Log swap';
    return `Log ${actionLabel(action).toLowerCase()}`;
  });
  // Implied swap rate for the preview line.
  let swapRate = $derived(
    fromAmount > 0 && toAmount > 0 ? toAmount / fromAmount : null,
  );
</script>

<Modal {open} {onClose} title={modalTitle}>
  <div class="space-y-4 text-sm">
    {#if holdings.length === 0}
      <div class="bg-amber-50 border border-amber-200 text-amber-800 rounded p-3 text-sm">
        No holdings to log against. Add one first via the
        <strong>{isCrypto ? '+ Add Coin' : '+ Add Stock'}</strong> button on the
        Portfolio view.
      </div>
    {:else}
      <!-- Action toggle -->
      <div>
        <span class="block text-xs font-medium text-slate-700 mb-1">Action</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each visibleActions as a (a.value)}
            <button
              type="button"
              onclick={() => (action = a.value)}
              class="px-4 py-2 min-h-[40px] rounded text-sm transition {action === a.value
                ? a.value === 'sell'
                  ? 'bg-white shadow-sm text-red-700 font-medium'
                  : 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >{actionLabel(a.value)}</button>
          {/each}
        </div>
      </div>

      {#if action === 'swap'}
        <!-- ============== SWAP FORM ============== -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">From (spend)</span>
            <select
              bind:value={fromTicker}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md bg-white text-base sm:text-sm"
            >
              {#each holdings as h (h.ticker)}
                <option value={h.ticker}>{h.ticker} — {h.name}</option>
              {/each}
            </select>
          </label>
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">
              Amount of {fromTicker || 'coin'}
            </span>
            <input
              type="number" inputmode="decimal" min="0" step="any"
              bind:value={fromAmount}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
            />
          </label>
        </div>

        <div class="flex justify-center text-slate-400 text-lg">↓</div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">To (receive)</span>
            <select
              bind:value={toTicker}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md bg-white text-base sm:text-sm"
            >
              {#each holdings as h (h.ticker)}
                <option value={h.ticker} disabled={h.ticker === fromTicker}>
                  {h.ticker} — {h.name}
                </option>
              {/each}
            </select>
          </label>
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">
              Amount of {toTicker || 'coin'}
            </span>
            <input
              type="number" inputmode="decimal" min="0" step="any"
              bind:value={toAmount}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
            />
          </label>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">Date</span>
            <input
              type="date" bind:value={date}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md text-base sm:text-sm"
            />
          </label>
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">
              EUR value (optional)
            </span>
            <input
              type="number" inputmode="decimal" min="0" step="0.01"
              bind:value={eurValue}
              placeholder="auto from price"
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
            />
          </label>
        </div>

        <div class="bg-slate-50 border border-slate-200 rounded-md p-3 text-xs">
          {#if swapRate}
            <span class="font-mono">
              1 {fromTicker} ≈ {fmtCoin(swapRate, toTicker)} {toTicker}
            </span>
            {#if eurValue}
              <span class="text-slate-500"> · €{eurValue.toFixed(2)} value</span>
            {:else}
              <span class="text-slate-400"> · EUR value priced from the from-coin</span>
            {/if}
          {:else}
            <span class="text-slate-400">enter both amounts to see the implied rate</span>
          {/if}
        </div>
      {:else}
        <!-- ============== SINGLE-LEG (buy/sell/deposit/withdraw) ============== -->
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">
            {isCrypto ? 'Coin' : 'Holding'}
          </span>
          <select
            bind:value={ticker}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md bg-white text-base sm:text-sm"
          >
            {#each holdings as h (h.ticker)}
              <option value={h.ticker}>{h.ticker} — {h.name} · {h.currency}</option>
            {/each}
          </select>
          {#if action === 'sell' && position}
            <p class="mt-1 text-xs text-slate-500">
              Current position:
              <span class="font-mono font-medium text-slate-700">
                {fmtCoin(position.shares, ticker)}
              </span> {ticker}
            </p>
          {/if}
        </label>

        {#if action === 'sell'}
          <div>
            <span class="block text-xs font-medium text-slate-700 mb-1">Enter as</span>
            <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
              <button
                type="button" onclick={() => (mode = 'amount_eur')}
                class="px-3 py-2 min-h-[40px] rounded text-sm transition {mode === 'amount_eur'
                  ? 'bg-white shadow-sm text-slate-900 font-medium'
                  : 'text-slate-600 hover:text-slate-900'}"
              >EUR amount</button>
              <button
                type="button" onclick={() => (mode = 'shares')}
                class="px-3 py-2 min-h-[40px] rounded text-sm transition {mode === 'shares'
                  ? 'bg-white shadow-sm text-slate-900 font-medium'
                  : 'text-slate-600 hover:text-slate-900'}"
              >{isCrypto ? 'Coin units' : 'Units'}</button>
            </div>
          </div>
        {/if}

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">Date</span>
            <input
              type="date" bind:value={date}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md text-base sm:text-sm"
            />
          </label>
          {#if action === 'sell' && mode === 'shares'}
            <label class="block">
              <span class="flex items-center justify-between text-xs font-medium text-slate-700 mb-1">
                <span>{isCrypto ? 'Coins to withdraw' : 'Shares to sell'}</span>
                {#if position && position.shares > 0}
                  <button
                    type="button" onclick={withdrawAll}
                    class="text-blue-600 hover:underline font-normal"
                  >{isCrypto ? 'Withdraw' : 'Sell'} all ({fmtCoin(position.shares, ticker)})</button>
                {/if}
              </span>
              <input
                type="number" inputmode="decimal" min="0" step="any"
                bind:value={shares}
                class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
              />
            </label>
          {:else}
            <label class="block">
              <span class="block text-xs font-medium text-slate-700 mb-1">Amount (EUR)</span>
              <input
                type="number" inputmode="decimal" min="0.01" step="0.01"
                bind:value={amount}
                class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
              />
            </label>
          {/if}
        </div>

        <div class="bg-slate-50 border border-slate-200 rounded-md p-3 text-xs">
          <div class="font-medium text-slate-700 mb-1">Preview (EOD close on {date})</div>
          {#if previewing}
            <span class="text-slate-500">computing…</span>
          {:else if previewError}
            <span class="text-red-600">{previewError}</span>
          {:else if preview}
            <div class="font-mono leading-relaxed">
              {#if action === 'sell' && mode === 'shares'}
                <div>≈ <span class="text-slate-900 font-semibold">
                  €{(preview as UnitsPreview).amount_eur.toFixed(2)} proceeds
                </span> from {fmtCoin((preview as UnitsPreview).shares, ticker)} {ticker}</div>
              {:else}
                <div>≈ <span class="text-slate-900 font-semibold">
                  {fmtCoin(preview.shares, ticker)} {isCrypto ? 'coins' : 'shares'}
                </span> of {ticker}</div>
              {/if}
              <div class="text-slate-600">
                {preview.listing_currency} {preview.price_local.toFixed(2)} ·
                €{preview.price_eur.toFixed(2)} each
              </div>
            </div>
          {:else}
            <span class="text-slate-400">pick a {isCrypto ? 'coin' : 'holding'} and enter an amount</span>
          {/if}
        </div>
      {/if}

      <!-- Fee + note (shared by all modes) -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Fee (EUR, optional)</span>
          <input
            type="number" inputmode="decimal" min="0" step="0.01"
            bind:value={fee}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
          />
        </label>
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Note (optional)</span>
          <input
            type="text" bind:value={note}
            placeholder={action === 'swap'
              ? 'e.g. rotate stables into BTC'
              : isCrypto && action === 'sell'
                ? 'e.g. spent on hardware'
                : 'e.g. monthly contribution'}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md text-base sm:text-sm"
          />
        </label>
      </div>

      {#if error}
        <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
          {error}
        </div>
      {/if}
    {/if}
  </div>

  {#snippet footer()}
    <button
      type="button" onclick={onClose}
      class="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 rounded-md"
    >Cancel</button>
    <button
      type="button" onclick={submit} disabled={!canSubmit}
      class="px-4 py-2 text-sm {action === 'sell'
        ? 'bg-red-600 hover:bg-red-700'
        : 'bg-blue-600 hover:bg-blue-700'} text-white rounded-md disabled:bg-slate-300 disabled:cursor-not-allowed"
    >{submitLabel}</button>
  {/snippet}
</Modal>
