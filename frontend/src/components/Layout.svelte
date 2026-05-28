<script lang="ts">
  import { link, router } from 'svelte-spa-router';
  import type { Snippet } from 'svelte';
  import GlobalSearch from './GlobalSearch.svelte';
  import AdapterChip from './AdapterChip.svelte';
  import NavDrawer from './NavDrawer.svelte';
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

  // Mobile-only UI state.
  let drawerOpen = $state(false);
  let mobileSearchOpen = $state(false);
</script>

<div class="min-h-screen flex flex-col">
  <header class="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 h-14 flex items-center gap-3 md:gap-6">
      <!-- Mobile: hamburger (hidden at md+) -->
      <button
        type="button"
        onclick={() => (drawerOpen = true)}
        aria-label="Open navigation"
        class="md:hidden inline-flex items-center justify-center min-h-[44px] min-w-[44px] -ml-2 text-slate-600 hover:text-slate-900"
      >
        <!-- Three-line hamburger icon -->
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="3" y1="6" x2="21" y2="6"></line>
          <line x1="3" y1="12" x2="21" y2="12"></line>
          <line x1="3" y1="18" x2="21" y2="18"></line>
        </svg>
      </button>

      <a use:link href="/" class="font-semibold text-slate-900 tracking-tight whitespace-nowrap">
        AutoQuant
      </a>

      <!-- Desktop nav (hidden at <md, lives in NavDrawer there) -->
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

      <!-- Desktop search: inline. Mobile search: icon button that drops a
           full-width search bar below the header. -->
      <div class="flex-1 flex justify-end items-center gap-2">
        <div class="hidden md:flex flex-1 justify-end">
          <GlobalSearch />
        </div>
        <button
          type="button"
          onclick={() => (mobileSearchOpen = !mobileSearchOpen)}
          aria-label="Toggle search"
          aria-pressed={mobileSearchOpen}
          class="md:hidden inline-flex items-center justify-center min-h-[44px] min-w-[44px] text-slate-600 hover:text-slate-900"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
      </div>

      <!-- Adapter chip + logout pinned at md+; hidden on mobile (lives in
           the drawer footer there). -->
      <div class="hidden md:flex items-center gap-3">
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
    </div>

    <!-- Mobile search bar (drops below header when toggled). Reuses
         GlobalSearch which handles debounced fetch + dropdown internally. -->
    {#if mobileSearchOpen}
      <div class="md:hidden border-t border-slate-200 bg-white px-4 py-2">
        <GlobalSearch />
      </div>
    {/if}
  </header>

  <NavDrawer open={drawerOpen} onClose={() => (drawerOpen = false)} {nav} />

  <main class="flex-1 max-w-7xl w-full mx-auto px-4 py-4 md:py-6">
    {@render children()}
  </main>

  <footer class="border-t border-slate-200 text-xs text-slate-500 py-3">
    <div class="max-w-7xl mx-auto px-4 flex items-center justify-between">
      <span>AutoQuant · single-user · base EUR</span>
      <span class="font-mono">v0.1.0</span>
    </div>
  </footer>
</div>
