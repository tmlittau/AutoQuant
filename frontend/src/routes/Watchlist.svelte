<script lang="ts">
  /**
   * Watchlist screener: BUY / WATCH / AVOID recommendations for every name
   * NOT currently owned, sourced from ``signals.watchlist_signals``.
   * Renders a scored table + a 2-D signal map (x = trend/momentum sub-score,
   * y = mean-reversion sub-score, colour = recommendation, size = |score|).
   * Click a row to deep-dive into the Single-stock view.
   */
  import { push } from 'svelte-spa-router';
  import { onMount } from 'svelte';
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import AddStockModal from '../components/AddStockModal.svelte';
  import { fmtNum, fmtPct, fmtLocal } from '../lib/format';

  type Item = {
    ticker: string;
    name: string;
    group: string;
    currency: string;
    status: string;
    recommendation: string;
    last_price?: number | null;
    roc_20d_pct?: number | null;
    rsi_14?: number | null;
    zscore_20?: number | null;
    trend?: number | null;
    momentum?: number | null;
    macd?: number | null;
    mean_reversion?: number | null;
    score?: number | null;
    signal?: string | null;
  };

  let items = $state<Item[]>([]);
  let cached = $state(false);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);

  async function load(force = false) {
    loading = true;
    error = null;
    try {
      const r = await apiGet('/api/watchlist/signals', {
        params: { query: { force } },
      });
      items = (r as any).items ?? [];
      cached = !!(r as any).cached;
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => load(false));

  // Re-fetch when the holdings/transactions revision bumps (a new watchlist
  // entry was added or a holding was removed).
  $effect(() => {
    const _rev = $transactionsRevision;
    if (_rev > 0) load(false);
  });

  // ---------- Signal map scatter ----------
  type Reco = 'BUY' | 'WATCH' | 'AVOID';
  const REC_COLOR: Record<Reco, string> = {
    BUY: '#16a34a',
    WATCH: '#64748b',
    AVOID: '#dc2626',
  };

  let okItems = $derived(items.filter((i) => i.status === 'ok'));

  let signalMapData = $derived.by(() => {
    if (okItems.length === 0) return [];
    const recos: Reco[] = ['BUY', 'WATCH', 'AVOID'];
    return recos.map((rec) => {
      const subset = okItems.filter((i) => i.recommendation === rec);
      return {
        type: 'scatter',
        mode: 'markers+text',
        x: subset.map((i) => i.momentum ?? 0),
        y: subset.map((i) => i.mean_reversion ?? 0),
        text: subset.map((i) => i.ticker),
        textposition: 'top center',
        name: rec,
        marker: {
          size: subset.map((i) => 14 + 30 * Math.abs(i.score ?? 0)),
          color: REC_COLOR[rec],
          opacity: 0.8,
          line: { color: 'white', width: 1 },
        },
        hovertemplate:
          '<b>%{text}</b><br>trend/momentum=%{x:.2f}<br>mean-reversion=%{y:.2f}<extra>' +
          rec +
          '</extra>',
      };
    });
  });

  let signalMapLayout = $derived.by(() => ({
    height: 460,
    margin: { t: 16, r: 16, b: 50, l: 60 },
    xaxis: {
      title: { text: 'trend / momentum (sub-score)' },
      zeroline: true,
      zerolinecolor: '#94a3b8',
      gridcolor: '#f1f5f9',
      range: [-1.1, 1.1],
    },
    yaxis: {
      title: { text: 'mean-reversion (higher = oversold)' },
      zeroline: true,
      zerolinecolor: '#94a3b8',
      gridcolor: '#f1f5f9',
      range: [-1.1, 1.1],
    },
    legend: { orientation: 'h', y: -0.18 },
    shapes: [
      // Quadrant labels via shapes -- simple lines
      {
        type: 'line',
        x0: 0,
        x1: 0,
        y0: -1.1,
        y1: 1.1,
        line: { color: 'rgba(148,163,184,0.5)', width: 1, dash: 'dot' },
      },
      {
        type: 'line',
        x0: -1.1,
        x1: 1.1,
        y0: 0,
        y1: 0,
        line: { color: 'rgba(148,163,184,0.5)', width: 1, dash: 'dot' },
      },
    ],
  }));

  // Sort table by score desc (nulls last).
  let sortedItems = $derived(
    [...items].sort((a, b) => {
      const sa = a.score ?? Number.NEGATIVE_INFINITY;
      const sb = b.score ?? Number.NEGATIVE_INFINITY;
      return sb - sa;
    }),
  );

  function recoBadge(rec: string) {
    if (rec === 'BUY') return 'bg-emerald-100 text-emerald-700 border-emerald-200';
    if (rec === 'AVOID') return 'bg-red-100 text-red-700 border-red-200';
    return 'bg-slate-100 text-slate-600 border-slate-200';
  }
  function statusBadge(s: string) {
    if (s === 'ok') return 'bg-emerald-50 text-emerald-700';
    if (s === 'rate-limited') return 'bg-amber-50 text-amber-700';
    return 'bg-slate-100 text-slate-500';
  }
</script>

<div class="space-y-4">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Watchlist</h1>
    <span class="text-sm text-slate-500">{items.length} candidates</span>
    {#if cached}
      <span
        class="text-xs px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
        title="Cached signals (refresh recomputes)">cached</span
      >
    {/if}
    <div class="ml-auto flex gap-2">
      <button
        type="button"
        onclick={() => load(true)}
        class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >Refresh</button>
      <button
        type="button"
        onclick={() => (modalOpen = true)}
        class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >+ Add to Watchlist</button>
    </div>
  </header>

  {#if loading && items.length === 0}
    <p class="text-sm text-slate-500">Scoring watchlist (this can take 5-15 s on a cold cache)…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      {error}
    </div>
  {:else}
    <!-- Signal map -->
    {#if okItems.length > 0}
      <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Signal map · trend/momentum vs mean-reversion
        </h2>
        <PlotlyChart data={signalMapData} layout={signalMapLayout} />
      </section>
    {/if}

    <!-- Scoreboard -->
    <section
      class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-x-auto"
    >
      <table class="w-full text-sm">
        <thead>
          <tr
            class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200 bg-slate-50"
          >
            <th class="py-2 px-3">Ticker</th>
            <th class="py-2 px-3">Name</th>
            <th class="py-2 px-3">Group</th>
            <th class="py-2 px-3 text-right">Price</th>
            <th class="py-2 px-3 text-right">RSI</th>
            <th class="py-2 px-3 text-right">z20</th>
            <th class="py-2 px-3 text-right">ROC 20d</th>
            <th class="py-2 px-3 text-right">Score</th>
            <th class="py-2 px-3">Rec</th>
            <th class="py-2 px-3">Status</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedItems as i (i.ticker)}
            <tr
              class="border-b border-slate-100 hover:bg-slate-50 cursor-pointer"
              onclick={() => push(`/stock/${encodeURIComponent(i.ticker)}`)}
            >
              <td class="py-2 px-3">
                <span class="font-mono font-medium text-blue-600 hover:underline">
                  {i.ticker}
                </span>
              </td>
              <td class="py-2 px-3">{i.name}</td>
              <td class="py-2 px-3 text-slate-500">{i.group}</td>
              <td class="py-2 px-3 text-right font-mono">
                {fmtLocal(i.last_price, i.currency)}
              </td>
              <td class="py-2 px-3 text-right font-mono">{fmtNum(i.rsi_14, 1)}</td>
              <td
                class="py-2 px-3 text-right font-mono {(i.zscore_20 ?? 0) >= 2
                  ? 'text-red-600'
                  : (i.zscore_20 ?? 0) <= -2
                    ? 'text-emerald-600'
                    : ''}"
              >
                {fmtNum(i.zscore_20, 2)}
              </td>
              <td
                class="py-2 px-3 text-right font-mono {(i.roc_20d_pct ?? 0) >= 0
                  ? 'text-emerald-600'
                  : 'text-red-600'}"
              >
                {fmtPct(i.roc_20d_pct, 1)}
              </td>
              <td class="py-2 px-3 text-right font-mono font-semibold">
                {fmtNum(i.score, 2)}
              </td>
              <td class="py-2 px-3">
                <span
                  class="px-2 py-0.5 rounded text-xs font-medium border {recoBadge(i.recommendation)}"
                >
                  {i.recommendation}
                </span>
              </td>
              <td class="py-2 px-3">
                <span
                  class="px-2 py-0.5 rounded text-xs font-mono {statusBadge(i.status)}"
                >
                  {i.status}
                </span>
              </td>
            </tr>
          {/each}
          {#if items.length === 0}
            <tr>
              <td colspan="10" class="py-8 text-center text-sm text-slate-500">
                Watchlist is empty. Click "+ Add to Watchlist" to add a candidate.
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </section>
  {/if}
</div>

<AddStockModal
  open={modalOpen}
  onClose={() => (modalOpen = false)}
  initial={{ kind: 'watchlist', assetClass: 'stocks' }}
/>
