<script lang="ts">
  /**
   * Single-stock detail page. Reachable for any ticker -- owned, watched, or
   * discovered via the nav search -- so it doubles as the "explore the public
   * stock universe" view.
   *
   * Renders: latest quote header + Add-to-Portfolio / Add-to-Watchlist buttons,
   * price chart with SMA20/50 + Bollinger bands, RSI panel, MACD panel, and a
   * quant signal breakdown card (composite + sub-signals + BUY/HOLD/TRIM).
   */
  import { apiGet } from '../lib/api';
  import PlotlyChart from '../lib/PlotlyChart.svelte';
  import AddStockModal from '../components/AddStockModal.svelte';
  import { fmtPct, fmtNum, fmtDate, fmtLocal } from '../lib/format';

  type Props = { params?: { ticker?: string } };
  let { params }: Props = $props();
  let ticker = $derived((params?.ticker ?? '').toUpperCase());

  type Quote = {
    symbol: string;
    price?: number | null;
    open?: number | null;
    high?: number | null;
    low?: number | null;
    previous_close?: number | null;
    change?: number | null;
    change_percent?: number | null;
    volume?: number | null;
    latest_trading_day?: string | null;
  };
  type Score = {
    ticker: string;
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
  type Indicators = {
    symbol: string;
    dates: string[];
    columns: string[];
    data: (number | null)[][];
  };

  let quote = $state<Quote | null>(null);
  let score = $state<Score | null>(null);
  let indicators = $state<Indicators | null>(null);
  let searchHit = $state<{ name: string; currency: string } | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let range = $state<'3mo' | '6mo' | '1y'>('6mo');

  // Modal state.
  let modalOpen = $state(false);
  let modalInitial = $state<{
    kind: 'portfolio' | 'watchlist';
    ticker: string;
    name: string;
    currency: string;
  } | null>(null);

  function openAdd(kind: 'portfolio' | 'watchlist') {
    modalInitial = {
      kind,
      ticker,
      name: searchHit?.name ?? quote?.symbol ?? ticker,
      currency: searchHit?.currency ?? 'USD',
    };
    modalOpen = true;
  }

  async function loadAll(t: string, r: string) {
    loading = true;
    error = null;
    try {
      // Fetch quote + indicators + score in parallel. Also search for the
      // ticker to discover name + currency for the Add modal.
      const [q, ind, sc, hits] = await Promise.all([
        apiGet('/api/instruments/{ticker}/quote', { params: { path: { ticker: t } } }),
        apiGet('/api/instruments/{ticker}/indicators', {
          params: { path: { ticker: t }, query: { range: r } },
        }),
        apiGet('/api/instruments/{ticker}/score', { params: { path: { ticker: t } } }),
        apiGet('/api/instruments/search', { params: { query: { q: t } } }).catch(() => []),
      ]);
      quote = q as Quote;
      indicators = ind as Indicators;
      score = sc as Score;
      const exact = ((hits as any[]) ?? []).find(
        (h: any) => (h.symbol ?? '').toUpperCase() === t,
      );
      searchHit = exact
        ? { name: exact.name ?? t, currency: (exact.currency ?? 'USD').toUpperCase() }
        : null;
    } catch (e: any) {
      error = e?.message ?? String(e);
      quote = null;
      indicators = null;
      score = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    const t = ticker;
    const r = range;
    if (t) loadAll(t, r);
  });

  // -------------------------------------------------------------------------
  // Plot data derivations
  // -------------------------------------------------------------------------
  function col(ind: Indicators | null, name: string): (number | null)[] {
    if (!ind) return [];
    const idx = ind.columns.indexOf(name);
    if (idx < 0) return [];
    return ind.data.map((row) => row[idx]);
  }

  let priceChartData = $derived.by(() => {
    if (!indicators) return [];
    const dates = indicators.dates;
    const close = col(indicators, 'close');
    const sma20 = col(indicators, 'sma_20');
    const sma50 = col(indicators, 'sma_50');
    const sma200 = col(indicators, 'sma_200');
    const bbU = col(indicators, 'bb_upper');
    const bbL = col(indicators, 'bb_lower');
    // SMA 200 needs 200 points to be non-NaN. For a 3-month range we have ~63
    // -- the trace will be empty (Plotly skips null) but the legend entry
    // would still appear. Drop the trace entirely if it has no usable data.
    const sma200HasData = sma200.some((v) => v != null);
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: dates,
        y: bbU,
        name: 'Bollinger upper',
        line: { color: 'rgba(148,163,184,0.4)', width: 1 },
        showlegend: false,
        hoverinfo: 'skip',
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: dates,
        y: bbL,
        name: 'Bollinger (20, 2σ)',
        line: { color: 'rgba(148,163,184,0.4)', width: 1 },
        fill: 'tonexty',
        fillcolor: 'rgba(148,163,184,0.12)',
        hoverinfo: 'skip',
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: dates,
        y: close,
        name: 'Close',
        line: { color: '#0f172a', width: 2 },
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: dates,
        y: sma20,
        name: 'SMA 20',
        line: { color: '#2563eb', width: 1.4 },
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: dates,
        y: sma50,
        name: 'SMA 50',
        line: { color: '#ea580c', width: 1.4 },
      },
      ...(sma200HasData
        ? [
            {
              type: 'scatter',
              mode: 'lines',
              x: dates,
              y: sma200,
              name: 'SMA 200',
              line: { color: '#16a34a', width: 1.4, dash: 'dot' },
            },
          ]
        : []),
    ];
  });

  // Drawdown from rolling peak (computed client-side from the close column).
  let drawdownData = $derived.by(() => {
    if (!indicators) return [];
    const close = col(indicators, 'close');
    const out: (number | null)[] = [];
    let peak = -Infinity;
    for (const v of close) {
      if (v == null) {
        out.push(null);
        continue;
      }
      if (v > peak) peak = v;
      out.push(peak > 0 ? (v / peak - 1) * 100 : 0);
    }
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: indicators.dates,
        y: out,
        fill: 'tozeroy',
        line: { color: '#dc2626', width: 1.2 },
        fillcolor: 'rgba(220, 38, 38, 0.15)',
        hovertemplate: '%{y:.2f}%<extra>drawdown</extra>',
        showlegend: false,
      },
    ];
  });
  const drawdownLayout = {
    height: 180,
    margin: { t: 16, r: 16, b: 30, l: 60 },
    yaxis: { title: { text: 'drawdown %' }, gridcolor: '#f1f5f9', ticksuffix: '%' },
    xaxis: { showgrid: false },
  };
  let priceChartLayout = $derived({
    height: 360,
    margin: { t: 16, r: 16, b: 36, l: 60 },
    yaxis: {
      title: { text: 'price (' + (searchHit?.currency ?? '') + ')' },
      gridcolor: '#f1f5f9',
    },
    xaxis: { showgrid: false },
    hovermode: 'x unified' as const,
    legend: { orientation: 'h', y: -0.18 },
  });

  let rsiChartData = $derived.by(() => {
    if (!indicators) return [];
    const rsi = col(indicators, 'rsi_14');
    return [
      {
        type: 'scatter',
        mode: 'lines',
        x: indicators.dates,
        y: rsi,
        name: 'RSI (14)',
        line: { color: '#7c3aed', width: 1.5 },
      },
    ];
  });
  let rsiChartLayout = $derived({
    height: 200,
    margin: { t: 16, r: 16, b: 30, l: 60 },
    yaxis: { range: [0, 100], title: { text: 'RSI' }, gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    shapes: [
      {
        type: 'line',
        x0: indicators?.dates?.[0],
        x1: indicators?.dates?.at(-1),
        y0: 70,
        y1: 70,
        line: { color: 'rgba(220, 38, 38, 0.4)', dash: 'dot', width: 1 },
      },
      {
        type: 'line',
        x0: indicators?.dates?.[0],
        x1: indicators?.dates?.at(-1),
        y0: 30,
        y1: 30,
        line: { color: 'rgba(22, 163, 74, 0.4)', dash: 'dot', width: 1 },
      },
    ],
    showlegend: false,
  });

  let macdChartData = $derived.by(() => {
    if (!indicators) return [];
    const macd = col(indicators, 'macd');
    const sig = col(indicators, 'macd_signal');
    const hist = col(indicators, 'macd_hist');
    return [
      {
        type: 'bar',
        x: indicators.dates,
        y: hist,
        name: 'histogram',
        marker: {
          color: hist.map((v) => ((v ?? 0) >= 0 ? '#86efac' : '#fca5a5')),
        },
        hovertemplate: '%{y:.3f}<extra>histogram</extra>',
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: indicators.dates,
        y: macd,
        name: 'MACD',
        line: { color: '#0f172a', width: 1.4 },
      },
      {
        type: 'scatter',
        mode: 'lines',
        x: indicators.dates,
        y: sig,
        name: 'signal',
        line: { color: '#ea580c', width: 1.2, dash: 'dash' },
      },
    ];
  });
  let macdChartLayout = $derived({
    height: 220,
    margin: { t: 16, r: 16, b: 30, l: 60 },
    yaxis: { gridcolor: '#f1f5f9' },
    xaxis: { showgrid: false },
    legend: { orientation: 'h', y: -0.22 },
  });

  function recoColor(label: string | undefined | null): string {
    if (label === 'BUY') return 'bg-emerald-100 text-emerald-700 border-emerald-200';
    if (label === 'TRIM' || label === 'AVOID')
      return 'bg-red-100 text-red-700 border-red-200';
    return 'bg-slate-100 text-slate-700 border-slate-200';
  }

  function signalColor(v: number | null | undefined): string {
    if (v == null) return 'text-slate-400';
    if (v >= 0.35) return 'text-emerald-600';
    if (v <= -0.35) return 'text-red-600';
    return 'text-slate-600';
  }
</script>

<div class="space-y-4">
  <!-- Header -->
  <header
    class="bg-white border border-slate-200 rounded-xl shadow-sm p-4 flex flex-wrap items-center gap-4"
  >
    <div class="flex-1 min-w-[10rem]">
      <h1 class="text-2xl font-semibold font-mono">{ticker}</h1>
      <p class="text-sm text-slate-600">
        {searchHit?.name ?? quote?.symbol ?? ticker}
        {#if searchHit?.currency}· {searchHit.currency}{/if}
      </p>
    </div>
    {#if quote}
      <div class="text-right">
        <div class="text-2xl font-semibold font-mono">
          {fmtLocal(quote.price, searchHit?.currency ?? 'USD')}
        </div>
        <div
          class="text-sm font-mono {(quote.change_percent ?? 0) >= 0
            ? 'text-emerald-600'
            : 'text-red-600'}"
        >
          {fmtPct(quote.change_percent)}
          {#if quote.change != null}<span class="text-slate-500 ml-2"
              >({quote.change >= 0 ? '+' : ''}{fmtNum(quote.change, 2)})</span
            >{/if}
          {#if quote.latest_trading_day}
            <span class="text-slate-400 ml-2">· {fmtDate(quote.latest_trading_day)}</span>
          {/if}
        </div>
      </div>
    {/if}
    <div class="flex flex-col gap-2">
      <button
        type="button"
        onclick={() => openAdd('portfolio')}
        class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >+ Add to Portfolio</button>
      <button
        type="button"
        onclick={() => openAdd('watchlist')}
        class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
      >+ Add to Watchlist</button>
    </div>
  </header>

  {#if loading && !indicators}
    <p class="text-sm text-slate-500">Loading {ticker}…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">
      Could not load <span class="font-mono">{ticker}</span>: {error}
    </div>
  {:else if indicators}
    <!-- Range toggle -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-slate-500">Range:</span>
      <div class="inline-flex border border-slate-200 rounded p-0.5 bg-slate-50">
        {#each ['3mo', '6mo', '1y'] as r}
          <button
            type="button"
            onclick={() => (range = r as any)}
            class="px-2.5 py-0.5 rounded text-xs uppercase {range === r
              ? 'bg-white shadow-sm text-slate-900 font-medium'
              : 'text-slate-600 hover:text-slate-900'}"
          >{r}</button>
        {/each}
      </div>
    </div>

    <!-- Score breakdown card -->
    {#if score}
      <section
        class="bg-white border border-slate-200 rounded-xl shadow-sm p-4 grid grid-cols-2 md:grid-cols-6 gap-4 text-sm"
      >
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">Signal</span>
          <span
            class="mt-1 inline-flex items-center px-3 py-1 rounded-md border text-base font-semibold w-fit {recoColor(score.signal)}"
          >
            {score.signal ?? '–'}
          </span>
        </div>
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">Composite</span>
          <span class="mt-1 text-xl font-mono font-semibold {signalColor(score.score)}">
            {fmtNum(score.score, 2)}
          </span>
        </div>
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">Trend</span>
          <span class="mt-1 text-lg font-mono {signalColor(score.trend)}">
            {fmtNum(score.trend, 2)}
          </span>
        </div>
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">Momentum (RSI)</span>
          <span class="mt-1 text-lg font-mono {signalColor(score.momentum)}">
            {fmtNum(score.momentum, 2)}
          </span>
          <span class="text-xs text-slate-500">RSI 14: {fmtNum(score.rsi_14, 1)}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">MACD</span>
          <span class="mt-1 text-lg font-mono {signalColor(score.macd)}">
            {fmtNum(score.macd, 2)}
          </span>
        </div>
        <div class="flex flex-col">
          <span class="text-xs uppercase text-slate-500 tracking-wide">Mean-reversion</span>
          <span class="mt-1 text-lg font-mono {signalColor(score.mean_reversion)}">
            {fmtNum(score.mean_reversion, 2)}
          </span>
          <span class="text-xs text-slate-500">z20: {fmtNum(score.zscore_20, 2)}</span>
        </div>
      </section>
    {/if}

    <!-- Price chart -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Price · SMA 20/50 · Bollinger bands
      </h2>
      <PlotlyChart data={priceChartData} layout={priceChartLayout} />
    </section>

    <!-- RSI -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">RSI (14)</h2>
      <PlotlyChart data={rsiChartData} layout={rsiChartLayout} />
    </section>

    <!-- MACD -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">MACD (12/26/9)</h2>
      <PlotlyChart data={macdChartData} layout={macdChartLayout} />
    </section>

    <!-- Drawdown -->
    <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
      <h2 class="text-sm font-semibold text-slate-700 mb-2">
        Drawdown from rolling peak
      </h2>
      <PlotlyChart data={drawdownData} layout={drawdownLayout} />
    </section>
  {/if}
</div>

<AddStockModal
  open={modalOpen}
  onClose={() => (modalOpen = false)}
  initial={modalInitial ?? undefined}
/>
