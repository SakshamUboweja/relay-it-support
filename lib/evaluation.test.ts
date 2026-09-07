import assert from 'node:assert/strict';
import test from 'node:test';
import {
  armColor,
  bestKey,
  bestPerColumn,
  fetchEvaluation,
  type EvaluationRow,
} from './evaluation';

const rate = (numerator: number, denominator: number) => ({
  numerator,
  denominator,
  rate: denominator ? numerator / denominator : null,
});

const row = (over: Partial<EvaluationRow> = {}): EvaluationRow => ({
  arm: 'multi',
  split: 'heldout',
  routingAccuracy: rate(41, 60),
  acceptedPrecision: rate(30, 40),
  eligibleCoverage: rate(40, 60),
  securityRecall: rate(5, 6),
  escalationRecall: rate(4, 5),
  confidence: {
    ece: 0.041,
    brier: 0.12,
    auroc: 0.81,
    selectiveAccuracy: 0.9,
    coverage: 0.7,
    bins: [],
  },
  latency: { p50Ms: 1200, p95Ms: 3400 },
  tokens: { input: 1000, output: 200, cached: 0, reasoning: 0 },
  estimatedCostUSD: 0.6,
  budgetExhausted: 0,
  cacheHits: 0,
  ...over,
});

const response = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(typeof body === 'string' ? body : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );

const reportBody = (rows: unknown[]) => ({
  available: true,
  runDate: '2026-09-01T10:00:00Z',
  model: 'gpt-5-mini',
  effort: 'low',
  scoring: 'exact',
  promptVersions: { intake: 'v3' },
  policyHash: 'abc',
  calibrationHash: 'def',
  cacheHits: 3,
  aborted: false,
  caveats: ['Small sample'],
  rows,
});

void test('armColor maps the known arms to fixed chart tokens', () => {
  assert.equal(armColor('rules-v1', 0), 'var(--chart-1)');
  assert.equal(armColor('rules-v2', 1), 'var(--chart-2)');
  assert.equal(armColor('single', 2), 'var(--chart-3)');
  assert.equal(armColor('multi', 3), 'var(--chart-4)');
});

void test('armColor keeps a known arm on its own colour whatever its index', () => {
  assert.equal(armColor('multi', 0), 'var(--chart-4)');
  assert.equal(armColor('rules-v1', 3), 'var(--chart-1)');
});

void test('armColor gives unknown arms the palette in first-seen order', () => {
  assert.equal(armColor('rules-v3', 0), 'var(--chart-1)');
  assert.equal(armColor('rules-v3', 1), 'var(--chart-2)');
  assert.equal(armColor('rules-v3', 4), 'var(--chart-1)');
});

void test('bestPerColumn marks the highest rates and the lowest error metrics', () => {
  const rows = [
    row({ arm: 'single' }),
    row({
      arm: 'multi',
      routingAccuracy: rate(50, 60),
      acceptedPrecision: rate(20, 40),
      securityRecall: rate(6, 6),
      escalationRecall: rate(3, 5),
      confidence: {
        ece: 0.02,
        brier: 0.3,
        auroc: 0.9,
        selectiveAccuracy: null,
        coverage: null,
        bins: [],
      },
      latency: { p50Ms: 900, p95Ms: 5000 },
      estimatedCostUSD: 1.2,
    }),
  ];
  const best = bestPerColumn(rows, 'heldout');
  assert.ok(best.has(bestKey('multi', 'routingAccuracy')));
  assert.ok(best.has(bestKey('single', 'acceptedPrecision')));
  assert.ok(best.has(bestKey('multi', 'securityRecall')));
  assert.ok(best.has(bestKey('single', 'escalationRecall')));
  assert.ok(best.has(bestKey('multi', 'ece')));
  assert.ok(best.has(bestKey('single', 'brier')));
  assert.ok(best.has(bestKey('multi', 'auroc')));
  assert.ok(best.has(bestKey('multi', 'p50Ms')));
  assert.ok(best.has(bestKey('single', 'p95Ms')));
  assert.ok(best.has(bestKey('single', 'costPerCase')));
  assert.equal(best.has(bestKey('single', 'routingAccuracy')), false);
});

