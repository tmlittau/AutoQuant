<script lang="ts">
  /**
   * Generic modal: dimmed backdrop, escape-to-close, click-outside to close.
   * Optional title and footer snippet. Body content is the default slot.
   *
   * Not a strict focus-trap (kept lightweight for a single-user app); we just
   * autofocus the first focusable input on mount.
   */
  import type { Snippet } from 'svelte';

  type Props = {
    open: boolean;
    title?: string;
    onClose: () => void;
    children: Snippet;
    footer?: Snippet;
    /** Tailwind max-width override, e.g. 'max-w-xl' */
    sizeClass?: string;
  };

  let { open, title, onClose, children, footer, sizeClass = 'max-w-lg' }: Props = $props();

  let dialog = $state<HTMLDivElement | undefined>(undefined);

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }

  $effect(() => {
    if (open && dialog) {
      // Move focus inside on open
      const first = dialog.querySelector<HTMLElement>(
        'input, select, textarea, button',
      );
      first?.focus();
    }
  });
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="fixed inset-0 bg-black/40 flex items-start justify-center z-50 p-0 sm:p-4 overflow-y-auto"
    onclick={onClose}
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    onkeydown={(e) => e.stopPropagation()}
  >
    <!--
      Mobile: near-fullscreen sheet (mt-2, no rounding) so dense forms have
      vertical room and the tap-outside area is just the top strip.
      Desktop: classic centered modal with rounded corners and mt-16.
    -->
    <div
      bind:this={dialog}
      class="bg-white sm:rounded-xl shadow-xl w-full {sizeClass} mt-2 sm:mt-16 mb-2 sm:mb-0 min-h-[calc(100vh-1rem)] sm:min-h-0 sm:max-h-[calc(100vh-5rem)] flex flex-col"
      onclick={(e) => e.stopPropagation()}
      role="document"
      tabindex="-1"
      onkeydown={(e) => e.stopPropagation()}
    >
      {#if title}
        <header
          class="px-4 sm:px-5 py-3 sm:py-4 border-b border-slate-200 flex items-center justify-between flex-shrink-0"
        >
          <h2 class="text-lg font-semibold text-slate-900">{title}</h2>
          <button
            type="button"
            onclick={onClose}
            aria-label="Close"
            class="text-slate-400 hover:text-slate-600 text-3xl leading-none min-h-[44px] min-w-[44px] flex items-center justify-center -mr-2"
          >×</button>
        </header>
      {/if}
      <div class="px-4 sm:px-5 py-4 flex-1 overflow-y-auto">
        {@render children()}
      </div>
      {#if footer}
        <footer
          class="px-4 sm:px-5 py-3 border-t border-slate-200 flex flex-col-reverse sm:flex-row justify-end gap-2 bg-slate-50 sm:rounded-b-xl flex-shrink-0 [&>button]:w-full sm:[&>button]:w-auto [&>button]:min-h-[44px]"
        >
          {@render footer()}
        </footer>
      {/if}
    </div>
  </div>
{/if}
