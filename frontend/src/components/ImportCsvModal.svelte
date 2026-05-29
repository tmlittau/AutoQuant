<script lang="ts">
  /**
   * Restore the transactions ledger from a CSV that matches the existing
   * Export-CSV format. Two modes:
   *
   * - Append (default): adds new rows; rows that already exist (matched by
   *   date + ticker + action + amount_eur + shares) are skipped. Safe to
   *   re-run with the same file -> no double-up.
   * - Replace: wipes every existing Transaction first, then inserts. Used
   *   after a schema migration when the user wants to restore a known-good
   *   ledger. Doesn't touch Holdings / GroupConfig.
   *
   * Holdings referenced by tickers the DB doesn't know yet are auto-created
   * (kind=portfolio, asset_class inferred from the CSV's group column).
   *
   * Multipart uploads bypass openapi-fetch, so we use a thin fetch() with
   * the existing csrftoken cookie helper.
   */
  import { transactionsRevision } from '../lib/stores';
  import { getCookie } from '../lib/auth';
  import Modal from './Modal.svelte';

  type Props = {
    open: boolean;
    onClose: () => void;
  };
  let { open, onClose }: Props = $props();

  type RowError = { row_index: number; message: string };
  type ImportResult = {
    mode: string;
    imported: number;
    skipped: number;
    errors: RowError[];
    holdings_created: string[];
    strict: boolean;
  };

  let file = $state<File | null>(null);
  let mode = $state<'append' | 'replace'>('append');
  let strict = $state(true);

  let submitting = $state(false);
  let serverError = $state<string | null>(null);
  let result = $state<ImportResult | null>(null);

  // Reset on open.
  $effect(() => {
    if (open) {
      file = null;
      mode = 'append';
      strict = true;
      submitting = false;
      serverError = null;
      result = null;
    }
  });

  function onFilePick(e: Event) {
    const t = e.target as HTMLInputElement;
    file = t.files && t.files.length ? t.files[0] : null;
    result = null;
    serverError = null;
  }

  async function submit() {
    if (!file) {
      serverError = 'Pick a CSV file first.';
      return;
    }
    submitting = true;
    serverError = null;
    result = null;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const qs = new URLSearchParams({
        mode,
        strict: strict ? 'true' : 'false',
      });
      const r = await fetch(`/api/transactions/import?${qs}`, {
        method: 'POST',
        credentials: 'include',
        body: fd,
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
      });
      // 200 = success (or strict=false with partial errors).
      // 422 = strict mode aborted because of bad rows (body is still
      //       ImportResultOut with errors[]).
      // 400 = header validation failed.
      if (r.status === 400) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail ?? 'CSV rejected by the server');
      }
      result = (await r.json()) as ImportResult;
      if (r.status === 200 && result.imported > 0) {
        // Bump the revision so the ledger view (and any open portfolio view)
        // refetches without the user having to reload.
        transactionsRevision.update((n) => n + 1);
      }
    } catch (err: any) {
      serverError = err?.message ?? String(err);
    } finally {
      submitting = false;
    }
  }

  let canSubmit = $derived(!submitting && !!file && !result);
</script>

