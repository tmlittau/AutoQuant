<script lang="ts">
  import { onMount } from 'svelte';
  import { link, push } from 'svelte-spa-router';
  import { apiGet } from '../lib/api';
  import KpiCard from '../components/KpiCard.svelte';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import { fmtEUR, fmtPct } from '../lib/format';

  let loading = $state(true);
  let error = $state<string | null>(null);
  let dash = $state<any>(null);

  async function load() {
    loading = true;
    error = null;
    try {
      dash = await apiGet('/api/dashboard');
    } catch (err: any) {
      error = err.message ?? String(err);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  let sparkData = $derived(
    dash
      ? [
          {
            type: 'scatter',
            mode: 'lines',
            x: dash.sparkline.dates,
            y: dash.sparkline.values,
            fill: 'tozeroy',
            line: { color: '#3b82f6', width: 2 },
            fillcolor: 'rgba(59, 130, 246, 0.08)',
            hovertemplate: '€%{y:,.2f}<br>%{x}<extra></extra>',
          },
        ]
      : [],
  );

  let sparkLayout = $derived({
    margin: { t: 8, r: 16, b: 32, l: 60 },
    yaxis: { tickprefix: '€', tickformat: ',.0f', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    showlegend: false,
    height: 240,
  });
</script>

<div class="space-y-6">
  <header class="flex items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Dashboard</h1>
    <span class="text-sm text-slate-500">portfolio at a glance</span>
  </header>

  {#if loading}
    <p class="text-sm text-slate-500">Loading…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      Failed to load dashboard: {error}
    </div>
  {:else if dash}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <KpiCard
        label="Combined value"
        value={fmtEUR(dash.combined.value_eur)}
        delta={fmtPct(dash.combined.return_pct)}
        deltaPositive={dash.combined.return_pct != null
          ? dash.combined.return_pct >= 0
          : null}
      />
      <KpiCard
        label="Invested (cost)"
        value={fmtEUR(dash.combined.cost_eur)}
        delta={`P&L ${fmtEUR(dash.combined.pnl_eur)}`}
        deltaPositive={dash.combined.pnl_eur >= 0}
      />
      <KpiCard
        label="Stocks"
        value={fmtEUR(dash.stocks.value_eur)}
        delta={fmtPct(dash.stocks.return_pct)}
        deltaPositive={dash.stocks.return_pct != null ? dash.stocks.return_pct >= 0 : null}
      />
      <KpiCard
        label="ETFs"
        value={fmtEUR(dash.etfs.value_eur)}
        delta={fmtPct(dash.etfs.return_pct)}
        deltaPositive={dash.etfs.return_pct != null ? dash.etfs.return_pct >= 0 : null}
      />
    </div>

    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Portfolio value over time (EUR)
      </h2>
      {#if dash.sparkline.dates.length > 1}
        <PlotlyChart data={sparkData} layout={sparkLayout} />
      {:else}
        <p class="text-sm text-slate-500">
          Need at least two days of holding history for a sparkline.
        </p>
      {/if}
    </section>

    <section class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-3">Top movers today</h2>
        {#if dash.top_movers.length === 0}
          <p class="text-sm text-slate-500">No movers (need ≥ 2 days of data).</p>
        {:else}
          <ul class="space-y-2">
            {#each dash.top_movers as m (m.ticker)}
              <li class="flex items-center justify-between text-sm">
                <button
                  type="button"
                  onclick={() => push(`/stock/${m.ticker}`)}
                  class="flex flex-col items-start hover:underline"
                >
                  <span class="font-medium">{m.ticker}</span>
                  <span class="text-xs text-slate-500">
                    {m.name} · {m.asset_class}
                  </span>
                </button>
                <span
                  class="font-mono text-sm {m.change_pct >= 0
                    ? 'text-emerald-600'
                    : 'text-red-600'}"
                >
                  {fmtPct(m.change_pct)}
                </span>
              </li>
            {/each}
          </ul>
        {/if}
      </div>

      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-3">Quick links</h2>
        <ul class="space-y-2 text-sm">
          <li>
            <a use:link href="/portfolio/stocks" class="text-blue-600 hover:underline">
              Stock portfolio
            </a>
            <span class="text-slate-500">— positions, allocation, P&L</span>
          </li>
          <li>
            <a use:link href="/portfolio/etfs" class="text-blue-600 hover:underline">
              ETF sleeve
            </a>
            <span class="text-slate-500">— diversified holdings, separate analysis</span>
          </li>
          <li>
            <a
              use:link
              href="/portfolio/diversification"
              class="text-blue-600 hover:underline"
            >
              Diversification
            </a>
            <span class="text-slate-500">— Pearson + distance correlation</span>
          </li>
          <li>
            <a use:link href="/watchlist" class="text-blue-600 hover:underline">
              Watchlist
            </a>
            <span class="text-slate-500">— BUY / WATCH / AVOID candidates</span>
          </li>
        </ul>
      </div>
    </section>
  {/if}
</div>
