<script lang="ts">
  type Props = {
    label: string;
    value: string;
    delta?: string;
    /** ``true`` -> green, ``false`` -> red, ``null`` -> muted. */
    deltaPositive?: boolean | null;
    accent?: string;
  };

  let {
    label,
    value,
    delta,
    deltaPositive = null,
    accent = 'text-slate-900',
  }: Props = $props();
</script>

<div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
  <div class="text-xs uppercase tracking-wide text-slate-500">{label}</div>
  <div class="mt-1 text-2xl font-semibold {accent}">{value}</div>
  {#if delta != null}
    <div
      class="mt-1 text-xs font-medium {deltaPositive == null
        ? 'text-slate-500'
        : deltaPositive
          ? 'text-emerald-600'
          : 'text-red-600'}"
    >
      {delta}
    </div>
  {/if}
</div>
