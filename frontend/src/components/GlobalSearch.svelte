<script lang="ts">
  /**
   * Debounced global stock search.
   *
   * Each keystroke (>= 2 chars, 300 ms debounce) hits ``/api/instruments/search``,
   * which is backed by the active adapter's symbol-search endpoint -- yfinance
   * Search by default, Alpha Vantage SYMBOL_SEARCH when that adapter is active.
   * The dropdown lets the user discover *any* ticker, not just ones already
   * owned or watched; clicking a hit navigates to ``/stock/:ticker``.
   */
  import { push } from 'svelte-spa-router';
  import { apiGet } from '../lib/api';

  type Hit = {
    symbol: string;
    name?: string | null;
    type?: string | null;
    region?: string | null;
    currency?: string | null;
  };

  let query = $state('');
  let results = $state<Hit[]>([]);
  let open = $state(false);
  let loading = $state(false);
  let timer: ReturnType<typeof setTimeout> | null = null;

  function reset() {
    query = '';
    results = [];
    open = false;
  }

  async function runSearch(q: string) {
    if (q.length < 2) {
      results = [];
      return;
    }
    loading = true;
    try {
      // Global nav search is stocks-only by design -- ETF discovery happens
      // inside the Add-Stock modal once the user has picked the ETFs sleeve.
      const hits = await apiGet('/api/instruments/search', {
        params: { query: { q, type: 'stocks' } },
      });
      results = (hits as Hit[]) ?? [];
    } catch {
      results = [];
    } finally {
      loading = false;
    }
  }

  function onInput(e: Event) {
    const target = e.target as HTMLInputElement;
    query = target.value;
    open = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => runSearch(query), 300);
  }

  function pick(symbol: string) {
    reset();
    push(`/stock/${encodeURIComponent(symbol)}`);
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault();
      pick(results[0].symbol);
    } else if (e.key === 'Escape') {
      reset();
    }
  }

  function onBlur() {
    // Close after a small delay so mousedown on a result fires first.
    setTimeout(() => {
      open = false;
    }, 150);
  }
</script>

<div class="relative w-full max-w-md">
  <input
    type="text"
    placeholder="Search any stock (e.g. ASML, Berkshire) …"
    value={query}
    oninput={onInput}
    onkeydown={onKeydown}
    onfocus={() => {
      open = true;
    }}
    onblur={onBlur}
    class="w-full px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-400"
  />
  {#if open && (loading || results.length > 0)}
    <div
      class="absolute mt-1 w-full bg-white border border-slate-200 rounded-md shadow-lg max-h-72 overflow-y-auto z-50"
    >
      {#if loading}
        <div class="px-3 py-2 text-xs text-slate-500">Searching…</div>
      {/if}
      {#each results as r (r.symbol)}
        <button
          type="button"
          onmousedown={() => pick(r.symbol)}
          class="w-full text-left px-3 py-2 hover:bg-slate-50 border-b border-slate-100 last:border-b-0"
        >
          <div class="text-sm font-medium text-slate-900">{r.symbol}</div>
          <div class="text-xs text-slate-500">
            {r.name ?? ''}
            {#if r.region}· {r.region}{/if}
            {#if r.currency}· {r.currency}{/if}
          </div>
        </button>
      {/each}
    </div>
  {/if}
</div>
