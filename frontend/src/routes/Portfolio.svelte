<script lang="ts">
  /**
   * Portfolio route wrapper. Reads ``router.location`` to decide between the
   * Stocks / ETFs / Crypto sub-tabs, then renders the shared <PortfolioView>
   * with the matching asset class.
   */
  import { link, router } from 'svelte-spa-router';
  import PortfolioView from '../components/PortfolioView.svelte';

  type AssetClass = 'stocks' | 'etfs' | 'crypto';

  let assetClass = $derived<AssetClass>(
    router.location.includes('/crypto')
      ? 'crypto'
      : router.location.includes('/etfs')
        ? 'etfs'
        : 'stocks',
  );

  type Tab = { href: string; label: string; key: AssetClass };
  const tabs: Tab[] = [
    { href: '/portfolio/stocks', label: 'Stocks', key: 'stocks' },
    { href: '/portfolio/etfs', label: 'ETFs', key: 'etfs' },
    { href: '/portfolio/crypto', label: 'Crypto', key: 'crypto' },
  ];

  // Short descriptive subtitle per sleeve.
  let subtitle = $derived(
    assetClass === 'stocks'
      ? 'Sector-grouped equities · target weights enforced'
      : assetClass === 'etfs'
        ? 'ETF sleeve · diversified by construction, no group targets'
        : 'Crypto wallet · priced in EUR · log deposits / withdrawals / swaps',
  );
</script>

<div class="space-y-5">
  <header class="flex flex-wrap items-baseline gap-4">
    <h1 class="text-2xl font-semibold text-slate-900">Portfolio</h1>
    <nav
      class="flex gap-1 text-sm rounded-md bg-slate-100 border border-slate-200 p-1"
    >
      {#each tabs as t (t.key)}
        <a
          use:link
          href={t.href}
          class="px-3 py-1 rounded transition {assetClass === t.key
            ? 'bg-white text-slate-900 font-medium shadow-sm'
            : 'text-slate-600 hover:text-slate-900'}"
        >
          {t.label}
        </a>
      {/each}
    </nav>
    <span class="text-xs text-slate-500 ml-auto">{subtitle}</span>
  </header>

  <PortfolioView {assetClass} />
</div>
