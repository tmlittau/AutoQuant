<script lang="ts">
  /**
   * Shared Portfolio view for either asset class (stocks or ETFs).
   *
   * Re-fetches /api/portfolio + /portfolio/history when the assetClass prop
   * changes, then derives all chart specs reactively. Chart specs are passed
   * to <PlotlyChart>, which calls Plotly.react under the hood (efficient
   * update, no DOM teardown).
   */
  import { push } from 'svelte-spa-router';
  import { apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import { isMobile, pickHeight, pickMargin } from '../lib/responsive';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import KpiCard from './KpiCard.svelte';
  import AddInvestmentModal from './AddInvestmentModal.svelte';
  import AddStockModal from './AddStockModal.svelte';
  import GroupManagerModal from './GroupManagerModal.svelte';
  import { fmtEUR, fmtLocal, fmtNum, fmtPct } from '../lib/format';

  type Props = { assetClass: 'stocks' | 'etfs' };
  let { assetClass }: Props = $props();

  // Loaded payloads.
  let snap = $state<any>(null);
  let hist = $state<any>(null);
  let signals = $state<any>(null);          // stocks only -- BUY/HOLD/TRIM per holding

  // UX state.
  let firstLoad = $state(true);   // distinguishes "loading…" from "refreshing"
  let refreshing = $state(false);
  let signalsLoading = $state(false);
  let signalsError = $state<string | null>(null);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);
  let addStockOpen = $state(false);
  let groupsOpen = $state(false);

  function load(ac: 'stocks' | 'etfs') {
    refreshing = true;
    error = null;
    Promise.all([
      apiGet('/api/portfolio', { params: { query: { asset_class: ac } } }),
      apiGet('/api/portfolio/history', { params: { query: { asset_class: ac } } }),
    ])
      .then(([s, h]) => {
        snap = s;
        hist = h;
      })
      .catch((e: any) => {
        error = e?.message ?? String(e);
      })
      .finally(() => {
        refreshing = false;
        firstLoad = false;
      });
  }

  function loadSignals(ac: 'stocks' | 'etfs', force = false) {
    // Signal scatter is stocks-only; ETFs would just be index funds and the
    // momentum/mean-reversion frame doesn't add anything actionable there.
    if (ac !== 'stocks') {
      signals = null;
      return;
    }
    signalsLoading = true;
    signalsError = null;
    apiGet('/api/portfolio/signals', {
      params: { query: { asset_class: ac, force } },
    })
      .then((r: any) => {
        signals = r;
      })
      .catch((e: any) => {
        signals = null;
        signalsError = e?.message ?? String(e);
      })
      .finally(() => {
        signalsLoading = false;
      });
  }

  // Re-fetch when the asset class changes (prop change) or whenever a
  // transaction mutation bumps the revision store.
  $effect(() => {
    const ac = assetClass;
    const _rev = $transactionsRevision; // dep tracking
    load(ac);
    loadSignals(ac);
  });

  let modalHoldings = $derived(
    (snap?.positions ?? []).map((p: any) => ({
      ticker: p.ticker,
      name: p.name,
      currency: p.currency,
      group: p.group,
    })),
  );

  // -------------------------------------------------------------------------
  // Plotly chart specs (all reactive via $derived; PlotlyChart re-applies via
  // Plotly.react on every change without tearing down the DOM).
  // -------------------------------------------------------------------------

  // Allocation chart: sunburst (group → holding) for stocks, flat donut by
  // ticker for ETFs. ETFs all share the single "ETFs" group so the sunburst
  // collapses to a wedge -- a donut by ticker is more informative.
  let sunburstData = $derived.by(() => {
    if (!snap || snap.positions.length === 0) return [];
    if (assetClass === 'etfs') {
      return [
        {
          type: 'pie',
          hole: 0.4,
          labels: snap.positions.map((p: any) => p.ticker),
          values: snap.positions.map((p: any) => p.value_eur ?? 0),
          textinfo: 'label+percent',
          hovertemplate:
            '<b>%{label}</b><br>€%{value:,.2f}<br>%{percent}<extra></extra>',
        },
      ];
    }
    const groupTotals: Record<string, number> = {};
    for (const p of snap.positions) {
      groupTotals[p.group] = (groupTotals[p.group] ?? 0) + (p.value_eur ?? 0);
    }
    const labels: string[] = [];
    const parents: string[] = [];
    const values: number[] = [];
    for (const [g, v] of Object.entries(groupTotals)) {
      labels.push(g);
      parents.push('');
      values.push(v);
    }
    for (const p of snap.positions) {
      labels.push(p.ticker);
      parents.push(p.group);
      values.push(p.value_eur ?? 0);
    }
    return [
      {
        type: 'sunburst',
        labels,
        parents,
        values,
        branchvalues: 'total',
        textinfo: 'label+percent entry',
        hovertemplate: '<b>%{label}</b><br>€%{value:,.2f}<br>%{percentEntry}<extra></extra>',
        insidetextorientation: 'radial',
      },
    ];
  });
  let sunburstLayout = $derived({
    height: pickHeight($isMobile, 460, 300),
    margin: { t: 24, l: 0, r: 0, b: 0 },
  });

  // Group allocation (actual %) vs target % (only stocks have targets).
  let allocationData = $derived.by(() => {
    if (!snap || snap.by_group.length === 0) return [];
    const groups = snap.by_group.map((g: any) => g.group);
    const actual = snap.by_group.map((g: any) => +((g.weight ?? 0) * 100).toFixed(2));
    const traces: any[] = [
      {
        type: 'bar',
        x: groups,
        y: actual,
        name: 'Actual %',
        marker: { color: '#3b82f6' },
        hovertemplate: '<b>%{x}</b><br>%{y:.2f}%<extra>actual</extra>',
      },
    ];
    if (assetClass === 'stocks') {
      const target = snap.by_group.map((g: any) =>
        g.target_weight != null ? +((g.target_weight ?? 0) * 100).toFixed(2) : null,
      );
      traces.push({
        type: 'bar',
        x: groups,
        y: target,
        name: 'Target %',
        marker: { color: '#cbd5e1' },
        hovertemplate: '<b>%{x}</b><br>%{y:.2f}%<extra>target</extra>',
      });
    }
    return traces;
  });
  let allocationLayout = $derived({
    barmode: 'group',
    height: pickHeight($isMobile, 360, 260),
    yaxis: { title: { text: '% of portfolio' }, ticksuffix: '%', gridcolor: '#f1f5f9' },
    margin: pickMargin($isMobile, { b: 50 }),
    legend: { orientation: 'h', y: -0.25 },
  });

  // Value over time: total vs invested (lines).
  let valueVsInvestedData = $derived.by(() => {
    if (!hist || hist.dates.length === 0) return [];
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: hist.dates,
        y: hist.total,
        name: 'Market value',
        line: { color: '#3b82f6', width: 3 },
        hovertemplate: '€%{y:,.2f}<extra>value</extra>',
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: hist.dates,
        y: hist.invested,
        name: 'Invested (cost)',
        line: { color: '#94a3b8', width: 2, dash: 'dash' },
        hovertemplate: '€%{y:,.2f}<extra>invested</extra>',
      },
    ];
  });
  let valueVsInvestedLayout = $derived({
    height: pickHeight($isMobile, 360, 240),
    margin: pickMargin($isMobile, { b: 40 }),
    yaxis: { tickprefix: '€', tickformat: ',.0f', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.2 },
  });

  // Stacked area by group over time.
  let stackedByGroupData = $derived.by(() => {
    if (!hist || hist.dates.length === 0) return [];
    return Object.keys(hist.by_group).map((g) => ({
      type: 'scatter',
      mode: 'lines',
      x: hist.dates,
      y: hist.by_group[g],
      name: g,
      stackgroup: 'one',
      hovertemplate: '<b>%{fullData.name}</b>: €%{y:,.2f}<extra></extra>',
    }));
  });
  let stackedByGroupLayout = $derived({
    height: pickHeight($isMobile, 360, 240),
    margin: pickMargin($isMobile, { b: 40 }),
    yaxis: { tickprefix: '€', tickformat: ',.0f', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.2 },
  });

  // P&L by holding (horizontal bar coloured by return %).
  let pnlData = $derived.by(() => {
    if (!snap || snap.positions.length === 0) return [];
    const sorted = [...snap.positions].sort(
      (a: any, b: any) => (a.pnl_eur ?? 0) - (b.pnl_eur ?? 0),
    );
    return [
      {
        type: 'bar',
        orientation: 'h',
        x: sorted.map((p: any) => p.pnl_eur ?? 0),
        y: sorted.map((p: any) => p.ticker),
        text: sorted.map((p: any) => fmtPct(p.return_pct)),
        textposition: 'auto',
        marker: {
          color: sorted.map((p: any) => p.return_pct ?? 0),
          colorscale: 'RdYlGn',
          cmid: 0,
          colorbar: { title: { text: 'return %' }, ticksuffix: '%' },
        },
        hovertemplate:
          '<b>%{y}</b><br>P&L €%{x:,.2f}<br>return %{marker.color:.2f}%<extra></extra>',
      },
    ];
  });
  let pnlLayout = $derived({
    height: Math.max($isMobile ? 220 : 280, 60 + 26 * (snap?.positions?.length ?? 0)),
    margin: $isMobile
      ? { t: 16, r: 40, b: 36, l: 60 }
      : { t: 20, r: 80, b: 40, l: 80 },
    xaxis: { title: { text: 'P&L (EUR)' }, tickprefix: '€', gridcolor: '#f1f5f9' },
    yaxis: { automargin: true },
  });

  // Holdings table: sorted by value desc.
  let sortedPositions = $derived(
    snap
      ? [...snap.positions].sort((a: any, b: any) => (b.value_eur ?? 0) - (a.value_eur ?? 0))
      : [],
  );

  // ---- Signal map (BUY / HOLD / TRIM, stocks only) ------------------------
  type Stance = 'BUY' | 'HOLD' | 'TRIM';
  const STANCE_COLOR: Record<Stance, string> = {
    BUY: '#16a34a',     // emerald
    HOLD: '#64748b',    // slate
    TRIM: '#dc2626',    // red
  };

  let signalItems = $derived<any[]>(signals?.items ?? []);
  let okSignals = $derived(signalItems.filter((i: any) => i.status === 'ok'));

  let signalMapData = $derived.by(() => {
    if (okSignals.length === 0) return [];
    const stances: Stance[] = ['BUY', 'HOLD', 'TRIM'];
    return stances.map((s) => {
      const subset = okSignals.filter((i: any) => i.signal === s);
      return {
        type: 'scatter',
        mode: 'markers+text',
        x: subset.map((i: any) => i.momentum ?? 0),
        y: subset.map((i: any) => i.mean_reversion ?? 0),
        text: subset.map((i: any) => i.ticker),
        textposition: 'top center',
        name: s,
        marker: {
          size: subset.map((i: any) => 14 + 30 * Math.abs(i.score ?? 0)),
          color: STANCE_COLOR[s],
          opacity: 0.8,
          line: { color: 'white', width: 1 },
        },
        hovertemplate:
          '<b>%{text}</b><br>trend/momentum=%{x:.2f}<br>mean-reversion=%{y:.2f}<extra>' +
          s +
          '</extra>',
      };
    });
  });

  let signalMapLayout = $derived({
    height: pickHeight($isMobile, 460, 320),
    margin: pickMargin($isMobile, { b: 50 }),
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
  });

  // Quick lookup so the holdings table can display per-row stance badges.
  let signalByTicker = $derived<Record<string, any>>(
    Object.fromEntries(signalItems.map((i: any) => [i.ticker, i])),
  );

  function stanceBadgeClass(s: string | null | undefined) {
    if (s === 'BUY') return 'bg-emerald-100 text-emerald-700 border-emerald-200';
    if (s === 'TRIM') return 'bg-red-100 text-red-700 border-red-200';
    return 'bg-slate-100 text-slate-600 border-slate-200';
  }
