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

/**
 * Bumped by the top-bar Refresh button. Every price-fetching view (Dashboard,
 * PortfolioView, Watchlist, Diversification, Stock) tracks this in its
 * loading effect so a single tap re-pulls fresh quotes everywhere.
 *
 * Kept separate from ``transactionsRevision`` so a price refresh doesn't
 * reload write-only views (Transactions) that don't need it.
 */
export const pricesRevision: Writable<number> = writable(0);

/** ISO-8601 of the last successful Refresh; rendered in the top bar / drawer
 * as "Updated 14:32". null until the user has tapped Refresh at least once. */
export const lastRefreshedAt: Writable<string | null> = writable(null);
