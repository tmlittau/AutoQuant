<script lang="ts">
  /**
   * Mobile-only slide-in navigation drawer. Mounted by Layout.svelte and
   * shown when the user taps the hamburger icon (visible only at <md).
   *
   * Mirrors the desktop top-nav routes plus the user / logout footer, with
   * touch-friendly tap targets (>= 44 px). Closes on:
   *   - tapping the backdrop
   *   - pressing Escape
   *   - clicking any nav link (so navigation feels snappy)
   *   - tapping Logout
   */
  import { fly, fade } from 'svelte/transition';
  import { link, router } from 'svelte-spa-router';
  import AdapterChip from './AdapterChip.svelte';
  import RefreshButton from './RefreshButton.svelte';
  import { authStore, logout } from '../lib/auth';

  type NavItem = { href: string; label: string; match: RegExp };

  type Props = {
    open: boolean;
    onClose: () => void;
    nav: NavItem[];
  };

  let { open, onClose, nav }: Props = $props();

  const isActive = (item: NavItem) => item.match.test(router.location);

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }

  // Close the drawer when navigation happens. Tapping a use:link anchor
  // fires the SPA router which updates `router.location` -- we just listen
  // for any click inside the drawer (event bubbles) and close after a
  // microtask so the router has time to apply.
  function maybeCloseOnLinkClick(e: MouseEvent) {
    const a = (e.target as HTMLElement | null)?.closest('a');
    if (a && open) {
      // Defer so use:link's navigation handler runs first.
      queueMicrotask(onClose);
    }
  }

  async function onLogoutClick() {
    onClose();
    await logout();
  }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <!-- Backdrop -->
  <div
    transition:fade={{ duration: 150 }}
    class="fixed inset-0 bg-black/40 z-40 md:hidden"
    onclick={onClose}
    role="presentation"
  ></div>

  <!-- Drawer panel. The onclick is a bubble-up listener that auto-closes
       the drawer after a user taps any nav link inside; the real
       keyboard / focus targets are the <a> elements within. We suppress
       the linter's non-interactive-element warnings here. -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <aside
    transition:fly={{ x: -320, duration: 200 }}
    class="fixed inset-y-0 left-0 w-[80vw] max-w-[320px] bg-white shadow-xl z-50 flex flex-col md:hidden"
    aria-label="Main navigation"
    onclick={maybeCloseOnLinkClick}
  >
    <header class="h-14 px-4 flex items-center justify-between border-b border-slate-200">
      <span class="font-semibold text-slate-900 tracking-tight">AutoQuant</span>
      <button
        type="button"
        onclick={onClose}
        aria-label="Close navigation"
        class="text-slate-400 hover:text-slate-600 text-2xl leading-none min-h-[44px] min-w-[44px] flex items-center justify-center"
      >×</button>
    </header>

    <nav class="flex-1 overflow-y-auto py-2">
      {#each nav as item (item.href)}
        <a
          use:link
          href={item.href}
          class="block px-4 py-3 text-base min-h-[44px] transition {isActive(item)
            ? 'bg-slate-100 text-slate-900 font-medium'
            : 'text-slate-700 hover:bg-slate-50'}"
        >
          {item.label}
        </a>
      {/each}
    </nav>

    <footer class="border-t border-slate-200 px-4 py-3 space-y-3">
      <RefreshButton variant="menu" onAfterClick={onClose} />
      <AdapterChip />
      {#if $authStore.user}
        <div class="flex items-center justify-between text-sm">
          <span class="text-slate-500 truncate">{$authStore.user}</span>
          <button
            type="button"
            onclick={onLogoutClick}
            class="px-3 py-2 min-h-[40px] rounded-md border border-slate-200 text-slate-600 hover:text-slate-900 hover:bg-slate-50 text-sm"
          >Logout</button>
        </div>
      {/if}
    </footer>
  </aside>
{/if}