</script>

<div class="space-y-4">
  {#if firstLoad}
    <p class="text-sm text-slate-500">Loading {assetClass}…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      Failed to load: {error}
    </div>
  {:else if snap && snap.positions.length === 0}
    <div class="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-4 text-sm">
      No {assetClass} holdings yet. Add one from the <strong>Add Stock</strong> flow
      (coming in Phase 6) or directly in the Django admin for now.
    </div>
  {:else if snap}
    <!-- Action bar: stacks full-width on mobile, inline on desktop. -->
    <div class="flex flex-wrap items-stretch justify-end gap-2">
      {#if refreshing}
        <span class="w-full sm:w-auto text-xs text-slate-500 self-center text-right">
          refreshing…
        </span>
      {/if}
      {#if assetClass === 'stocks'}
        <button
          type="button"
          onclick={() => (groupsOpen = true)}
          class="w-full sm:w-auto px-4 py-2 min-h-[44px] text-sm border border-slate-300 rounded-md hover:bg-slate-50"
        >
          Manage groups
        </button>
      {/if}
      <button
        type="button"
        onclick={() => (addStockOpen = true)}
        class="w-full sm:w-auto px-4 py-2 min-h-[44px] text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >
        {#if assetClass === 'etfs'}+ Add ETF{:else}+ Add Stock{/if}
      </button>
      <button
        type="button"
        onclick={() => (modalOpen = true)}
        class="w-full sm:w-auto px-4 py-2 min-h-[44px] text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >
        + Add Investment
      </button>
    </div>

    <!-- KPIs (skip the "Groups" card for ETFs -- they live in one flat sleeve) -->
    <div class="grid grid-cols-1 sm:grid-cols-2 {assetClass === 'stocks' ? 'lg:grid-cols-4' : 'lg:grid-cols-3'} gap-4">
      <KpiCard
        label="Value (EUR)"
        value={fmtEUR(snap.totals.value_eur)}
        delta={fmtPct(snap.totals.return_pct)}
        deltaPositive={snap.totals.return_pct != null
          ? snap.totals.return_pct >= 0
          : null}
      />
      <KpiCard
        label="Invested (EUR)"
        value={fmtEUR(snap.totals.cost_eur)}
        delta={'P&L ' + fmtEUR(snap.totals.pnl_eur)}
        deltaPositive={snap.totals.pnl_eur >= 0}
      />
      <KpiCard label="Holdings" value={String(snap.positions.length)} />
      {#if assetClass === 'stocks'}
        <KpiCard label="Groups" value={String(snap.by_group.length)} />
      {/if}
    </div>

    {#if assetClass === 'stocks'}
      <!-- Sunburst + Allocation row (stocks only -- ETFs don't have groups) -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
          <h2 class="text-sm font-semibold text-slate-700 mb-2">
            Allocation by group → holding
          </h2>
          <PlotlyChart data={sunburstData} layout={sunburstLayout} />
        </section>
        <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
          <h2 class="text-sm font-semibold text-slate-700 mb-2">
            Group allocation vs. target
          </h2>
          <PlotlyChart data={allocationData} layout={allocationLayout} />
        </section>
      </div>
    {:else}
      <!-- ETFs: a flat allocation donut is all the breakdown that makes sense. -->
      <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Allocation by holding
        </h2>
        <PlotlyChart data={sunburstData} layout={sunburstLayout} />
      </section>
    {/if}

    <!-- Signal map (stocks only) -->
    {#if assetClass === 'stocks'}
      <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div class="flex items-baseline justify-between mb-2">
          <h2 class="text-sm font-semibold text-slate-700">
            BUY / HOLD / TRIM · trend &amp; momentum vs mean-reversion
          </h2>
          <div class="flex items-center gap-2">
            {#if signals?.cached}
              <span
                class="text-xs px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                title="Cached scores (refresh recomputes)">cached</span
              >
            {/if}
            {#if signalsLoading}
              <span class="text-xs text-slate-500">scoring…</span>
            {/if}
            <button
              type="button"
              onclick={() => loadSignals(assetClass, true)}
              class="text-xs text-blue-600 hover:underline"
            >Refresh</button>
          </div>
        </div>
        {#if signalsError}
          <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
            {signalsError}
          </div>
        {:else if signalsLoading && okSignals.length === 0}
          <p class="text-sm text-slate-500">
            Scoring your stocks (this can take 5–15 s on a cold cache)…
          </p>
        {:else if okSignals.length === 0}
          <p class="text-sm text-slate-500">No stock signals available yet.</p>
        {:else}
          <PlotlyChart data={signalMapData} layout={signalMapLayout} />
          <p class="mt-2 text-xs text-slate-500">
            Bubble size = |composite score|. Use the BUY quadrant (top-right /
            bottom-right with positive momentum) to bias next month's deposit
            toward oversold or trending names; TRIM signals suggest stretched
            positions you may want to underweight.
          </p>
        {/if}
      </section>
    {/if}

    <!-- Value vs invested -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Market value vs. invested (EUR)
      </h2>
      {#if hist && hist.dates.length > 1}
        <PlotlyChart data={valueVsInvestedData} layout={valueVsInvestedLayout} />
      {:else}
        <p class="text-sm text-slate-500">
          Need at least two days of holding history.
        </p>
      {/if}
    </section>

    {#if assetClass === 'stocks'}
      <!-- Stacked by group (stocks only) -->
      <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Value by group over time (stacked)
        </h2>
        {#if hist && hist.dates.length > 1}
          <PlotlyChart data={stackedByGroupData} layout={stackedByGroupLayout} />
        {:else}
          <p class="text-sm text-slate-500">
            Need at least two days of holding history.
          </p>
        {/if}
      </section>
    {/if}

    <!-- P&L bar -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">P&L by holding (EUR)</h2>
      <PlotlyChart data={pnlData} layout={pnlLayout} />
    </section>

    <!-- Holdings table -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-sm font-semibold text-slate-700">Holdings</h2>
        {#if refreshing}
          <span class="text-xs text-slate-500">refreshing…</span>
        {/if}
      </div>
      <!-- Mobile: vertical card-stack (each row as a card with label/value
           pairs). Hidden at md+; the full table below takes over. -->
      <ul class="md:hidden space-y-2">
        {#each sortedPositions as p (p.ticker)}
          <li class="border border-slate-200 rounded-lg p-3 bg-white">
            <div class="flex items-start justify-between gap-2">
              <button
                type="button"
                onclick={() => push(`/stock/${p.ticker}`)}
                class="font-mono font-semibold text-base text-blue-600 hover:underline text-left min-h-[44px]"
              >
                {p.ticker}
                <span class="block text-xs text-slate-500 font-sans font-normal truncate max-w-[60vw]">
                  {p.name}
                </span>
              </button>
              {#if assetClass === 'stocks' && signalByTicker[p.ticker]?.signal}
                <span
                  class="px-2 py-0.5 rounded text-xs font-medium border whitespace-nowrap {stanceBadgeClass(
                    signalByTicker[p.ticker].signal,
                  )}"
                >
                  {signalByTicker[p.ticker].signal}
                </span>
              {/if}
            </div>
            <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt class="text-slate-500">Value</dt>
              <dd class="text-right font-mono font-medium">{fmtEUR(p.value_eur)}</dd>
              <dt class="text-slate-500">P&L</dt>
              <dd
                class="text-right font-mono {(p.pnl_eur ?? 0) >= 0
                  ? 'text-emerald-600'
                  : 'text-red-600'}"
              >
                {fmtEUR(p.pnl_eur)}
              </dd>
              <dt class="text-slate-500">Return</dt>
              <dd
                class="text-right font-mono {(p.return_pct ?? 0) >= 0
                  ? 'text-emerald-600'
                  : 'text-red-600'}"
              >
                {fmtPct(p.return_pct)}
              </dd>
              <dt class="text-slate-500">Weight</dt>
              <dd class="text-right font-mono">{fmtPct((p.weight ?? 0) * 100, 1)}</dd>
              <dt class="text-slate-500">Shares</dt>
              <dd class="text-right font-mono">{fmtNum(p.shares, 4)}</dd>
              <dt class="text-slate-500">Last price</dt>
              <dd class="text-right font-mono text-slate-600">
                {fmtLocal(p.price_local, p.currency)}
              </dd>
            </dl>
          </li>
        {/each}
      </ul>

      <!-- Desktop: full table (hidden at <md). -->
      <div class="hidden md:block overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr
              class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200"
            >
              <th class="py-2 pr-3">Ticker</th>
              <th class="py-2 pr-3">Name</th>
              {#if assetClass === 'stocks'}
                <th class="py-2 pr-3">Group</th>
              {/if}
              <th class="py-2 pr-3 text-right">Shares</th>
              <th class="py-2 pr-3 text-right">Last price</th>
              <th class="py-2 pr-3 text-right">Value</th>
              <th class="py-2 pr-3 text-right">Cost</th>
              <th class="py-2 pr-3 text-right">P&L</th>
              <th class="py-2 pr-3 text-right">Return</th>
              <th class="py-2 pr-3 text-right">Weight</th>
              {#if assetClass === 'stocks'}
                <th class="py-2 pr-3 text-center">Signal</th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each sortedPositions as p (p.ticker)}
              <tr class="border-b border-slate-100 hover:bg-slate-50">
                <td class="py-2 pr-3">
                  <button
                    type="button"
                    onclick={() => push(`/stock/${p.ticker}`)}
                    class="font-mono font-medium text-blue-600 hover:underline"
                  >
                    {p.ticker}
                  </button>
                </td>
                <td class="py-2 pr-3">{p.name}</td>
                {#if assetClass === 'stocks'}
                  <td class="py-2 pr-3 text-slate-500">{p.group}</td>
                {/if}
                <td class="py-2 pr-3 text-right font-mono">{fmtNum(p.shares, 4)}</td>
                <td class="py-2 pr-3 text-right font-mono text-slate-600">
                  {fmtLocal(p.price_local, p.currency)}
                </td>
                <td class="py-2 pr-3 text-right font-mono">{fmtEUR(p.value_eur)}</td>
                <td class="py-2 pr-3 text-right font-mono text-slate-500">
                  {fmtEUR(p.cost_eur)}
                </td>
                <td
                  class="py-2 pr-3 text-right font-mono {(p.pnl_eur ?? 0) >= 0
                    ? 'text-emerald-600'
                    : 'text-red-600'}"
                >
                  {fmtEUR(p.pnl_eur)}
                </td>
                <td
                  class="py-2 pr-3 text-right font-mono {(p.return_pct ?? 0) >= 0
                    ? 'text-emerald-600'
                    : 'text-red-600'}"
                >
                  {fmtPct(p.return_pct)}
                </td>
                <td class="py-2 pr-3 text-right font-mono">
                  {fmtPct((p.weight ?? 0) * 100, 1)}
                </td>
                {#if assetClass === 'stocks'}
                  <td class="py-2 pr-3 text-center">
                    {#if signalByTicker[p.ticker]?.signal}
                      <span
                        class="px-2 py-0.5 rounded text-xs font-medium border {stanceBadgeClass(
                          signalByTicker[p.ticker].signal,
                        )}"
                        title={`score ${fmtNum(signalByTicker[p.ticker].score, 2)}`}
                      >
                        {signalByTicker[p.ticker].signal}
                      </span>
                    {:else}
                      <span class="text-xs text-slate-400">—</span>
                    {/if}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
</div>

<AddInvestmentModal
  open={modalOpen}
  onClose={() => (modalOpen = false)}
  holdings={modalHoldings}
/>

<AddStockModal
  open={addStockOpen}
  onClose={() => (addStockOpen = false)}
  initial={{ kind: 'portfolio', assetClass }}
/>

{#if assetClass === 'stocks'}
  <GroupManagerModal
    open={groupsOpen}
    onClose={() => (groupsOpen = false)}
    assetClass="stocks"
  />
{/if}
