/** Locale-aware formatters used across the UI. EUR is the base currency. */

const eurFmt = new Intl.NumberFormat('de-DE', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 2,
});

const numFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });

export function fmtEUR(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '–';
  return eurFmt.format(v);
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '–';
  return v.toFixed(digits);
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '–';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '–';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString('en-CA'); // YYYY-MM-DD
}

export function fmtLocal(v: number | null | undefined, currency = 'USD'): string {
  if (v == null || Number.isNaN(v)) return '–';
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 2 }).format(v);
  } catch {
    return `${currency} ${numFmt.format(v)}`;
  }
}

/**
 * Format a crypto / share quantity with a precision that suits the asset.
 * Crypto holdings are often fractional to 8 places (BTC) or 6 (ETH); a flat
 * 2-dp format would render 0.00075 BTC as "0.00". The ticker prefix
 * (before the "-EUR" / "-USD" pair suffix) picks the precision.
 */
export function fmtCoin(v: number | null | undefined, ticker = ''): string {
  if (v == null || Number.isNaN(v)) return '–';
  const base = ticker.split('-')[0].toUpperCase();
  let digits = 4;
  if (base === 'BTC' || base === 'WBTC') digits = 8;
  else if (base === 'ETH' || base === 'SOL' || base === 'BNB') digits = 6;
  else if (base === 'USDC' || base === 'USDT' || base === 'DAI' || base === 'EUROC')
    digits = 2;
  // Trim trailing zeros for readability while keeping at least 2 dp.
  const fixed = v.toFixed(digits);
  return fixed.includes('.')
    ? fixed.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '.00')
    : fixed;
}
