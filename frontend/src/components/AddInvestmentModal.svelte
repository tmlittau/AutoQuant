<script lang="ts">
  /**
   * Add-Investment modal: logs a buy on an existing holding.
   *
   * Live preview via /api/instruments/{ticker}/estimate-shares (debounced 400 ms)
   * shows how many shares the EUR amount buys at the EOD close + FX for the
   * chosen date. Submit POSTs /api/transactions, then bumps transactionsRevision
   * so dashboards / portfolio views re-fetch.
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

  type Props = {
    open: boolean;
    onClose: () => void;
    holdings: Holding[];
    /** Pre-select a ticker (used when opened from a holding row). */
    initialTicker?: string;
  };

  let { open, onClose, holdings, initialTicker }: Props = $props();

  // Form state. Reset on open via $effect below.
  let ticker = $state('');
  let date = $state(new Date().toISOString().slice(0, 10));
  let amount = $state(50);
  let fee = $state(0);
  let note = $state('');

  let submitting = $state(false);
  let error = $state<string | null>(null);
  let preview = $state<{
    shares: number;
    price_local: number;
    listing_currency: string;
    eur_per_local: number;
    price_eur: number;
  } | null>(null);
  let previewing = $state(false);
  let previewError = $state<string | null>(null);

  let chosen = $derived(holdings.find((h) => h.ticker === ticker));

  // Reset form whenever the modal opens (so previous values don't leak).
  $effect(() => {
    if (open) {
      ticker = initialTicker || holdings[0]?.ticker || '';
      date = new Date().toISOString().slice(0, 10);
      amount = 50;
      fee = 0;
      note = '';
      error = null;
      preview = null;
      previewError = null;
    }
  });

  // Debounced live preview. NB ``debounceTimer`` is a plain ``let`` rather than
  // ``$state(...)`` -- the effect both reads it (``if (debounceTimer)``) and
  // writes it (``debounceTimer = setTimeout(...)``); making it reactive caused
  // ``effect_update_depth_exceeded`` and froze the modal's close handlers.
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    // Track inputs we care about.
    const t = ticker, d = date, a = amount;
    if (!open || !chosen || !t || !d || a <= 0) {
      preview = null;
      return;
    }
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      previewing = true;
      previewError = null;
      try {
        const r = await apiGet('/api/instruments/{ticker}/estimate-shares', {
          params: {
            path: { ticker: t },
            query: {
              on: d,
              amount_eur: a,
              listing_currency: chosen!.currency,
            },
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

  async function submit() {
    if (!chosen) return;
    submitting = true;
    error = null;
    try {
      const { data, error: e } = await api.POST('/api/transactions', {
        body: {
          date,
          ticker,
          action: 'buy',
          amount_eur: amount,
          listing_currency: chosen.currency,
          fee_eur: fee,
          note: note || '',
        },
      });
      if (e) {
        throw new Error((e as any).detail ?? 'Create failed');
      }
      transactionsRevision.update((n) => n + 1);
      onClose();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      submitting = false;
    }
  }

  let canSubmit = $derived(!submitting && !!chosen && amount > 0 && !!date);
</script>

<Modal {open} {onClose} title="Add Investment">
  <div class="space-y-4 text-sm">
    {#if holdings.length === 0}
      <div class="bg-amber-50 border border-amber-200 text-amber-800 rounded p-3 text-sm">
        No holdings to log against. Add a stock first (Phase 6 will surface the modal;
        for now use the Django admin).
      </div>
    {:else}
      <label class="block">
        <span class="block text-xs font-medium text-slate-700 mb-1">Holding</span>
        <select
          bind:value={ticker}
          class="w-full px-3 py-1.5 border border-slate-300 rounded-md bg-white"
        >
          {#each holdings as h (h.ticker)}
            <option value={h.ticker}>
              {h.ticker} — {h.name} · {h.currency} · {h.group}
            </option>
          {/each}
        </select>
      </label>

      <div class="grid grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Date</span>
          <input
            type="date"
            bind:value={date}
            class="w-full px-3 py-1.5 border border-slate-300 rounded-md"
          />
        </label>
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Amount (EUR)</span>
          <input
            type="number"
            min="0.01"
            step="0.01"
            bind:value={amount}
            class="w-full px-3 py-1.5 border border-slate-300 rounded-md font-mono"
          />
        </label>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Fee (EUR, optional)</span>
          <input
            type="number"
            min="0"
            step="0.01"
            bind:value={fee}
            class="w-full px-3 py-1.5 border border-slate-300 rounded-md font-mono"
          />
        </label>
        <label class="block">
          <span class="block text-xs font-medium text-slate-700 mb-1">Note (optional)</span>
          <input
            type="text"
            bind:value={note}
            placeholder="e.g. monthly contribution"
            class="w-full px-3 py-1.5 border border-slate-300 rounded-md"
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
            <div>
              ≈ <span class="text-slate-900 font-semibold">
                {preview.shares.toFixed(4)} shares
              </span>
              of {ticker}
            </div>
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
      class="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900"
    >Cancel</button>
    <button
      type="button"
      onclick={submit}
      disabled={!canSubmit}
      class="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
    >
      {submitting ? 'Logging…' : 'Log buy'}
    </button>
  {/snippet}
</Modal>
