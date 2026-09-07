import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fmtDateTime,
  fmtMs,
  fmtPct,
  fmtRate,
  fmtTokens,
  fmtUsd,
} from './format';

void test('fmtDateTime renders a readable month, day and time', () => {
  const formatted = fmtDateTime('2026-03-04T09:05:00Z');
  assert.match(formatted, /\d/);
  assert.notEqual(formatted, '2026-03-04T09:05:00Z');
});

void test('fmtDateTime honours an explicit locale', () => {
  assert.match(
    fmtDateTime('2026-03-04T09:05:00Z', 'en-US'),
    /^Mar [34], \d{1,2}:\d{2}\s(AM|PM)$/,
  );
});

void test('fmtMs formats milliseconds, seconds and minutes', () => {
  assert.equal(fmtMs(820), '820 ms');
  assert.equal(fmtMs(0), '0 ms');
  assert.equal(fmtMs(999), '999 ms');
  assert.equal(fmtMs(1000), '1.0 s');
  assert.equal(fmtMs(4200), '4.2 s');
  assert.equal(fmtMs(59900), '59.9 s');
  assert.equal(fmtMs(59949), '59.9 s');
  assert.equal(fmtMs(59950), '1m 0s');
  assert.equal(fmtMs(59999), '1m 0s');
  assert.equal(fmtMs(60000), '1m 0s');
  assert.equal(fmtMs(72000), '1m 12s');
  assert.equal(fmtMs(119900), '2m 0s');
});

void test('fmtMs renders an em dash when the duration is missing', () => {
  assert.equal(fmtMs(null), '—');
  assert.equal(fmtMs(undefined), '—');
});

void test('fmtMs treats non-finite durations like a missing value', () => {
  assert.equal(fmtMs(Number.NaN), '—');
  assert.equal(fmtMs(Number.POSITIVE_INFINITY), '—');
  assert.equal(fmtMs(Number.NEGATIVE_INFINITY), '—');
});

void test('fmtTokens abbreviates thousands and millions', () => {
  assert.equal(fmtTokens(0), '0');
  assert.equal(fmtTokens(742), '742');
  assert.equal(fmtTokens(999), '999');
  assert.equal(fmtTokens(1000), '1K');
  assert.equal(fmtTokens(12400), '12.4K');
  assert.equal(fmtTokens(12000), '12K');
  assert.equal(fmtTokens(1200000), '1.2M');
  assert.equal(fmtTokens(3000000), '3M');
});

void test('fmtTokens rounds before choosing the unit', () => {
  assert.equal(fmtTokens(999999), '1.0M');
  assert.equal(fmtTokens(999499), '999.5K');
  assert.equal(fmtTokens(1000000), '1M');
});

void test('fmtTokens treats non-finite counts like a missing value', () => {
  assert.equal(fmtTokens(Number.NaN), '—');
  assert.equal(fmtTokens(Number.POSITIVE_INFINITY), '—');
  assert.equal(fmtTokens(null), '—');
  assert.equal(fmtTokens(undefined), '—');
});

void test('fmtUsd keeps four decimals for sub-cent amounts', () => {
  assert.equal(fmtUsd(null), 'unknown');
  assert.equal(fmtUsd(undefined), 'unknown');
  assert.equal(fmtUsd(0), '$0.00');
  assert.equal(fmtUsd(0.0042), '$0.0042');
  assert.equal(fmtUsd(0.01), '$0.01');
  assert.equal(fmtUsd(1.234), '$1.23');
});

void test('fmtUsd treats non-finite amounts like a missing value', () => {
  assert.equal(fmtUsd(Number.NaN), 'unknown');
  assert.equal(fmtUsd(Number.POSITIVE_INFINITY), 'unknown');
  assert.equal(fmtUsd(Number.NEGATIVE_INFINITY), 'unknown');
});

void test('fmtPct renders one decimal or n/a', () => {
  assert.equal(fmtPct(null), 'n/a');
  assert.equal(fmtPct(0), '0.0%');
  assert.equal(fmtPct(0.683), '68.3%');
  assert.equal(fmtPct(1), '100.0%');
});

void test('fmtPct treats non-finite rates like a missing value', () => {
  assert.equal(fmtPct(Number.NaN), 'n/a');
  assert.equal(fmtPct(Number.POSITIVE_INFINITY), 'n/a');
});

void test('fmtRate pairs the counts with the percentage', () => {
  assert.equal(
    fmtRate({ numerator: 41, denominator: 60, rate: 41 / 60 }),
    '41/60 (68.3%)',
  );
  assert.equal(
    fmtRate({ numerator: 0, denominator: 0, rate: null }),
    '0/0 (n/a)',
  );
});
