<script lang="ts">
  /**
   * Manage GroupConfig rows for the stocks sleeve: edit description + target
   * weight (as %), delete unused groups, add a new one. Backed by
   * GET/POST/PATCH/DELETE /api/groups.
   *
   * ETFs only ever have a single canonical "ETFs" group, so this modal is
   * only ever opened with assetClass='stocks'. The asset_class is kept as a
   * prop in case we ever want to expose ETF-group metadata too.
   */
  import { api, apiGet } from '../lib/api';
  import { transactionsRevision } from '../lib/stores';
  import Modal from './Modal.svelte';

  type AssetClass = 'stocks' | 'etfs';

  type Group = {
    asset_class: AssetClass;
    name: string;
    description: string;
    target_weight: number | null;     // fraction (0..1)
    holdings_count: number;
  };

  type Props = {
    open: boolean;
    onClose: () => void;
    assetClass?: AssetClass;
  };

  let { open, onClose, assetClass = 'stocks' }: Props = $props();

  let groups = $state<Group[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let savingKey = $state<string | null>(null);   // group name being saved (disables that row)

  // Per-row draft state (keyed by group name). Lets the user edit without
  // mutating the snapshot until they click Save.
  type Draft = { description: string; targetPct: number | null };
  let drafts = $state<Record<string, Draft>>({});

  // New group form
  let newName = $state('');
  let newDescription = $state('');
  let newTargetPct = $state<number | null>(null);
  let creating = $state(false);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await apiGet('/api/groups', {
        params: { query: { asset_class: assetClass } },
      });
      groups = (r as Group[]) ?? [];
      drafts = Object.fromEntries(
        groups.map((g) => [
          g.name,
          {
            description: g.description ?? '',
            targetPct:
              g.target_weight != null
                ? +(g.target_weight * 100).toFixed(4)
                : null,
          },
        ]),
      );
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (open) {
      newName = '';
      newDescription = '';
      newTargetPct = null;
      error = null;
      load();
    }
  });

  function rowChanged(g: Group) {
    const d = drafts[g.name];
    if (!d) return false;
    const origPct =
      g.target_weight != null ? +(g.target_weight * 100).toFixed(4) : null;
    return (
      (d.description ?? '') !== (g.description ?? '') || d.targetPct !== origPct
    );
  }

  async function saveRow(g: Group) {
    const d = drafts[g.name];
    if (!d) return;
    savingKey = g.name;
    error = null;
    try {
      const body: Record<string, any> = {
        description: d.description ?? '',
      };
      if (d.targetPct == null || Number.isNaN(d.targetPct)) {
        body.clear_target_weight = true;
      } else {
        body.target_weight = d.targetPct / 100;
      }
      const { data, error: e } = await api.PATCH(
        '/api/groups/{asset_class}/{name}' as any,
        {
          params: { path: { asset_class: g.asset_class, name: g.name } },
          body: body as any,
        } as any,
      );
      if (e) throw new Error((e as any).detail ?? 'Save failed');
      transactionsRevision.update((n) => n + 1);
      await load();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      savingKey = null;
    }
  }

  async function deleteRow(g: Group) {
    if (g.holdings_count > 0) {
      error = `'${g.name}' still has ${g.holdings_count} holding(s); reassign or delete those first.`;
      return;
    }
    if (!confirm(`Delete group '${g.name}'? This cannot be undone.`)) return;
    savingKey = g.name;
    error = null;
    try {
      const { error: e } = await api.DELETE(
        '/api/groups/{asset_class}/{name}' as any,
        {
          params: { path: { asset_class: g.asset_class, name: g.name } },
        } as any,
      );
      if (e) throw new Error((e as any).detail ?? 'Delete failed');
      transactionsRevision.update((n) => n + 1);
      await load();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      savingKey = null;
    }
  }

  async function createGroup() {
    const name = newName.trim();
    if (!name) {
      error = 'Group name is required';
      return;
    }
    creating = true;
    error = null;
    try {
      const body: Record<string, any> = {
        asset_class: assetClass,
        name,
        description: newDescription.trim(),
      };
      if (
        newTargetPct != null &&
        Number.isFinite(newTargetPct) &&
        assetClass === 'stocks'
      ) {
        body.target_weight = newTargetPct / 100;
      }
      const { data, error: e } = await api.POST('/api/groups', {
        body: body as any,
      });
      if (e) throw new Error((e as any).detail ?? 'Create failed');
      newName = '';
      newDescription = '';
      newTargetPct = null;
      transactionsRevision.update((n) => n + 1);
      await load();
    } catch (err: any) {
      error = err?.message ?? String(err);
    } finally {
      creating = false;
    }
  }

  // Live total of target weights (% summed) so the user can spot under/over-allocation.
  let totalTargetPct = $derived.by(() => {
    let t = 0;
    let any = false;
    for (const g of groups) {
      const d = drafts[g.name];
      const pct = d?.targetPct;
      if (pct != null && Number.isFinite(pct)) {
        t += pct;
        any = true;
      }
    }
    return any ? +t.toFixed(2) : null;
  });
</script>