void test('bestPerColumn only compares rows in the requested split', () => {
  const rows = [
    row({ arm: 'single', split: 'dev', routingAccuracy: rate(60, 60) }),
    row({ arm: 'multi', split: 'heldout', routingAccuracy: rate(41, 60) }),
    row({ arm: 'single', split: 'heldout', routingAccuracy: rate(30, 60) }),
  ];
  const best = bestPerColumn(rows, 'heldout');
  assert.ok(best.has(bestKey('multi', 'routingAccuracy')));
  assert.equal(best.has(bestKey('single', 'routingAccuracy')), false);
});

void test('bestPerColumn skips unknown values and marks every tie', () => {
  const rows = [
    row({
      arm: 'single',
      confidence: {
        ece: null,
        brier: null,
        auroc: null,
        selectiveAccuracy: null,
        coverage: null,
        bins: [],
      },
      estimatedCostUSD: null,
    }),
    row({ arm: 'multi' }),
    row({ arm: 'rules-v1' }),
  ];
  const best = bestPerColumn(rows, 'heldout');
  assert.equal(best.has(bestKey('single', 'ece')), false);
  assert.ok(best.has(bestKey('multi', 'ece')));
  assert.ok(best.has(bestKey('rules-v1', 'ece')));
  assert.equal(best.has(bestKey('single', 'costPerCase')), false);
  assert.ok(best.has(bestKey('multi', 'routingAccuracy')));
  assert.ok(best.has(bestKey('single', 'routingAccuracy')));
});

void test('bestPerColumn marks nothing when a split holds a single arm', () => {
  assert.equal(bestPerColumn([row()], 'heldout').size, 0);
});

void test('fetchEvaluation reads a report into typed rows', async () => {
  const report = await fetchEvaluation(() =>
    response(reportBody([{ ...row(), arm: 'multi', split: 'dev' }])),
  );
  assert.ok(report);
  assert.equal(report.model, 'gpt-5-mini');
  assert.deepEqual(report.caveats, ['Small sample']);
  assert.equal(report.rows.length, 1);
  assert.equal(report.rows[0].arm, 'multi');
  assert.equal(report.rows[0].routingAccuracy.numerator, 41);
  assert.equal(report.rows[0].confidence.bins.length, 0);
});

void test('fetchEvaluation returns null when the endpoint is missing', async () => {
  assert.equal(
    await fetchEvaluation(() => response({ error: 'not found' }, 404)),
    null,
  );
});

void test('fetchEvaluation returns null when no run is available', async () => {
  assert.equal(
    await fetchEvaluation(() => response({ available: false })),
    null,
  );
});

void test('fetchEvaluation returns null for a payload it cannot read', async () => {
  assert.equal(await fetchEvaluation(() => response('null')), null);
  assert.equal(await fetchEvaluation(() => response(reportBody([]))), null);
  assert.equal(
    await fetchEvaluation(() => response(reportBody([{ split: 'dev' }]))),
    null,
  );
});

void test('fetchEvaluation throws the server error on other failures', async () => {
  await assert.rejects(
    fetchEvaluation(() => response({ error: 'Operators only.' }, 403)),
    /Operators only\./,
  );
  await assert.rejects(
    fetchEvaluation(() => response('<html>', 500)),
    /evaluation/i,
  );
});

void test('fetchEvaluation treats unusable numbers as unknown', async () => {
  const report = await fetchEvaluation(() =>
    response(
      reportBody([
        {
          arm: 'single',
          split: 'dev',
          routingAccuracy: { numerator: 3, denominator: 4, rate: 'high' },
          confidence: { ece: null, bins: 'none' },
          latency: { p50Ms: null },
          estimatedCostUSD: 'free',
        },
      ]),
    ),
  );
  assert.ok(report);
  const parsed = report.rows[0];
  assert.equal(parsed.routingAccuracy.rate, null);
  assert.deepEqual(parsed.acceptedPrecision, {
    numerator: 0,
    denominator: 0,
    rate: null,
  });
  assert.deepEqual(parsed.confidence.bins, []);
  assert.equal(parsed.confidence.auroc, null);
  assert.equal(parsed.latency.p95Ms, null);
  assert.equal(parsed.estimatedCostUSD, null);
  assert.equal(parsed.tokens.input, 0);
});
