<script lang="ts">
  /**
   * Log a buy or sell against an existing holding.
   *
   * Buys are EUR-only (you say "I bought €100 of AAPL" and the backend
   * computes the share count from EOD close + FX). Sells support **both**
   * entry modes:
   *
   *   - EUR amount  -> mirrors buy ("I sold €100 of AAPL")
   *   - Units       -> "I sold 5 AAPL" with a one-tap "Sell all" prefill
   *                    sourced from /api/holdings/{ticker}/position.
   *
   * Live preview hits /estimate-shares (EUR mode) or /estimate-proceeds
   * (units mode) so the user sees the implied other side before submit.
   * On submit, transactionsRevision is bumped so portfolio + dashboard
   * views refetch.
   */
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import Modal from './Modal.svelte';

  type Holding = {
    ticker: string;
    name: string;
    currency: string;
    group: string;
  };

  type Action = 'buy' | 'sell';
  type Mode = 'amount_eur' | 'shares';

  type Props = {
    open: boolean;
    onClose: () => void;
    holdings: Holding[];
    /** Pre-select a ticker (used when opened from a holding row). */
    initialTicker?: string;
    /** Pre-set the action; defaults to 'buy'. */
    initialAction?: Action;
  };

  let { open, onClose, holdings, initialTicker, initialAction }: Props = $props();

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

  let submitting = $state(false);
  let error = $state<string | null>(null);

  // Live preview (depends on action+mode).
  type EurPreview = {
    shares: number;
    price_local: number;
    listing_currency: string;
    eur_per_local: number;
    price_eur: number;
  };
  type UnitsPreview = {
    shares: number;
    price_local: number;
    listing_currency: string;
    eur_per_local: number;
    price_eur: number;
    amount_eur: number;
  };
  let preview = $state<EurPreview | UnitsPreview | null>(null);
  let previewing = $state(false);
  let previewError = $state<string | null>(null);

  // Current holdings position (for "Sell all").
  let position = $state<{ shares: number; cost_eur: number } | null>(null);
  let positionLoading = $state(false);

  let chosen = $derived(holdings.find((h) => h.ticker === ticker));

  // -------------------------------------------------------------------------
  // Reset form whenever the modal opens. Wrapped in untrack pattern below
  // by reading state only inside the effect body (we don't read internal
  // state like ticker on the RHS, so no feedback loop).
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
    }
  });

  // -------------------------------------------------------------------------
  // Fetch current position whenever the user picks (or changes) the ticker
  // OR flips the action to "sell". Powers the "Sell all" shortcut.
  // -------------------------------------------------------------------------
  $effect(() => {
    if (!open) return;
    const t = ticker;
    const _act = action;
    if (!t) {
      position = null;
      return;
    }
    positionLoading = true;
    apiGet('/api/holdings/{ticker}/position', {
      params: { path: { ticker: t } },
    })
      .then((r: any) => {
        position = { shares: r.shares ?? 0, cost_eur: r.cost_eur ?? 0 };
      })
      .catch(() => {
        position = null;
      })
      .finally(() => {
        positionLoading = false;
      });
  });

  function sellAll() {
    if (!position) return;
    mode = 'shares';
    shares = Number(position.shares.toFixed(8));
  }

  // -------------------------------------------------------------------------
  // Live preview (debounced 400 ms). Routes to /estimate-shares (EUR mode)
  // or /estimate-proceeds (units mode).
  // -------------------------------------------------------------------------
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const t = ticker, d = date;
    const useUnits = action === 'sell' && mode === 'shares';
    const value = useUnits ? shares : amount;
    if (!open || !chosen || !t || !d || value <= 0) {
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
            params: {
              path: { ticker: t },
              query: { on: d, shares: value, listing_currency: chosen!.currency },
            },
          });
          preview = r as any;
        } else {
          const r = await apiGet('/api/instruments/{ticker}/estimate-shares', {
            params: {
              path: { ticker: t },
              query: { on: d, amount_eur: value, listing_currency: chosen!.currency },
            },
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
    if (!chosen) return;
    submitting = true;
    error = null;
    try {
      // Build the body for whichever entry mode the user picked. Backend
      // requires exactly one of amount_eur / shares.
      const body: Record<string, any> = {
        date,
        ticker,
        action,
        listing_currency: chosen.currency,
        fee_eur: fee,
        note: note || '',
      };
      if (action === 'sell' && mode === 'shares') {
        body.shares = shares;
      } else {
        body.amount_eur = amount;
      }
      const { error: e } = await api.POST('/api/transactions', {
        body: body as any,
      });
      if (e) throw new Error((e as any).detail ?? 'Create failed');
      transactionsRevision.update((n) => n + 1);
      onClose();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      submitting = false;
    }
  }

  let canSubmit = $derived.by(() => {
    if (submitting || !chosen || !date) return false;
    if (action === 'sell' && mode === 'shares') return shares > 0;
    return amount > 0;
  });

  // Pretty title for the modal header.
  let modalTitle = $derived(action === 'sell' ? 'Log sell' : 'Add Investment');
</script>

<Modal {open} {onClose} title={modalTitle}>
  <div class="space-y-4 text-sm">
    {#if holdings.length === 0}
      <div class="bg-amber-50 border border-amber-200 text-amber-800 rounded p-3 text-sm">
        No holdings to log against. Add a stock first via the
        <strong>+ Add Stock</strong> button on the Portfolio view.
      </div>
    {:else}
      <!-- Action toggle (Buy / Sell) -->
      <div>
        <span class="block text-xs font-medium text-slate-700 mb-1">Action</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each ['buy', 'sell'] as a (a)}
            <button
              type="button"
              onclick={() => (action = a as Action)}
              class="px-4 py-2 min-h-[40px] rounded text-sm capitalize transition {action === a
                ? a === 'sell'
                  ? 'bg-white shadow-sm text-red-700 font-medium'
                  : 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >{a}</button>
          {/each}
        </div>
      </div>

      <label class="block">
        <span class="block text-xs font-medium text-slate-700 mb-1">Holding</span>
        <select
          bind:value={ticker}
          class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md bg-white text-base sm:text-sm"
        >
          {#each holdings as h (h.ticker)}
            <option value={h.ticker}>
              {h.ticker} — {h.name} · {h.currency} · {h.group}
            </option>
          {/each}
        </select>
        {#if action === 'sell' && position}
          <p class="mt-1 text-xs text-slate-500">
            Current position:
            <span class="font-mono font-medium text-slate-700">
              {position.shares.toFixed(4)}
            </span> {ticker}
            {#if position.cost_eur > 0}
              · cost basis €{position.cost_eur.toFixed(2)}
            {/if}
          </p>
        {/if}
      </label>

      <!-- Sell-only: EUR vs Units mode toggle -->
      {#if action === 'sell'}
        <div>
          <span class="block text-xs font-medium text-slate-700 mb-1">Enter as</span>
          <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
            <button
              type="button"
              onclick={() => (mode = 'amount_eur')}
              class="px-3 py-2 min-h-[40px] rounded text-sm transition {mode === 'amount_eur'
                ? 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >EUR amount</button>
            <button
              type="button"
              onclick={() => (mode = 'shares')}
              class="px-3 py-2 min-h-[40px] rounded text-sm transition {mode === 'shares'
                ? 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >Units</button>
          </div>
        </div>
      {/if}

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Date</span>
          <input
            type="date"
            bind:value={date}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md text-base sm:text-sm"
          />
        </label>
        {#if action === 'sell' && mode === 'shares'}
          <label class="block">
            <span class="flex items-center justify-between text-xs font-medium text-slate-700 mb-1">
              <span>Shares to sell</span>
              {#if position && position.shares > 0}
                <button
                  type="button"
                  onclick={sellAll}
                  class="text-blue-600 hover:underline font-normal"
                >Sell all ({position.shares.toFixed(4)})</button>
              {/if}
            </span>
            <input
              type="number"
              inputmode="decimal"
              min="0.0001"
              step="0.0001"
              bind:value={shares}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
            />
          </label>
        {:else}
          <label class="block">
            <span class="block text-xs font-medium text-slate-700 mb-1">
              Amount (EUR)
            </span>
            <input
              type="number"
              inputmode="decimal"
              min="0.01"
              step="0.01"
              bind:value={amount}
              class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
            />
          </label>
        {/if}
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Fee (EUR, optional)</span>
          <input
            type="number"
            inputmode="decimal"
            min="0"
            step="0.01"
            bind:value={fee}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md font-mono text-base sm:text-sm"
          />
        </label>
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Note (optional)</span>
          <input
            type="text"
            bind:value={note}
            placeholder={action === 'sell' ? 'e.g. reinvest into ETF' : 'e.g. monthly contribution'}
            class="w-full px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md text-base sm:text-sm"
          />
        </label>
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
              <div>
                ≈ <span class="text-slate-900 font-semibold">
                  €{(preview as UnitsPreview).amount_eur.toFixed(2)} proceeds
                </span>
                from {(preview as UnitsPreview).shares.toFixed(4)} {ticker}
              </div>
            {:else}
              <div>
                ≈ <span class="text-slate-900 font-semibold">
                  {preview.shares.toFixed(4)} shares
                </span>
                of {ticker}
              </div>
            {/if}
            <div class="text-slate-600">
              {preview.listing_currency}
              {preview.price_local.toFixed(2)} / share ·
              EUR per {preview.listing_currency} = {preview.eur_per_local.toFixed(4)} ·
              €{preview.price_eur.toFixed(2)} / share
            </div>
          </div>
        {:else}
          <span class="text-slate-400">pick a holding and enter an amount</span>
        {/if}
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
      type="button"
      onclick={onClose}
      class="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 rounded-md"
    >Cancel</button>
    <button
      type="button"
      onclick={submit}
      disabled={!canSubmit}
      class="px-4 py-2 text-sm {action === 'sell'
        ? 'bg-red-600 hover:bg-red-700'
        : 'bg-blue-600 hover:bg-blue-700'} text-white rounded-md disabled:bg-slate-300 disabled:cursor-not-allowed"
    >
      {submitting
        ? (action === 'sell' ? 'Logging sell…' : 'Logging…')
        : (action === 'sell' ? 'Log sell' : 'Log buy')}
    </button>
  {/snippet}
</Modal>
