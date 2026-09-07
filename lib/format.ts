export const fmtDateTime = (s: string) =>
  new Date(s).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
const trim = (value: string) => value.replace(/\.0$/, '');
export function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1e6) return `${trim((n / 1000).toFixed(1))}K`;
  return `${trim((n / 1e6).toFixed(1))}M`;
}
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return 'unknown';
  if (v === 0) return '$0.00';
  return `$${v.toFixed(v < 0.01 ? 4 : 2)}`;
}
export function fmtPct(rate: number | null): string {
  if (rate == null) return 'n/a';
  return `${(rate * 100).toFixed(1)}%`;
}
export function fmtRate(v: {
  numerator: number;
  denominator: number;
  rate: number | null;
}): string {
  return `${v.numerator}/${v.denominator} (${fmtPct(v.rate)})`;
}
