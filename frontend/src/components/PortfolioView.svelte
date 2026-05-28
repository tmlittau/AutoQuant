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
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import KpiCard from './KpiCard.svelte';
  import AddInvestmentModal from './AddInvestmentModal.svelte';
  import AddStockModal from './AddStockModal.svelte';
  import { fmtEUR, fmtLocal, fmtNum, fmtPct } from '../lib/format';

  type Props = { assetClass: 'stocks' | 'etfs' };
  let { assetClass }: Props = $props();

  // Loaded payloads.
  let snap = $state<any>(null);
  let hist = $state<any>(null);

  // UX state.
  let firstLoad = $state(true);   // distinguishes "loading…" from "refreshing"
  let refreshing = $state(false);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);
  let addStockOpen = $state(false);

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

  // Re-fetch when the asset class changes (prop change) or whenever a
  // transaction mutation bumps the revision store.
  $effect(() => {
    const ac = assetClass;
    const _rev = $transactionsRevision; // dep tracking
    load(ac);
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

  // Sunburst: group -> holding, EUR value.
  let sunburstData = $derived.by(() => {
    if (!snap || snap.positions.length === 0) return [];
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
  const sunburstLayout = { height: 460, margin: { t: 24, l: 0, r: 0, b: 0 } };

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
    height: 360,
    yaxis: { title: { text: '% of portfolio' }, ticksuffix: '%', gridcolor: '#f1f5f9' },
    margin: { t: 20, r: 10, b: 50, l: 60 },
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
  const valueVsInvestedLayout = {
    height: 360,
    margin: { t: 20, r: 16, b: 40, l: 60 },
    yaxis: { tickprefix: '€', tickformat: ',.0f', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.2 },
  };

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
  const stackedByGroupLayout = {
    height: 360,
    margin: { t: 20, r: 16, b: 40, l: 60 },
    yaxis: { tickprefix: '€', tickformat: ',.0f', gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.2 },
  };

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
    height: Math.max(280, 60 + 26 * (snap?.positions?.length ?? 0)),
    margin: { t: 20, r: 80, b: 40, l: 80 },
    xaxis: { title: { text: 'P&L (EUR)' }, tickprefix: '€', gridcolor: '#f1f5f9' },
    yaxis: { automargin: true },
  });

  // Holdings table: sorted by value desc.
  let sortedPositions = $derived(
    snap
      ? [...snap.positions].sort((a: any, b: any) => (b.value_eur ?? 0) - (a.value_eur ?? 0))
      : [],
  );
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
    <!-- Action bar -->
    <div class="flex items-center justify-end gap-3">
      {#if refreshing}
        <span class="text-xs text-slate-500">refreshing…</span>
      {/if}
      <button
        type="button"
        onclick={() => (addStockOpen = true)}
        class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >
        + Add Stock
      </button>
      <button
        type="button"
        onclick={() => (modalOpen = true)}
        class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >
        + Add Investment
      </button>
    </div>

    <!-- KPIs -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
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
      <KpiCard label="Groups" value={String(snap.by_group.length)} />
    </div>

    <!-- Sunburst + Allocation row -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          Allocation by group → holding
        </h2>
        <PlotlyChart data={sunburstData} layout={sunburstLayout} />
      </section>
      <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">
          {#if assetClass === 'stocks'}
            Group allocation vs. target
          {:else}
            Group allocation
          {/if}
        </h2>
        <PlotlyChart data={allocationData} layout={allocationLayout} />
      </section>
    </div>

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

    <!-- Stacked by group -->
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
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr
              class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200"
            >
              <th class="py-2 pr-3">Ticker</th>
              <th class="py-2 pr-3">Name</th>
              <th class="py-2 pr-3">Group</th>
              <th class="py-2 pr-3 text-right">Shares</th>
              <th class="py-2 pr-3 text-right">Last price</th>
              <th class="py-2 pr-3 text-right">Value</th>
              <th class="py-2 pr-3 text-right">Cost</th>
              <th class="py-2 pr-3 text-right">P&L</th>
              <th class="py-2 pr-3 text-right">Return</th>
              <th class="py-2 pr-3 text-right">Weight</th>
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
                <td class="py-2 pr-3 text-slate-500">{p.group}</td>
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
