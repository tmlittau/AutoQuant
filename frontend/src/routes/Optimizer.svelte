<script lang="ts">
  /**
   * Optimizer / Rebalance (Phase R4) -- the payoff view. Pick an optimisation
   * method + constraints, and get back suggested target weights, the efficient
   * frontier (with your current + suggested portfolios plotted), and a concrete
   * buy/trim plan vs. your current holdings.
   *
   * Advisory only -- it never executes trades. A "Backtest these methods" link
   * hands off to the Strategy Lab where HRP / min-var / CVaR are A/B-able.
   */
  import { push } from 'svelte-spa-router';
  import { apiGet } from '../lib/api';
  import { transactionsRevision, pricesRevision } from '../lib/stores';
  import { isMobile, pickHeight, pickMargin } from '../lib/responsive';
  import { getCookie } from '../lib/auth';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import { fmtEUR, fmtPct } from '../lib/format';

  type AssetClass = 'stocks' | 'etfs' | 'crypto';

  const METHODS: { key: string; label: string; note: string }[] = [
    { key: 'hrp', label: 'HRP', note: 'Hierarchical Risk Parity — robust default, no return estimates' },
    { key: 'min_variance', label: 'Min variance', note: 'Lowest-risk portfolio on the shrunk covariance' },
    { key: 'cvar', label: 'Min CVaR', note: 'Minimise the worst-case tail loss (Expected Shortfall)' },
    { key: 'black_litterman', label: 'Black-Litterman', note: 'Market prior + your factor scores as views' },
    { key: 'max_sharpe', label: 'Max Sharpe', note: 'Tangency portfolio — fragile; a baseline, not a default' },
  ];

  let assetClass = $state<AssetClass>('stocks');
  let method = $state('hrp');
  let maxWeight = $state(100);     // percent
  let lookbackDays = $state(756);

  let result = $state<any>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function run() {
    loading = true;
    error = null;
    try {
      const r = await fetch('/api/optimize', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify({
          asset_class: assetClass,
          method,
          min_weight: 0,
          max_weight: maxWeight / 100,
          lookback_days: lookbackDays,
        }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b?.detail ?? `optimize failed (${r.status})`);
      }
      result = await r.json();
    } catch (e: any) {
      error = e?.message ?? String(e);
      result = null;
    } finally {
      loading = false;
    }
  }

  // Re-run automatically on the control changes + data refresh.
  $effect(() => {
    const _ac = assetClass, _m = method, _mw = maxWeight, _lb = lookbackDays;
    const _tx = $transactionsRevision, _px = $pricesRevision;
    run();
  });

  // ---- efficient frontier scatter ----
  let frontierData = $derived.by(() => {
    if (!result) return [];
    const traces: any[] = [];
    if (result.frontier_volatility?.length) {
      traces.push({
        type: 'scatter', mode: 'lines',
        x: result.frontier_volatility.map((v: number) => v * 100),
        y: result.frontier_return.map((v: number) => v * 100),
        name: 'Efficient frontier',
        line: { color: '#cbd5e1', width: 2 },
        hovertemplate: 'vol %{x:.1f}%<br>ret %{y:.1f}%<extra></extra>',
      });
    }
    const pt = (p: any, name: string, color: string, symbol: string) =>
      p && {
        type: 'scatter', mode: 'markers', name,
        x: [p.volatility * 100], y: [p.ret * 100],
        marker: { color, size: 13, symbol, line: { color: 'white', width: 1 } },
        hovertemplate: `<b>${name}</b><br>vol %{x:.1f}%<br>ret %{y:.1f}%<extra></extra>`,
      };
    const a = pt(result.current_point, 'Current', '#64748b', 'circle');
    const b = pt(result.target_point, 'Suggested', '#2563eb', 'star');
    const c = pt(result.min_var_point, 'Min variance', '#16a34a', 'diamond');
    const d = pt(result.max_sharpe_point, 'Max Sharpe', '#ea580c', 'triangle-up');
    [a, b, c, d].forEach((t) => t && traces.push(t));
    return traces;
  });
  let frontierLayout = $derived({
    height: pickHeight($isMobile, 380, 320),
    margin: pickMargin($isMobile, { b: 44, l: 48 }),
    xaxis: { title: { text: 'volatility (ann. %)' }, gridcolor: '#f1f5f9', ticksuffix: '%' },
    yaxis: { title: { text: 'return (ann. %)' }, gridcolor: '#f1f5f9', ticksuffix: '%' },
    legend: { orientation: 'h', y: -0.25 },
  });

  // ---- current vs target weight bar ----
  let weightData = $derived.by(() => {
    if (!result?.weights?.length) return [];
    const sorted = [...result.weights].sort((a: any, b: any) => b.target_weight - a.target_weight);
    const t = sorted.map((w: any) => w.ticker);
    return [
      {
        type: 'bar', name: 'Current',
        x: t, y: sorted.map((w: any) => w.current_weight * 100),
        marker: { color: '#cbd5e1' },
        hovertemplate: '<b>%{x}</b> current %{y:.1f}%<extra></extra>',
      },
      {
        type: 'bar', name: 'Suggested',
        x: t, y: sorted.map((w: any) => w.target_weight * 100),
        marker: { color: '#2563eb' },
        hovertemplate: '<b>%{x}</b> suggested %{y:.1f}%<extra></extra>',
      },
    ];
  });
  let weightLayout = $derived({
    barmode: 'group',
    height: pickHeight($isMobile, 320, 260),
    margin: pickMargin($isMobile, { b: 60 }),
    yaxis: { ticksuffix: '%', gridcolor: '#f1f5f9' },
    xaxis: { tickangle: -45, automargin: true },
    legend: { orientation: 'h', y: -0.25 },
  });

  let trades = $derived(
    (result?.trades ?? []).filter((t: any) => Math.abs(t.trade_eur) >= 0.5),
  );
  let methodNote = $derived(METHODS.find((m) => m.key === method)?.note ?? '');
