<script lang="ts">
  /**
   * Diversification view: side-by-side Pearson + distance correlation heatmaps
   * over the portfolio's daily returns. Distance correlation catches non-linear
   * dependence that Pearson misses; the contrast is the whole point.
   *
   * Both fetches share an asset-class + lookback control. Results are cached
   * server-side for 10 min keyed by (metric, asset_class, lookback, tx revision)
   * so the lookback slider feels snappy after the first calc.
   */
  import { apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import { isMobile, pickHeight, pickMargin } from '../lib/responsive';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import { fmtNum } from '../lib/format';

  type AssetClass = 'stocks' | 'etfs';
  type Metric = 'pearson' | 'distance';

  type Payload = {
    metric: Metric;
    asset_class: AssetClass;
    lookback_days: number;
    tickers: string[];
    matrix: (number | null)[][];
    summary: Record<string, any>;
    cached: boolean;
  };

  let assetClass = $state<AssetClass>('stocks');
  let lookback = $state(126);

  let pearson = $state<Payload | null>(null);
  let distance = $state<Payload | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function loadBoth() {
    loading = true;
    error = null;
    try {
      const [pe, di] = await Promise.all([
        apiGet('/api/diversification', {
          params: { query: { metric: 'pearson', asset_class: assetClass, lookback } },
        }),
        apiGet('/api/diversification', {
          params: { query: { metric: 'distance', asset_class: assetClass, lookback } },
        }),
      ]);
      pearson = pe as any;
      distance = di as any;
    } catch (e: any) {
      error = e?.message ?? String(e);
      pearson = null;
      distance = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const _ac = assetClass;
    const _lb = lookback;
    const _rev = $transactionsRevision;
    loadBoth();
  });

  // Heatmap traces ----------------------------------------------------------
  // Distance correlation lives in [0, 1] (no negatives by construction), so a
  // sequential blue-yellow-red ramp is more readable than the Pearson-style
  // diverging RdBu. The 0.3 break-point is where the colorscale flips from
  // blue (well diversified) through yellow (transition) to red (highly
  // coupled) -- chosen by the user, who wants the boundary in a meaningful
  // place for a typical stock portfolio.
  const DCOR_COLORSCALE: [number, string][] = [
    [0, '#4575b4'],
    [0.3, '#ffffbf'],
    [1, '#d73027'],
  ];

  function heatmapTrace(
    p: Payload,
    scheme: string | [number, string][],
    zmin: number,
    zmax: number,
  ) {
    if (!p) return [];
    return [
      {
        type: 'heatmap',
        z: p.matrix,
        x: p.tickers,
        y: p.tickers,
        zmin,
        zmax,
        colorscale: scheme,
        hovertemplate:
          '<b>%{y}</b> ↔ <b>%{x}</b><br>' + p.metric + ' = %{z:.3f}<extra></extra>',
        showscale: true,
        text: p.matrix.map((row) =>
          row.map((v) => (v == null ? '' : v.toFixed(2))),
        ),
        texttemplate: '%{text}',
        textfont: { size: 9 },
      },
    ];
  }

  let pearsonData = $derived(pearson ? heatmapTrace(pearson, 'RdBu', -1, 1) : []);
  let distanceData = $derived(
    distance ? heatmapTrace(distance, DCOR_COLORSCALE, 0, 1) : [],
  );

  let heatmapLayout = $derived({
    height: pickHeight($isMobile, 520, 340),
    margin: $isMobile
      ? { t: 16, r: 8, b: 60, l: 60 }
      : { t: 20, r: 16, b: 80, l: 80 },
    xaxis: { side: 'bottom', tickangle: -45, automargin: true },
    yaxis: { autorange: 'reversed' as const, automargin: true },
  });

  function fmt(v: any, digits = 3) {
    if (v == null || Number.isNaN(v)) return '–';
    return typeof v === 'number' ? v.toFixed(digits) : String(v);
  }
</script>

<div class="space-y-5">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Diversification</h1>
    <span class="text-sm text-slate-500">
      pairwise return correlation — Pearson (linear) and distance (any dependence)
    </span>
  </header>

  <!-- Controls -->
  <section
    class="bg-white border border-slate-200 rounded-xl p-4 flex flex-wrap items-end gap-6 text-sm"
  >
    <div>
      <span class="block text-xs font-medium text-slate-600 mb-1">Asset class</span>
      <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
        {#each ['stocks', 'etfs'] as ac}
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
        Lookback: {lookback} trading days (≈ {(lookback / 21).toFixed(1)} months)
      </span>
      <input
        type="range"
        min="21"
        max="252"
        step="1"
        bind:value={lookback}
        class="w-full"
      />
    </div>
    {#if pearson?.cached || distance?.cached}
      <span
        class="px-2 py-1 rounded bg-amber-50 text-amber-700 border border-amber-200 text-xs"
      >cached</span>
    {/if}
  </section>

  {#if loading && !pearson}
    <p class="text-sm text-slate-500">Computing correlations…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      {error}
    </div>
  {:else if pearson && distance}
    <!-- Summary KPIs -->
    <section class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div class="text-xs uppercase tracking-wide text-slate-500">Pearson avg</div>
        <div class="mt-1 text-2xl font-semibold font-mono">
          {fmt(pearson.summary.avg_corr)}
        </div>
        <div class="text-xs text-slate-500 mt-1">
          effective N ≈ {fmt(pearson.summary.effective_n, 2)} of {pearson.tickers.length}
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div class="text-xs uppercase tracking-wide text-slate-500">Distance avg</div>
        <div class="mt-1 text-2xl font-semibold font-mono">
          {fmt(distance.summary.avg_dcor)}
        </div>
        <div class="text-xs text-slate-500 mt-1">
          effective N ≈ {fmt(distance.summary.effective_n, 2)} of {distance.tickers.length}
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div class="text-xs uppercase tracking-wide text-slate-500">Most coupled</div>
        <div class="mt-1 text-sm font-medium">
          {pearson.summary.max_pair ?? '–'}
        </div>
        <div class="text-xs text-slate-500 font-mono mt-1">
          ρ = {fmt(pearson.summary.max_corr)} · dCor = {fmt(distance.summary.max_dcor)}
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div class="text-xs uppercase tracking-wide text-slate-500">
          Most diversifying
        </div>
        <div class="mt-1 text-sm font-medium">
          {pearson.summary.min_pair ?? '–'}
        </div>
        <div class="text-xs text-slate-500 font-mono mt-1">
          ρ = {fmt(pearson.summary.min_corr)} · dCor (low) = {fmt(distance.summary.min_dcor)}
        </div>
      </div>
    </section>

    <!-- Heatmaps side by side -->
    <section class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Pearson correlation (linear)
        </h2>
        <PlotlyChart data={pearsonData} layout={heatmapLayout} />
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Distance correlation (any dependence)
        </h2>
        <PlotlyChart data={distanceData} layout={heatmapLayout} />
      </div>
    </section>

    <p class="text-xs text-slate-500">
      Reading: low average correlation = better diversified. Distance correlation
      ≥ |Pearson| for any pair; a big gap suggests non-linear coupling Pearson
      missed. Effective N ≈ 1 / avg_corr is "how many truly independent bets you
      have" — compare it to the number of holdings.
    </p>
  {/if}
</div>
