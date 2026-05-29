<script lang="ts">
  /**
   * Top-level "Refresh prices" affordance. POSTs /api/cache/clear (which
   * wipes Django's view cache + the adapter's in-memory price cache), then
   * bumps the ``pricesRevision`` store so every mounted view refetches.
   *
   * Two visual variants:
   *   - ``compact`` (default): icon button + small "Updated HH:MM" badge,
   *     designed for the desktop top bar in Layout.svelte.
   *   - ``menu``: full-width menu row with label + timestamp underneath,
   *     designed for the mobile NavDrawer footer.
   *
   * Pass ``onAfterClick`` from a parent that needs to react to the tap
   * (e.g. the drawer closes itself after a successful refresh).
   */
  import { api } from '../lib/api';
  import { pricesRevision, lastRefreshedAt } from '../lib/stores';

  type Props = {
    variant?: 'compact' | 'menu';
    onAfterClick?: () => void;
  };
  let { variant = 'compact', onAfterClick }: Props = $props();

  let refreshing = $state(false);
  let error = $state<string | null>(null);

  function fmtTime(iso: string | null): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    error = null;
    try {
      const { data, error: e } = await api.POST('/api/cache/clear', {} as any);
      if (e) throw new Error((e as any).detail ?? 'refresh failed');
      lastRefreshedAt.set((data as any)?.at ?? new Date().toISOString());
      pricesRevision.update((n) => n + 1);
      onAfterClick?.();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      refreshing = false;
    }
  }
</script>

{#if variant === 'compact'}
  <div class="flex items-center gap-2">
    <button
      type="button"
      onclick={refresh}
      disabled={refreshing}
      aria-label="Refresh prices"
      title={error ?? 'Refetch the latest prices from the active adapter'}
      class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-slate-200 text-slate-600 hover:text-slate-900 hover:bg-slate-50 disabled:opacity-60 text-xs whitespace-nowrap"
    >
      <!-- Circular arrow -->
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-linejoin="round"
        class={refreshing ? 'animate-spin' : ''}
        aria-hidden="true"
      >
        <polyline points="23 4 23 10 17 10"></polyline>
        <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"></path>
      </svg>
      <span>{refreshing ? '…' : 'Refresh'}</span>
    </button>
    {#if $lastRefreshedAt}
      <span class="text-[10px] text-slate-400 font-mono" title={$lastRefreshedAt}>
        {fmtTime($lastRefreshedAt)}
      </span>
    {/if}
  </div>
{:else}
  <button
    type="button"
    onclick={refresh}
    disabled={refreshing}
    class="w-full text-left px-4 py-3 min-h-[44px] flex items-center gap-3 rounded-md border border-slate-200 bg-white text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60"
  >
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      class={refreshing ? 'animate-spin' : ''}
      aria-hidden="true"
    >
      <polyline points="23 4 23 10 17 10"></polyline>
      <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"></path>
    </svg>
    <span class="flex-1">
      <span class="block font-medium">
        {refreshing ? 'Refreshing prices…' : 'Refresh prices'}
      </span>
      {#if $lastRefreshedAt && !refreshing}
        <span class="block text-xs text-slate-500 font-mono">
          Updated {fmtTime($lastRefreshedAt)}
        </span>
      {:else if !$lastRefreshedAt && !refreshing}
        <span class="block text-xs text-slate-400">Pulls the latest quotes</span>
      {/if}
    </span>
  </button>
  {#if error}
    <p class="mt-1 text-xs text-red-600 px-1">{error}</p>
  {/if}
{/if}
