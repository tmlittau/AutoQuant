<script lang="ts">
  import { link, router } from 'svelte-spa-router';
  import type { Snippet } from 'svelte';
  import GlobalSearch from './GlobalSearch.svelte';
  import AdapterChip from './AdapterChip.svelte';
  import { authStore, logout } from '../lib/auth';

  type Props = { children: Snippet };
  let { children }: Props = $props();

  type NavItem = { href: string; label: string; match: RegExp };
  const nav: NavItem[] = [
    { href: '/', label: 'Dashboard', match: /^\/$/ },
    { href: '/portfolio/stocks', label: 'Stocks', match: /^\/portfolio\/stocks/ },
    { href: '/portfolio/etfs', label: 'ETFs', match: /^\/portfolio\/etfs/ },
    {
      href: '/portfolio/diversification',
      label: 'Diversification',
      match: /^\/portfolio\/diversification/,
    },
    { href: '/watchlist', label: 'Watchlist', match: /^\/watchlist/ },
    { href: '/transactions', label: 'Transactions', match: /^\/transactions/ },
    { href: '/settings', label: 'Settings', match: /^\/settings/ },
  ];

  const isActive = (item: NavItem) => item.match.test(router.location);
</script>

<div class="min-h-screen flex flex-col">
  <header class="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
      <a use:link href="/" class="font-semibold text-slate-900 tracking-tight whitespace-nowrap">
        AutoQuant
      </a>
      <nav class="hidden md:flex items-center gap-1 text-sm">
        {#each nav as item (item.href)}
          <a
            use:link
            href={item.href}
            class="px-3 py-1.5 rounded-md transition whitespace-nowrap {isActive(item)
              ? 'bg-slate-100 text-slate-900 font-medium'
              : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'}"
          >
            {item.label}
          </a>
        {/each}
      </nav>
      <div class="flex-1 flex justify-end">
        <GlobalSearch />
      </div>
      <AdapterChip />
      {#if $authStore.user}
        <div class="flex items-center gap-2 text-xs">
          <span class="text-slate-500">{$authStore.user}</span>
          <button
            type="button"
            onclick={logout}
            class="px-2 py-1 rounded border border-slate-200 text-slate-600 hover:text-slate-900 hover:bg-slate-50"
            title="Sign out"
          >Logout</button>
        </div>
      {/if}
    </div>
  </header>

  <main class="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
    {@render children()}
  </main>

  <footer class="border-t border-slate-200 text-xs text-slate-500 py-3">
    <div class="max-w-7xl mx-auto px-4 flex items-center justify-between">
      <span>AutoQuant · single-user · base EUR</span>
      <span class="font-mono">v0.1.0</span>
    </div>
  </footer>
</div>