<Modal {open} {onClose} title="Manage groups" sizeClass="max-w-3xl">
  <div class="space-y-4 text-sm">
    {#if loading}
      <p class="text-slate-500">Loading…</p>
    {:else if error}
      <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
        {error}
      </div>
    {/if}

    {#if !loading}
      <section class="overflow-x-auto border border-slate-200 rounded-lg">
        <table class="w-full text-sm">
          <thead>
            <tr
              class="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200 bg-slate-50"
            >
              <th class="py-2 px-3">Group</th>
              <th class="py-2 px-3">Description</th>
              <th class="py-2 px-3 text-right">Target %</th>
              <th class="py-2 px-3 text-right">Holdings</th>
              <th class="py-2 px-3"></th>
            </tr>
          </thead>
          <tbody>
            {#each groups as g (g.name)}
              <tr class="border-b border-slate-100 align-top">
                <td class="py-2 px-3 font-medium text-slate-800">
                  {g.name}
                </td>
                <td class="py-2 px-3">
                  <input
                    type="text"
                    bind:value={drafts[g.name].description}
                    disabled={savingKey === g.name}
                    placeholder="(no description)"
                    class="w-full px-2 py-1 border border-slate-300 rounded text-sm"
                  />
                </td>
                <td class="py-2 px-3 text-right">
                  {#if assetClass === 'stocks'}
                    <input
                      type="number"
                      bind:value={drafts[g.name].targetPct}
                      disabled={savingKey === g.name}
                      placeholder="—"
                      min="0"
                      max="100"
                      step="0.1"
                      class="w-24 px-2 py-1 border border-slate-300 rounded text-sm font-mono text-right"
                    />
                  {:else}
                    <span class="text-slate-400">n/a</span>
                  {/if}
                </td>
                <td class="py-2 px-3 text-right font-mono text-slate-500">
                  {g.holdings_count}
                </td>
                <td class="py-2 px-3 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onclick={() => saveRow(g)}
                    disabled={savingKey === g.name || !rowChanged(g)}
                    class="px-3 py-2 min-h-[36px] text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed"
                  >
                    {savingKey === g.name ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    type="button"
                    onclick={() => deleteRow(g)}
                    disabled={savingKey === g.name || g.holdings_count > 0}
                    title={g.holdings_count > 0
                      ? 'Reassign or delete this group\'s holdings first.'
                      : 'Delete group'}
                    class="ml-1 px-3 py-2 min-h-[36px] text-xs border border-red-200 text-red-600 rounded hover:bg-red-50 disabled:text-slate-300 disabled:border-slate-200 disabled:cursor-not-allowed"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            {/each}
            {#if groups.length === 0}
              <tr>
                <td
                  colspan="5"
                  class="py-6 text-center text-xs text-slate-500"
                >
                  No groups yet. Add one below.
                </td>
              </tr>
            {/if}
          </tbody>
          {#if assetClass === 'stocks' && totalTargetPct != null}
            <tfoot>
              <tr class="bg-slate-50 border-t border-slate-200">
                <td class="py-2 px-3 text-xs uppercase tracking-wide text-slate-500">
                  Total target
                </td>
                <td></td>
                <td
                  class="py-2 px-3 text-right font-mono {Math.abs(
                    totalTargetPct - 100,
                  ) < 0.01
                    ? 'text-emerald-600'
                    : 'text-amber-600'}"
                >
                  {totalTargetPct.toFixed(2)} %
                </td>
                <td colspan="2" class="py-2 px-3 text-xs text-slate-500">
                  {Math.abs(totalTargetPct - 100) < 0.01
                    ? 'sums to 100% ✓'
                    : `${(totalTargetPct - 100).toFixed(2)}% off`}
                </td>
              </tr>
            </tfoot>
          {/if}
        </table>
      </section>

      <!-- New group form -->
      <section class="border border-slate-200 rounded-lg p-3 bg-slate-50">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          Add new group
        </h3>
        <div class="grid grid-cols-1 sm:grid-cols-[1fr_2fr_8rem_auto] gap-2 items-end">
          <input
            type="text"
            bind:value={newName}
            placeholder="Name (e.g. Energy)"
            class="px-3 py-2 sm:py-1 border border-slate-300 rounded text-base sm:text-sm"
          />
          <input
            type="text"
            bind:value={newDescription}
            placeholder="Description (optional)"
            class="px-3 py-2 sm:py-1 border border-slate-300 rounded text-base sm:text-sm"
          />
          {#if assetClass === 'stocks'}
            <input
              type="number"
              inputmode="decimal"
              bind:value={newTargetPct}
              placeholder="Target %"
              min="0"
              max="100"
              step="0.1"
              class="px-3 py-2 sm:py-1 border border-slate-300 rounded text-base sm:text-sm font-mono text-right"
            />
          {:else}
            <span></span>
          {/if}
          <button
            type="button"
            onclick={createGroup}
            disabled={creating || !newName.trim()}
            class="px-4 py-2 min-h-[44px] text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {creating ? 'Adding…' : 'Add'}
          </button>
        </div>
      </section>
    {/if}
  </div>

  {#snippet footer()}
    <button
      type="button"
      onclick={onClose}
      class="px-4 py-2 text-sm bg-slate-100 text-slate-700 rounded-md hover:bg-slate-200"
    >Done</button>
  {/snippet}
</Modal>
