<script lang="ts">
  /**
   * Settings page: swap the active market-data adapter, manage the Alpha
   * Vantage API key (write-only, never echoed back), inspect the AV daily
   * quota, and clear the in-memory price cache.
   */
  import { onMount } from 'svelte';
  import { api, apiGet } from '../lib/api';
  import { adapterStore, type AvQuota } from '../lib/stores';
  import { fmtDate } from '../lib/format';

  type SettingsPayload = {
    adapter: string;
    base_currency: string;
    av_quota: AvQuota;
    av_api_key_set: boolean;
  };

  let snap = $state<SettingsPayload | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // Form state.
  let selectedAdapter = $state<'yfinance' | 'alphavantage'>('yfinance');
  let apiKeyInput = $state('');
  let clearKeyOnSave = $state(false);

  // Action state.
  let saving = $state(false);
  let clearingCache = $state(false);
  let toast = $state<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  async function refresh() {
    loading = true;
    error = null;
    try {
      const s = (await apiGet('/api/settings')) as SettingsPayload;
      snap = s;
      selectedAdapter = (s.adapter as any) ?? 'yfinance';
      adapterStore.set({ name: s.adapter, av_quota: s.av_quota });
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  onMount(refresh);

  function flash(kind: 'ok' | 'err', msg: string) {
    toast = { kind, msg };
    setTimeout(() => {
      toast = null;
    }, 3500);
  }

  async function save() {
    saving = true;
    error = null;
    try {
      const body: Record<string, any> = { adapter: selectedAdapter };
      if (clearKeyOnSave) body.av_api_key = '';
      else if (apiKeyInput.trim()) body.av_api_key = apiKeyInput.trim();
      const { data, error: e } = await api.PUT('/api/settings', { body: body as any });
      if (e) throw new Error((e as any).detail ?? 'Save failed');
      apiKeyInput = '';
      clearKeyOnSave = false;
      await refresh();
      flash('ok', 'Settings updated.');
    } catch (err: any) {
      flash('err', err?.message ?? String(err));
    } finally {
      saving = false;
    }
  }

  async function clearCache() {
    clearingCache = true;
    try {
      const { error: e } = await api.POST('/api/cache/clear', {});
      if (e) throw new Error((e as any).detail ?? 'Clear failed');
      flash('ok', 'Adapter + view caches cleared.');
    } catch (err: any) {
      flash('err', err?.message ?? String(err));
    } finally {
      clearingCache = false;
    }
  }

  function quotaBadgeClass(used: number, limit: number) {
    if (used >= limit) return 'bg-red-100 text-red-700 border-red-200';
    if (used >= limit * 0.85) return 'bg-amber-100 text-amber-700 border-amber-200';
    if (used >= limit * 0.6) return 'bg-yellow-100 text-yellow-700 border-yellow-200';
    return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  }
</script>

<div class="space-y-5 max-w-2xl">
  <header>
    <h1 class="text-2xl font-semibold text-slate-900">Settings</h1>
    <p class="text-sm text-slate-500">
      Market-data adapter, Alpha Vantage API key, and cache controls.
    </p>
  </header>

  {#if loading && !snap}
    <p class="text-sm text-slate-500">Loading…</p>
  {:else if error}
    <div class="bg-red-50 border border-red-200 text-red-700 rounded p-3 text-sm">
      {error}
    </div>
  {:else if snap}
    <!-- Current adapter status -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-3">
      <h2 class="text-sm font-semibold text-slate-700">Market data adapter</h2>
      <dl class="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-xs text-slate-500 uppercase">Active</dt>
          <dd class="font-mono">{snap.adapter}</dd>
        </div>
        <div>
          <dt class="text-xs text-slate-500 uppercase">Base currency</dt>
          <dd class="font-mono">{snap.base_currency}</dd>
        </div>
        <div>
          <dt class="text-xs text-slate-500 uppercase">AV quota today</dt>
          <dd>
            <span
              class="inline-block px-2 py-0.5 rounded border text-xs font-mono {quotaBadgeClass(
                snap.av_quota.used ?? 0,
                snap.av_quota.limit ?? 25,
              )}"
            >
              {snap.av_quota.used ?? 0}/{snap.av_quota.limit ?? 25}
            </span>
          </dd>
        </div>
        <div>
          <dt class="text-xs text-slate-500 uppercase">Last AV call</dt>
          <dd class="text-xs font-mono text-slate-600">
            {fmtDate(snap.av_quota.last_call_at)}
          </dd>
        </div>
      </dl>
    </section>

    <!-- Update form -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-4">
      <h2 class="text-sm font-semibold text-slate-700">Switch adapter / update key</h2>

      <label class="block text-sm">
        <span class="block text-xs font-medium text-slate-700 mb-1">Adapter</span>
        <select
          bind:value={selectedAdapter}
          class="px-3 py-1.5 border border-slate-300 rounded-md bg-white w-full"
        >
          <option value="yfinance">yfinance (no key, no daily cap)</option>
          <option value="alphavantage">alphavantage (free tier ~25/day)</option>
        </select>
        {#if selectedAdapter === 'alphavantage' && !snap.av_api_key_set}
          <span class="text-xs text-amber-700 block mt-1">
            ⚠ No AV API key on file — save one below before applying.
          </span>
        {/if}
      </label>

      <label class="block text-sm">
        <span class="block text-xs font-medium text-slate-700 mb-1">
          Alpha Vantage API key
          {#if snap.av_api_key_set}
            <span class="text-emerald-600">(currently set)</span>
          {:else}
            <span class="text-slate-500">(not set)</span>
          {/if}
        </span>
        <input
          type="password"
          bind:value={apiKeyInput}
          placeholder={snap.av_api_key_set
            ? 'leave blank to keep current key'
            : 'paste your AV API key'}
          disabled={clearKeyOnSave}
          class="w-full px-3 py-1.5 border border-slate-300 rounded-md font-mono disabled:bg-slate-100"
        />
        {#if snap.av_api_key_set}
          <label class="inline-flex items-center gap-2 text-xs text-slate-600 mt-2">
            <input type="checkbox" bind:checked={clearKeyOnSave} />
            Clear stored key on save
          </label>
        {/if}
      </label>

      <div class="flex gap-2 pt-2">
        <button
          type="button"
          onclick={save}
          disabled={saving}
          class="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-slate-300"
        >{saving ? 'Saving…' : 'Apply'}</button>
        <button
          type="button"
          onclick={refresh}
          disabled={loading}
          class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
        >Reload</button>
      </div>
    </section>

    <!-- Cache controls -->
    <section class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-3">
      <h2 class="text-sm font-semibold text-slate-700">Caches</h2>
      <p class="text-xs text-slate-500">
        Dashboard, diversification, and watchlist results are cached for 5-10 min
        keyed by adapter + transaction revision. The yfinance adapter also
        memo-ises price history in-process. Clear both when prices look stale or
        right after switching adapters.
      </p>
      <button
        type="button"
        onclick={clearCache}
        disabled={clearingCache}
        class="px-3 py-1.5 text-sm border border-slate-300 rounded-md hover:bg-slate-50 disabled:bg-slate-100"
      >{clearingCache ? 'Clearing…' : 'Clear adapter + view caches'}</button>
    </section>
  {/if}

  {#if toast}
    <div
      class="fixed bottom-6 right-6 px-4 py-2 rounded-md shadow-lg text-sm border {toast.kind ===
      'ok'
        ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
        : 'bg-red-50 text-red-700 border-red-200'}"
    >
      {toast.msg}
    </div>
  {/if}
</div>