<Modal {open} {onClose} title="Import transactions" sizeClass="max-w-2xl">
  <div class="space-y-5 text-sm">
    {#if result}
      <!-- ------------- Result panel ------------- -->
      <section class="space-y-3">
        <div
          class={result.errors.length === 0 && result.imported > 0
            ? 'bg-emerald-50 border border-emerald-200 rounded p-3 text-emerald-800'
            : result.imported === 0 && result.errors.length > 0
              ? 'bg-red-50 border border-red-200 rounded p-3 text-red-800'
              : 'bg-amber-50 border border-amber-200 rounded p-3 text-amber-800'}
        >
          <div class="font-medium">
            {result.imported === 0 && result.errors.length > 0
              ? 'Nothing imported.'
              : `Imported ${result.imported} row${result.imported === 1 ? '' : 's'} in ${result.mode} mode.`}
          </div>
          <div class="text-xs mt-1 font-mono">
            imported={result.imported} · skipped={result.skipped} ·
            errors={result.errors.length} ·
            holdings_created={result.holdings_created.length}
          </div>
        </div>

        {#if result.holdings_created.length > 0}
          <div class="text-xs text-slate-600">
            <span class="font-medium">Auto-created holdings:</span>
            <span class="font-mono">{result.holdings_created.join(', ')}</span>
            — rename them in Portfolio if you want a friendly name.
          </div>
        {/if}

        {#if result.errors.length > 0}
          <div class="border border-red-200 rounded">
            <div class="px-3 py-2 bg-red-50 text-xs font-semibold text-red-700 border-b border-red-200">
              {result.errors.length} row error{result.errors.length === 1 ? '' : 's'}
              {result.strict && result.imported === 0
                ? '(strict mode — nothing was committed)'
                : '(skipped, valid rows committed)'}
            </div>
            <ul class="max-h-60 overflow-y-auto divide-y divide-red-100 text-xs">
              {#each result.errors as e (e.row_index)}
                <li class="px-3 py-1.5">
                  <span class="font-mono text-red-700">row {e.row_index + 2}</span>
                  <span class="text-slate-600">: {e.message}</span>
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </section>
    {:else}
      <!-- ------------- File pick + options ------------- -->
      <label class="block">
        <span class="block text-xs font-medium text-slate-700 mb-1">
          CSV file (same shape as the Export download)
        </span>
        <input
          type="file"
          accept=".csv,text/csv"
          onchange={onFilePick}
          class="block w-full text-sm file:mr-3 file:px-3 file:py-2 file:min-h-[40px] file:rounded-md file:border-0 file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
        />
        {#if file}
          <p class="mt-1 text-xs text-slate-500 font-mono">
            {file.name} · {(file.size / 1024).toFixed(1)} KB
          </p>
        {/if}
      </label>

      <fieldset class="space-y-2">
        <legend class="block text-xs font-medium text-slate-700 mb-1">Mode</legend>
        <label class="flex gap-2 items-start cursor-pointer">
          <input
            type="radio"
            bind:group={mode}
            value="append"
            class="mt-0.5"
          />
          <span class="text-sm">
            <span class="font-medium">Append</span>
            <span class="block text-xs text-slate-500">
              Add new rows. Rows already present in the DB (same date / ticker /
              action / amount / shares) are skipped — safe to re-run.
            </span>
          </span>
        </label>
        <label class="flex gap-2 items-start cursor-pointer">
          <input
            type="radio"
            bind:group={mode}
            value="replace"
            class="mt-0.5"
          />
          <span class="text-sm">
            <span class="font-medium text-red-700">Replace</span>
            <span class="block text-xs text-slate-500">
              <strong class="text-red-600">Wipes every existing transaction</strong>
              before inserting. Holdings + Groups are kept. Use this to restore
              a known-good ledger after a migration.
            </span>
          </span>
        </label>
      </fieldset>

      <label class="flex gap-2 items-start cursor-pointer">
        <input type="checkbox" bind:checked={strict} class="mt-0.5" />
        <span class="text-sm">
          <span class="font-medium">Strict</span>
          <span class="block text-xs text-slate-500">
            Abort if any row has a validation error. Uncheck to commit valid
            rows and report the bad ones.
          </span>
        </span>
      </label>

      {#if serverError}
        <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
          {serverError}
        </div>
      {/if}
    {/if}
  </div>

  {#snippet footer()}
    {#if result}
      <button
        type="button"
        onclick={onClose}
        class="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
      >Done</button>
    {:else}
      <button
        type="button"
        onclick={onClose}
        class="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 rounded-md"
      >Cancel</button>
      <button
        type="button"
        onclick={submit}
        disabled={!canSubmit}
        class="px-4 py-2 text-sm {mode === 'replace'
          ? 'bg-red-600 hover:bg-red-700'
          : 'bg-blue-600 hover:bg-blue-700'} text-white rounded-md disabled:bg-slate-300 disabled:cursor-not-allowed"
      >
        {submitting
          ? 'Importing…'
          : mode === 'replace'
            ? 'Replace ledger'
            : 'Append rows'}
      </button>
    {/if}
  {/snippet}
</Modal>
