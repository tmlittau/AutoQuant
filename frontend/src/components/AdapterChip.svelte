<script lang="ts">
  import { onMount } from 'svelte';
  import { adapterStore, type AvQuota } from '../lib/stores';
  import { apiGet } from '../lib/api';

  async function load() {
    try {
      const s = await apiGet('/api/settings');
      adapterStore.set({ name: s.adapter, av_quota: s.av_quota as AvQuota });
    } catch {
      adapterStore.set(null);
    }
  }

  onMount(load);

  function quotaClasses(used: number, limit: number): string {
    if (used >= limit) return 'bg-red-100 text-red-700 border-red-200';
    if (used >= limit * 0.85) return 'bg-amber-100 text-amber-700 border-amber-200';
    if (used >= limit * 0.6) return 'bg-yellow-100 text-yellow-700 border-yellow-200';
    return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  }
</script>

{#if $adapterStore}
  {@const isAV = $adapterStore.name === 'alphavantage'}
  {@const used = $adapterStore.av_quota?.used ?? 0}
  {@const limit = $adapterStore.av_quota?.limit ?? 25}
  <div class="flex items-center gap-2 text-xs">
    <span class="px-2 py-1 rounded-md bg-slate-100 text-slate-700 border border-slate-200 font-mono">
      {$adapterStore.name}
    </span>
    {#if isAV}
      <span class="px-2 py-1 rounded-md border font-mono {quotaClasses(used, limit)}">
        AV {used}/{limit}
      </span>
    {/if}
  </div>
{/if}
