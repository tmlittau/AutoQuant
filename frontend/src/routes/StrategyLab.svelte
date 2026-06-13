<script lang="ts">
  /**
   * Strategy Lab (Phase R2) -- the validation surface. Pick strategies, a
   * rebalance cadence + cost, and run a walk-forward backtest over the asset
   * class's EUR price history. Renders overlaid equity curves plus a metric
   * table with the Probabilistic and Deflated Sharpe ratios -- the latter is
   * the overfitting-corrected number to trust (green when DSR > 0.95).
   *
   * This is what makes every later claim ("the factor model beats the
   * baseline") checkable rather than asserted.
   */
  import { api, apiGet } from '../lib/api';
  import { isMobile, pickHeight, pickMargin } from '../lib/responsive';
  import { getCookie } from '../lib/auth';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import { fmtPct } from '../lib/format';

  type AssetClass = 'stocks' | 'etfs' | 'crypto';
  type StratInfo = { key: string; label: string; default_rebalance: string };

  let assetClass = $state<AssetClass>('stocks');
  let rebalance = $state<'M' | 'W' | 'Q'>('M');
  let costBps = $state(10);
  let lookbackDays = $state(756);

  let available = $state<StratInfo[]>([]);
  let selected = $state<Record<string, boolean>>({});

  let result = $state<any>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  // Load the strategy registry once; default-select the no-optimizer baselines.
  $effect(() => {
    apiGet('/api/backtest/strategies')
      .then((r: any) => {
        available = (r as StratInfo[]) ?? [];
        if (Object.keys(selected).length === 0) {
          const init: Record<string, boolean> = {};
          for (const s of available) {
            init[s.key] = ['buy_and_hold', 'equal_weight', 'inverse_vol', 'manual_target'].includes(
              s.key,
            );
          }
          selected = init;
        }
      })
      .catch(() => {
        available = [];
      });
  });

  let selectedKeys = $derived(
    available.filter((s) => selected[s.key]).map((s) => s.key),
  );

  async function run() {
    if (selectedKeys.length === 0) {
      error = 'Pick at least one strategy.';
      return;
    }
    loading = true;
    error = null;
    try {
      // POST with an array body; use raw fetch so the typed client doesn't fight
      // the union types, but keep CSRF + credentials.
      const r = await fetch('/api/backtest', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({
          asset_class: assetClass,
          strategies: selectedKeys,
          rebalance,
          cost_bps: costBps,
          lookback_days: lookbackDays,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail ?? `backtest failed (${r.status})`);
      }
      result = await r.json();
    } catch (e: any) {
      error = e?.message ?? String(e);
      result = null;
    } finally {
      loading = false;
    }
  }

  // ---- equity-curve overlay ----
  const PALETTE = ['#2563eb', '#16a34a', '#ea580c', '#7c3aed', '#dc2626', '#0891b2', '#ca8a04'];
  let curveData = $derived.by(() => {
    if (!result?.results?.length) return [];
    return result.results.map((res: any, i: number) => ({
      type: 'scatter',
      mode: 'lines',
      x: result.dates,
      y: res.equity_curve,
      name: res.key,
      line: { color: PALETTE[i % PALETTE.length], width: 2 },
      hovertemplate: `<b>${res.key}</b><br>%{x}<br>×%{y:.3f}<extra></extra>`,
    }));
  });
  let curveLayout = $derived({
    height: pickHeight($isMobile, 420, 300),
    margin: pickMargin($isMobile, { b: 40 }),
    yaxis: { title: { text: 'growth of €1' }, gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.2 },
  });

  function num(v: number | null | undefined, d = 2) {
    return v == null ? '–' : v.toFixed(d);
  }
  function dsrClass(v: number | null | undefined) {
    if (v == null) return 'text-slate-400';
    if (v >= 0.95) return 'text-emerald-700 font-semibold';
    if (v >= 0.8) return 'text-amber-600';
    return 'text-red-600';
  }
  // Best DSR row gets a subtle highlight.
  let bestKey = $derived.by(() => {
    if (!result?.results?.length) return null;
    let best: any = null;
    for (const r of result.results) {
      if (r.dsr != null && (best == null || r.dsr > best.dsr)) best = r;
    }
    return best?.key ?? null;
  });
</script>

<div class="space-y-5">
  <header class="flex flex-wrap items-baseline gap-3">
    <h1 class="text-2xl font-semibold text-slate-900">Strategy Lab</h1>
    <span class="text-sm text-slate-500">
      walk-forward backtest · transaction costs · overfitting-corrected Sharpe
    </span>
  </header>

  <!-- Controls -->
  <section class="bg-white border border-slate-200 rounded-xl p-4 space-y-4 text-sm">
    <div class="flex flex-wrap items-end gap-6">
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
      <div>
        <span class="block text-xs font-medium text-slate-600 mb-1">Rebalance</span>
        <div class="inline-flex rounded-md border border-slate-200 p-0.5 bg-slate-50">
          {#each [['W', 'Weekly'], ['M', 'Monthly'], ['Q', 'Quarterly']] as [v, label]}
            <button
              type="button"
              onclick={() => (rebalance = v as 'M' | 'W' | 'Q')}
              class="px-3 py-1 rounded text-sm transition {rebalance === v
                ? 'bg-white shadow-sm text-slate-900 font-medium'
                : 'text-slate-600 hover:text-slate-900'}"
            >{label}</button>
          {/each}
        </div>
      </div>
      <label class="block">
        <span class="block text-xs font-medium text-slate-600 mb-1">Cost (bps / trade)</span>
        <input
          type="number" min="0" max="200" step="1" bind:value={costBps}
          class="w-24 px-2 py-1 border border-slate-300 rounded text-sm font-mono"
        />
      </label>
      <div class="flex-1 min-w-0 sm:min-w-[16rem]">
        <span class="block text-xs font-medium text-slate-600 mb-1">
          Lookback: {lookbackDays} days (≈ {(lookbackDays / 252).toFixed(1)} yr)
        </span>
        <input type="range" min="252" max="2520" step="126" bind:value={lookbackDays} class="w-full" />
      </div>
    </div>

    <!-- Strategy multi-select -->
    <div>
      <span class="block text-xs font-medium text-slate-600 mb-1">Strategies</span>
      <div class="flex flex-wrap gap-2">
        {#each available as s (s.key)}
          <label
            class="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer {selected[s.key]
              ? 'border-blue-300 bg-blue-50 text-blue-800'
              : 'border-slate-200 text-slate-600 hover:bg-slate-50'}"
            title={s.label}
          >
            <input type="checkbox" bind:checked={selected[s.key]} class="sr-only" />
            <span>{s.label}</span>
          </label>
        {/each}
      </div>
    </div>

    <div class="flex items-center gap-3">
      <button
        type="button"
        onclick={run}
        disabled={loading || selectedKeys.length === 0}
        class="px-4 py-2 min-h-[44px] text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
      >{loading ? 'Running backtest…' : 'Run backtest'}</button>
      <span class="text-xs text-slate-500">{selectedKeys.length} selected</span>
    </div>
  </section>

  {#if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
  {/if}

  {#if result}
    <!-- Equity curves -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Equity curves · growth of €1 ({result.dates.length} days)
      </h2>
      <PlotlyChart data={curveData} layout={curveLayout} />
    </section>

    <!-- Metric table -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200 bg-slate-50">
            <th class="py-2 px-3">Strategy</th>
            <th class="py-2 px-3 text-right">Ann. return</th>
            <th class="py-2 px-3 text-right">Sharpe</th>
            <th class="py-2 px-3 text-right">Sortino</th>
            <th class="py-2 px-3 text-right">Calmar</th>
            <th class="py-2 px-3 text-right">Max DD</th>
            <th class="py-2 px-3 text-right">CVaR 95</th>
            <th class="py-2 px-3 text-right" title="Probabilistic Sharpe: P(true SR > 0)">PSR</th>
            <th class="py-2 px-3 text-right" title="Deflated Sharpe: corrected for trying multiple strategies">DSR</th>
            <th class="py-2 px-3 text-right">Turnover</th>
          </tr>
        </thead>
        <tbody>
          {#each result.results as r (r.key)}
            <tr class="border-b border-slate-100 {r.key === bestKey ? 'bg-emerald-50/40' : 'hover:bg-slate-50'}">
              <td class="py-2 px-3">
                <div class="font-medium text-slate-800">{r.key}</div>
                <div class="text-xs text-slate-400">{r.label}</div>
              </td>
              <td class="py-2 px-3 text-right font-mono">{fmtPct((r.metrics.ann_return ?? 0) * 100, 1)}</td>
              <td class="py-2 px-3 text-right font-mono">{num(r.metrics.sharpe)}</td>
              <td class="py-2 px-3 text-right font-mono">{num(r.metrics.sortino)}</td>
              <td class="py-2 px-3 text-right font-mono">{num(r.metrics.calmar)}</td>
              <td class="py-2 px-3 text-right font-mono text-red-600">{fmtPct((r.metrics.max_drawdown ?? 0) * 100, 1)}</td>
              <td class="py-2 px-3 text-right font-mono">{fmtPct((r.metrics.cvar_95 ?? 0) * 100, 2)}</td>
              <td class="py-2 px-3 text-right font-mono">{num(r.psr)}</td>
              <td class="py-2 px-3 text-right font-mono {dsrClass(r.dsr)}">{num(r.dsr)}</td>
              <td class="py-2 px-3 text-right font-mono text-slate-500">{num(r.turnover, 2)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>

    <p class="text-xs text-slate-500">
      <strong>DSR</strong> (Deflated Sharpe) is the number to trust: it corrects the
      Sharpe for both non-normal returns and the fact that you compared
      {result.n_trials} strateg{result.n_trials === 1 ? 'y' : 'ies'} — a green DSR ≥ 0.95
      means the result is unlikely to be luck. Walk-forward: each rebalance uses only
      data available before that date. Costs: {result.cost_bps} bps per unit of turnover.
    </p>
  {:else if !loading}
    <div class="bg-slate-50 border border-slate-200 rounded-lg p-6 text-sm text-slate-500">
      Pick strategies and a rebalance cadence, then <strong>Run backtest</strong> to
      compare them on your own holdings' price history.
    </div>
  {/if}
</div>
