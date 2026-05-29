<script lang="ts">
  import { onMount } from 'svelte';
  import Router from 'svelte-spa-router';

  import { authStore, ensureCsrf, fetchMe } from './lib/auth';
  import Layout from './components/Layout.svelte';
  import Login from './routes/Login.svelte';
  import Dashboard from './routes/Dashboard.svelte';
  import Portfolio from './routes/Portfolio.svelte';
  import Watchlist from './routes/Watchlist.svelte';
  import Diversification from './routes/Diversification.svelte';
  import Stock from './routes/Stock.svelte';
  import Transactions from './routes/Transactions.svelte';
  import Settings from './routes/Settings.svelte';
  import NotFound from './routes/NotFound.svelte';

  const routes = {
    '/': Dashboard,
    '/portfolio': Portfolio,
    '/portfolio/stocks': Portfolio,
    '/portfolio/etfs': Portfolio,
    '/portfolio/crypto': Portfolio,
    '/portfolio/diversification': Diversification,
    '/watchlist': Watchlist,
    '/transactions': Transactions,
    '/stock/:ticker': Stock,
    '/settings': Settings,
    '*': NotFound,
  };

  // Prime CSRF cookie and probe auth status on boot.
  onMount(async () => {
    await ensureCsrf();
    await fetchMe();
  });
</script>

{#if !$authStore.checked}
  <div class="min-h-screen flex items-center justify-center text-sm text-slate-500">
    Loading…
  </div>
{:else if !$authStore.user}
  <Login />
{:else}
  <Layout>
    <Router {routes} />
  </Layout>
{/if}
