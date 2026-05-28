/** Cross-cutting reactive state. */

import { writable, type Writable } from 'svelte/store';

export type AvQuota = {
  used: number;
  limit: number;
  last_call_at: string | null;
  resets_at: string | null;
};

export type AdapterStatus = {
  name: string;
  av_quota: AvQuota;
};

/** Current adapter + quota; loaded once at app boot, refreshed from Settings. */
export const adapterStore: Writable<AdapterStatus | null> = writable(null);

/** Bumped by mutating endpoints (Phase 5+) so views refetch on change. */
export const transactionsRevision: Writable<number> = writable(0);
