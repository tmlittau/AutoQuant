/**
 * Viewport-aware helpers for the SPA.
 *
 * We treat the Tailwind ``md`` breakpoint (768 px) as the line between mobile
 * and desktop layouts -- below it we collapse the nav, drop chart heights,
 * and switch tables into card-stack form.
 *
 * Use the ``isMobile`` store inside .svelte files via ``$isMobile``. It
 * reacts to ``matchMedia`` change events, which fire on viewport resizes,
 * orientation flips, and browser zoom -- so chart heights re-derive
 * automatically.
 */

import { readable } from 'svelte/store';

const MOBILE_QUERY = '(max-width: 767px)';

function readMatch(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia(MOBILE_QUERY).matches;
}

/** Reactive: true when viewport is below the Tailwind ``md`` breakpoint. */
export const isMobile = readable<boolean>(readMatch(), (set) => {
  if (typeof window === 'undefined') return;
  const mql = window.matchMedia(MOBILE_QUERY);
  const handler = (e: MediaQueryListEvent) => set(e.matches);
  mql.addEventListener('change', handler);
  return () => mql.removeEventListener('change', handler);
});

/**
 * Pick a Plotly chart height for the current viewport.
 *
 * - ``desktop`` is the existing fixed height (e.g. 460 for the sunburst).
 * - ``mobile`` overrides the phone height; if omitted we use ~65% of the
 *   desktop height (with a floor of 140 px so even the smallest panels
 *   stay readable).
 */
export function pickHeight(
  isMob: boolean,
  desktop: number,
  mobile?: number,
): number {
  if (!isMob) return desktop;
  return mobile ?? Math.max(140, Math.round(desktop * 0.65));
}

/**
 * Pick a Plotly chart ``margin`` object for the current viewport. The
 * mobile default trims the left axis margin from 60 px to 40 px which is
 * the difference between a heatmap fitting on a 375 px screen vs. clipping.
 */
export function pickMargin(
  isMob: boolean,
  overrides: Partial<{ l: number; r: number; t: number; b: number }> = {},
) {
  const base = isMob
    ? { l: 40, r: 12, t: 16, b: 36 }
    : { l: 60, r: 16, t: 20, b: 40 };
  return { ...base, ...overrides };
}
