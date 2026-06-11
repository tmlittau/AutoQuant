<script lang="ts">
  /**
   * Risk analytics (Phase R1). For the chosen asset class it shows the
   * risk/return metric suite of the *current* allocation (Sharpe / Sortino /
   * Calmar / CVaR / max-drawdown / beta), the rolling volatility + underwater
   * (drawdown) curve, and the per-holding risk contributions -- a sharper
   * "what actually drives my risk" lens than the correlation heatmap.
   *
   * Mirrors the Diversification view's asset-class toggle + lookback control.
   */
  import { apiGet } from '../lib/api';
  import { transactionsRevision, pricesRevision } from '../lib/stores';
  import { isMobile, pickHeight, pickMargin } from '../lib/responsive';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import KpiCard from '../components/KpiCard.svelte';
  import { fmtPct, fmtNum } from '../lib/format';

  type AssetClass = 'stocks' | 'etfs' | 'crypto';

  let assetClass = $state<AssetClass>('stocks');
  let lookback = $state(252);

  let data = $state<any>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    error = null;
    try {
      data = await apiGet('/api/portfolio/risk', {
        params: { query: { asset_class: assetClass, lookback } },
      });
    } catch (e: any) {
      error = e?.message ?? String(e);
      data = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const _ac = assetClass;
    const _lb = lookback;
    const _tx = $transactionsRevision;
    const _px = $pricesRevision;
    load();
  });

  let m = $derived(data?.metrics ?? {});
  let hasData = $derived(!!data && data.n_obs > 0);

  // ---- Rolling vol + underwater drawdown (two stacked panels) ----
  let volData = $derived.by(() => {
    if (!data || !data.dates?.length) return [];
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: data.dates,
        y: data.rolling_volatility.map((v: number | null) =>
          v == null ? null : v * 100,
        ),
        line: { color: '#7c3aed', width: 1.5 },
        hovertemplate: '%{y:.1f}%<extra>ann. vol</extra>',
      },
    ];
  });
  let volLayout = $derived({
    height: pickHeight($isMobile, 200, 160),
    margin: pickMargin($isMobile, { b: 30 }),
    yaxis: { title: { text: 'ann. vol' }, ticksuffix: '%', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    showlegend: false,
  });

  let ddData = $derived.by(() => {
    if (!data || !data.dates?.length) return [];
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: data.dates,
        y: data.drawdown.map((v: number | null) => (v == null ? null : v * 100)),
        fill: 'tozeroy',
        line: { color: '#dc2626', width: 1.2 },
        fillcolor: 'rgba(220, 38, 38, 0.15)',
        hovertemplate: '%{y:.1f}%<extra>drawdown</extra>',
      },
    ];
  });
  let ddLayout = $derived({
    height: pickHeight($isMobile, 200, 160),
    margin: pickMargin($isMobile, { b: 30 }),
    yaxis: { title: { text: 'drawdown' }, ticksuffix: '%', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    showlegend: false,
  });

  // ---- Risk contribution: % of portfolio risk vs weight, per holding ----
  let contribData = $derived.by(() => {
    if (!data || !data.contributions?.length) return [];
    const sorted = [...data.contributions].sort(
      (a: any, b: any) => (b.pct_contribution ?? 0) - (a.pct_contribution ?? 0),
    );
    const tickers = sorted.map((c: any) => c.ticker);
    return [
      {
        type: 'bar',
        x: tickers,
        y: sorted.map((c: any) => (c.weight ?? 0) * 100),
        name: 'Weight',
        marker: { color: '#cbd5e1' },
        hovertemplate: '<b>%{x}</b><br>%{y:.1f}% weight<extra></extra>',
      },
      {
        type: 'bar',
        x: tickers,
        y: sorted.map((c: any) => (c.pct_contribution ?? 0) * 100),
        name: 'Risk share',
        marker: { color: '#7c3aed' },
        hovertemplate: '<b>%{x}</b><br>%{y:.1f}% of portfolio risk<extra></extra>',
      },
    ];
  });
  let contribLayout = $derived({
    barmode: 'group',
    height: pickHeight($isMobile, 340, 260),
    margin: pickMargin($isMobile, { b: 60 }),
    yaxis: { ticksuffix: '%', gridcolor: '#f1f5f9' },
    xaxis: { tickangle: -45, automargin: true },
    legend: { orientation: 'h', y: -0.25 },
  });

  function pct(v: number | null | undefined, digits = 1) {
    return v == null ? '–' : `${(v * 100).toFixed(digits)}%`;
  }
  function ratio(v: number | null | undefined, digits = 2) {
    return v == null ? '–' : v.toFixed(digits);
  }
</script>

<div class="space-y-5">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Risk</h1>
    <span class="text-sm text-slate-500">
      risk/return profile of your current allocation, on EUR returns
    </span>
  </header>

  <!-- Controls -->
  <section
    class="bg-white border border-slate-200 rounded-xl p-4 flex flex-wrap items-end gap-6 text-sm"
  >
    <div>
      <span class="block text-xs font-medium text-slate-600 mb-1">Asset class</span>
      <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
        {#each ['stocks', 'etfs', 'crypto'] as ac}
          <button
            type="button"
            onclick={() => (assetClass = ac as AssetClass)}
            class="px-3 py-1 rounded text-sm capitalize transition {assetClass === ac
              ? 'bg-white shadow-sm text-slate-900 font-medium'
              : 'text-slate-600 hover:text-slate-900'}"
          >{ac}</button>
        {/each}
      </div>
    </div>
    <div class="flex-1 min-w-0 sm:min-w-[18rem] w-full">
      <span class="block text-xs font-medium text-slate-600 mb-1">
        Lookback: {lookback} trading days (≈ {(lookback / 21).toFixed(0)} months)
      </span>
      <input type="range" min="63" max="756" step="21" bind:value={lookback} class="w-full" />
    </div>
    {#if data?.cached}
      <span class="px-2 py-1 rounded bg-amber-50 text-amber-700 border border-amber-200 text-xs">cached</span>
    {/if}
  </section>

  {#if loading && !data}
    <p class="text-sm text-slate-500">Computing risk metrics…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
  {:else if !hasData}
    <div class="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-4 text-sm">
      No {assetClass} holdings with enough price history yet.
    </div>
  {:else}
    <!-- KPI cards -->
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
      <KpiCard label="Sharpe" value={ratio(m.sharpe)} />
      <KpiCard label="Sortino" value={ratio(m.sortino)} />
      <KpiCard label="Calmar" value={ratio(m.calmar)} />
      <KpiCard
        label="Max drawdown"
        value={pct(m.max_drawdown)}
        deltaPositive={false}
      />
      <KpiCard label="Volatility (ann.)" value={pct(m.ann_volatility)} />
      <KpiCard
        label="CVaR 95% (daily)"
        value={pct(m.cvar_95)}
        delta={`VaR ${pct(m.var_95)}`}
      />
      <KpiCard label="Beta (vs {data.benchmark ?? '—'})" value={ratio(m.beta)} />
      <KpiCard
        label="Effective bets"
        value={ratio(data.effective_bets)}
        delta={`of ${data.contributions.length} holdings`}
      />
    </div>

    <!-- Rolling vol + drawdown -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">Rolling volatility (21d, annualised)</h2>
        <PlotlyChart data={volData} layout={volLayout} />
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">Underwater (drawdown from peak)</h2>
        <PlotlyChart data={ddData} layout={ddLayout} />
      </div>
    </section>

    <!-- Risk contribution -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Risk contribution vs weight
      </h2>
      <PlotlyChart data={contribData} layout={contribLayout} />
      <p class="text-xs text-slate-500 mt-2">
        A holding whose <strong>risk share</strong> bar towers over its
        <strong>weight</strong> bar is punching above its size — it dominates
        portfolio risk through volatility/correlation, not capital.
        <strong>Effective bets</strong> ({ratio(data.effective_bets)}) is how many
        truly-independent risk positions you hold; closer to your holding count is
        better diversified.
        {#if data.shrinkage_intensity != null}
          Covariance uses Ledoit-Wolf shrinkage (intensity
          {ratio(data.shrinkage_intensity)} — higher means the raw sample
          covariance was noisier).
        {/if}
      </p>
    </section>
  {/if}
</div>