</script>

<div class="space-y-5">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Optimizer</h1>
    <span class="text-sm text-slate-500">
      suggested target weights + rebalance plan · advisory only
    </span>
  </header>

  <!-- Controls -->
  <section class="bg-white border border-slate-200 rounded-xl p-4 space-y-4 text-sm">
    <div class="flex flex-wrap items-end gap-6">
      <div>
        <span class="block text-xs font-medium text-slate-600 mb-1">Asset class</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each ['stocks', 'etfs', 'crypto'] as ac}
            <button type="button" onclick={() => (assetClass = ac as AssetClass)}
              class="px-3 py-1 rounded text-sm capitalize transition {assetClass === ac
                ? 'bg-white shadow-sm text-slate-900 font-medium' : 'text-slate-600 hover:text-slate-900'}"
            >{ac}</button>
          {/each}
        </div>
      </div>
      <label class="block">
        <span class="block text-xs font-medium text-slate-600 mb-1">Method</span>
        <select bind:value={method} class="px-3 py-2 sm:py-1.5 border border-slate-300 rounded-md bg-white text-base sm:text-sm">
          {#each METHODS as mo (mo.key)}<option value={mo.key}>{mo.label}</option>{/each}
        </select>
      </label>
      <div class="flex-1 min-w-0 sm:min-w-[14rem]">
        <span class="block text-xs font-medium text-slate-600 mb-1">Max weight / holding: {maxWeight}%</span>
        <input type="range" min="10" max="100" step="5" bind:value={maxWeight} class="w-full" />
      </div>
      <div class="flex-1 min-w-0 sm:min-w-[12rem]">
        <span class="block text-xs font-medium text-slate-600 mb-1">
          Lookback: {(lookbackDays / 252).toFixed(1)} yr
        </span>
        <input type="range" min="252" max="2520" step="126" bind:value={lookbackDays} class="w-full" />
      </div>
    </div>
    <p class="text-xs text-slate-500">{methodNote}{#if loading} · optimising…{/if}</p>
  </section>

  {#if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
  {:else if result}
    <!-- Frontier + weights -->
    <section class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">Efficient frontier</h2>
        <PlotlyChart data={frontierData} layout={frontierLayout} />
        <p class="mt-1 text-xs text-slate-500">
          The ★ is the suggested portfolio; ● your current one. Up-and-left is better
          (more return per unit of risk).
        </p>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-slate-700 mb-2">Suggested vs current weights</h2>
        <PlotlyChart data={weightData} layout={weightLayout} />
      </div>
    </section>

    <!-- Rebalance plan -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm">
      <div class="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-4">
        <h2 class="text-sm font-semibold text-slate-700">Rebalance plan</h2>
        <div class="text-xs text-slate-500">
          turnover {fmtPct((result.turnover ?? 0) * 100, 1)} · est. cost {fmtEUR(result.est_cost_eur)}
          {#if result.portfolio_value} · book {fmtEUR(result.portfolio_value)}{/if}
        </div>
      </div>
      {#if trades.length === 0}
        <p class="px-4 py-6 text-sm text-slate-500">
          {result.portfolio_value
            ? 'Already at the suggested weights — no trades needed.'
            : 'No current positions to rebalance; the suggested weights are a starting allocation.'}
        </p>
      {:else}
        <div class="overflow-x-auto mt-2">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200 bg-slate-50">
                <th class="py-2 px-4">Ticker</th>
                <th class="py-2 px-3 text-right">Current</th>
                <th class="py-2 px-3 text-right">Suggested</th>
                <th class="py-2 px-3 text-right">Δ</th>
                <th class="py-2 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {#each trades as t (t.ticker)}
                <tr class="border-b border-slate-100">
                  <td class="py-2 px-4 font-mono font-medium">{t.ticker}</td>
                  <td class="py-2 px-3 text-right font-mono text-slate-500">{fmtPct(t.current_weight * 100, 1)}</td>
                  <td class="py-2 px-3 text-right font-mono">{fmtPct(t.target_weight * 100, 1)}</td>
                  <td class="py-2 px-3 text-right font-mono {t.delta_weight >= 0 ? 'text-emerald-600' : 'text-red-600'}">
                    {t.delta_weight >= 0 ? '+' : ''}{fmtPct(t.delta_weight * 100, 1)}
                  </td>
                  <td class="py-2 px-4 text-right font-mono font-medium {t.trade_eur >= 0 ? 'text-emerald-700' : 'text-red-700'}">
                    {t.trade_eur >= 0 ? 'Buy ' : 'Trim '}{fmtEUR(Math.abs(t.trade_eur))}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
      <div class="px-4 py-3 flex flex-wrap items-center gap-3 border-t border-slate-100">
        <span class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          Suggestions only — review and execute trades yourself.
        </span>
        <button type="button" onclick={() => push('/strategy-lab')}
          class="text-xs text-blue-600 hover:underline ml-auto">Backtest these methods in Strategy Lab →</button>
      </div>
    </section>
  {:else if loading}
    <p class="text-sm text-slate-500">Optimising…</p>
  {/if}
</div>
