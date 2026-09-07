export const fmtDateTime = (s: string, locale?: string | string[]) =>
  new Date(s).toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
/** Missing and non-finite numbers read the same way: the value is not known. */
const given = (v: number | null | undefined): v is number =>
  v != null && Number.isFinite(v);
export function fmtMs(ms: number | null | undefined): string {
  if (!given(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = Number((ms / 1000).toFixed(1));
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}
/** Whole units print without a decimal; everything else keeps one. */
const scale = (n: number, unit: number) =>
  n % unit === 0 ? String(n / unit) : (n / unit).toFixed(1);
export function fmtTokens(n: number | null | undefined): string {
  if (!given(n)) return '—';
  if (n < 1000) return String(n);
  // Round in thousands first so 999_999 reads as 1.0M rather than 1000K.
  return Number((n / 1000).toFixed(1)) < 1000
    ? `${scale(n, 1000)}K`
    : `${scale(n, 1e6)}M`;
}
export function fmtUsd(v: number | null | undefined): string {
  if (!given(v)) return 'unknown';
  if (v === 0) return '$0.00';
  return `$${v.toFixed(v < 0.01 ? 4 : 2)}`;
}
export function fmtPct(rate: number | null | undefined): string {
  if (!given(rate)) return 'n/a';
  return `${(rate * 100).toFixed(1)}%`;
}
export function fmtRate(v: {
  numerator: number;
  denominator: number;
  rate: number | null;
}): string {
  return `${v.numerator}/${v.denominator} (${fmtPct(v.rate)})`;
}
