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
