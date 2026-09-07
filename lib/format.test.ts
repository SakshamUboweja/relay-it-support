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

void test('fmtMs formats milliseconds, seconds and minutes', () => {
  assert.equal(fmtMs(820), '820 ms');
  assert.equal(fmtMs(0), '0 ms');
  assert.equal(fmtMs(999), '999 ms');
  assert.equal(fmtMs(1000), '1.0 s');
  assert.equal(fmtMs(4200), '4.2 s');
  assert.equal(fmtMs(59999), '60.0 s');
  assert.equal(fmtMs(60000), '1m 0s');
  assert.equal(fmtMs(72000), '1m 12s');
  assert.equal(fmtMs(119900), '2m 0s');
});

void test('fmtMs renders an em dash when the duration is missing', () => {
  assert.equal(fmtMs(null), '—');
  assert.equal(fmtMs(undefined), '—');
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

void test('fmtUsd keeps four decimals for sub-cent amounts', () => {
  assert.equal(fmtUsd(null), 'unknown');
  assert.equal(fmtUsd(undefined), 'unknown');
  assert.equal(fmtUsd(0), '$0.00');
  assert.equal(fmtUsd(0.0042), '$0.0042');
  assert.equal(fmtUsd(0.01), '$0.01');
  assert.equal(fmtUsd(1.234), '$1.23');
});

void test('fmtPct renders one decimal or n/a', () => {
  assert.equal(fmtPct(null), 'n/a');
  assert.equal(fmtPct(0), '0.0%');
  assert.equal(fmtPct(0.683), '68.3%');
  assert.equal(fmtPct(1), '100.0%');
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
