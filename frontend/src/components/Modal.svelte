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
    class="fixed inset-0 bg-black/40 flex items-start justify-center z-50 p-4 overflow-y-auto"
    onclick={onClose}
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    onkeydown={(e) => e.stopPropagation()}
  >
    <div
      bind:this={dialog}
      class="bg-white rounded-xl shadow-xl w-full {sizeClass} mt-16"
      onclick={(e) => e.stopPropagation()}
      role="document"
      tabindex="-1"
      onkeydown={(e) => e.stopPropagation()}
    >
      {#if title}
        <header
          class="px-5 py-4 border-b border-slate-200 flex items-center justify-between"
        >
          <h2 class="text-lg font-semibold text-slate-900">{title}</h2>
          <button
            type="button"
            onclick={onClose}
            aria-label="Close"
            class="text-slate-400 hover:text-slate-600 text-2xl leading-none"
          >×</button>
        </header>
      {/if}
      <div class="px-5 py-4">
        {@render children()}
      </div>
      {#if footer}
        <footer
          class="px-5 py-3 border-t border-slate-200 flex justify-end gap-2 bg-slate-50 rounded-b-xl"
        >
          {@render footer()}
        </footer>
      {/if}
    </div>
  </div>
{/if}
