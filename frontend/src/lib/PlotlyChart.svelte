<script lang="ts">
  /**
   * Plotly wrapper for Svelte 5 (runes).
   *
   * Uses `Plotly.react` for efficient updates when `data`/`layout`/`config`
   * change (no DOM teardown between renders). `Plotly.purge` is called once on
   * component unmount.
   */
  import { onDestroy } from 'svelte';
  import Plotly from 'plotly.js-dist-min';

  type Props = {
    data: any[];
    layout?: Partial<Plotly.Layout>;
    config?: Partial<Plotly.Config>;
    style?: string;
    className?: string;
  };

  let { data, layout, config, style = '', className = '' }: Props = $props();
  let el = $state<HTMLDivElement | undefined>(undefined);

  const defaultConfig: Partial<Plotly.Config> = {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'] as any,
  };

  const defaultLayout: Partial<Plotly.Layout> = {
    autosize: true,
    margin: { t: 24, r: 16, b: 36, l: 56 },
    font: { family: 'ui-sans-serif, system-ui, sans-serif', size: 12 },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
  };

  $effect(() => {
    if (!el) return;
    Plotly.react(
      el,
      data,
      { ...defaultLayout, ...(layout ?? {}) },
      { ...defaultConfig, ...(config ?? {}) },
    );
  });

  onDestroy(() => {
    if (el) Plotly.purge(el);
  });
</script>

<div bind:this={el} class={`w-full ${className}`} {style}></div>
