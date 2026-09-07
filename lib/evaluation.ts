/** Types and helpers for the offline evaluation report behind GET /api/evaluation. */
export type EvaluationRate = {
  numerator: number;
  denominator: number;
  rate: number | null;
};
export type CalibrationBin = {
  lo: number;
  hi: number;
  count: number;
  meanConfidence: number;
  accuracy: number;
};
export type EvaluationConfidence = {
  ece: number | null;
  brier: number | null;
  auroc: number | null;
  selectiveAccuracy: number | null;
  coverage: number | null;
  bins: CalibrationBin[];
};
export type EvaluationRow = {
  arm: string;
  split: string;
  routingAccuracy: EvaluationRate;
  acceptedPrecision: EvaluationRate;
  eligibleCoverage: EvaluationRate;
  securityRecall: EvaluationRate;
  escalationRecall: EvaluationRate;
  confidence: EvaluationConfidence;
  latency: { p50Ms: number | null; p95Ms: number | null };
  tokens: { input: number; output: number; cached: number; reasoning: number };
  estimatedCostUSD: number | null;
  budgetExhausted: number;
  cacheHits: number;
};
export type EvaluationReport = {
  runDate: string;
  model: string;
  effort: string;
  scoring: string;
  promptVersions: Record<string, string>;
  policyHash: string;
  calibrationHash: string;
  cacheHits: number;
  aborted: boolean;
  caveats: string[];
  rows: EvaluationRow[];
};

/** Columns the table compares; each names the direction that counts as better. */
export const metrics = [
  'routingAccuracy',
  'acceptedPrecision',
  'securityRecall',
  'escalationRecall',
  'ece',
  'brier',
  'auroc',
  'p50Ms',
  'p95Ms',
  'costPerCase',
] as const;
export type Metric = (typeof metrics)[number];
const higherIsBetter: Record<Metric, boolean> = {
  routingAccuracy: true,
  acceptedPrecision: true,
  securityRecall: true,
  escalationRecall: true,
  ece: false,
  brier: false,
  auroc: true,
  p50Ms: false,
  p95Ms: false,
  costPerCase: false,
};

const palette = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
];
const armColors: Record<string, string> = {
  'rules-v1': palette[0],
  'rules-v2': palette[1],
  single: palette[2],
  multi: palette[3],
};
/** Fixed colour per known arm so a filtered chart never repaints the survivors. */
export const armColor = (arm: string, index: number): string =>
  armColors[arm] ?? palette[index % palette.length];

/** Cost of a single case, which is what makes arms with different sample sizes comparable. */
export const costPerCase = (row: EvaluationRow): number | null =>
  row.estimatedCostUSD == null || row.routingAccuracy.denominator <= 0
    ? null
    : row.estimatedCostUSD / row.routingAccuracy.denominator;

const metricValue = (row: EvaluationRow, metric: Metric): number | null => {
  switch (metric) {
    case 'routingAccuracy':
      return row.routingAccuracy.rate;
    case 'acceptedPrecision':
      return row.acceptedPrecision.rate;
    case 'securityRecall':
      return row.securityRecall.rate;
    case 'escalationRecall':
      return row.escalationRecall.rate;
    case 'ece':
      return row.confidence.ece;
    case 'brier':
      return row.confidence.brier;
    case 'auroc':
      return row.confidence.auroc;
    case 'p50Ms':
      return row.latency.p50Ms;
    case 'p95Ms':
      return row.latency.p95Ms;
    case 'costPerCase':
      return costPerCase(row);
  }
};

/** Key for the winners set; the component asks it per cell. */
export const bestKey = (arm: string, metric: Metric): string =>
  `${arm}|${metric}`;

/**
 * Winners per column within one split. Ties all win, unknown values never do,
 * and a split holding one arm has nothing to compare, so nothing is marked.
 */
export function bestPerColumn(
  rows: EvaluationRow[],
  split: string,
): Set<string> {
  const winners = new Set<string>();
  const inSplit = rows.filter((row) => row.split === split);
  if (inSplit.length < 2) return winners;
  for (const metric of metrics) {
    const scored = inSplit
      .map((row) => ({ arm: row.arm, value: metricValue(row, metric) }))
      .filter((x): x is { arm: string; value: number } => x.value != null);
    if (!scored.length) continue;
    const best = scored.reduce(
      (acc, x) =>
        higherIsBetter[metric]
          ? Math.max(acc, x.value)
          : Math.min(acc, x.value),
      scored[0].value,
    );
    for (const x of scored) {
      if (x.value === best) winners.add(bestKey(x.arm, metric));
    }
  }
  return winners;
}

const isObject = (x: unknown): x is Record<string, unknown> =>
  typeof x === 'object' && x !== null;
const number = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;
const count = (x: unknown): number => number(x) ?? 0;
const text = (x: unknown, fallback = ''): string =>
  typeof x === 'string' ? x : fallback;
const readRate = (x: unknown): EvaluationRate => {
  const o = isObject(x) ? x : {};
  return {
    numerator: count(o.numerator),
    denominator: count(o.denominator),
    rate: number(o.rate),
  };
};
const readBins = (x: unknown): CalibrationBin[] =>
  (Array.isArray(x) ? x : []).filter(isObject).map((b) => ({
    lo: count(b.lo),
    hi: count(b.hi),
    count: count(b.count),
    meanConfidence: count(b.meanConfidence),
    accuracy: count(b.accuracy),
  }));
const readRow = (x: unknown): EvaluationRow | null => {
  if (!isObject(x) || typeof x.arm !== 'string' || typeof x.split !== 'string')
    return null;
  const confidence = isObject(x.confidence) ? x.confidence : {};
  const latency = isObject(x.latency) ? x.latency : {};
  const tokens = isObject(x.tokens) ? x.tokens : {};
  return {
    arm: x.arm,
    split: x.split,
    routingAccuracy: readRate(x.routingAccuracy),
    acceptedPrecision: readRate(x.acceptedPrecision),
    eligibleCoverage: readRate(x.eligibleCoverage),
    securityRecall: readRate(x.securityRecall),
    escalationRecall: readRate(x.escalationRecall),
    confidence: {
      ece: number(confidence.ece),
      brier: number(confidence.brier),
      auroc: number(confidence.auroc),
      selectiveAccuracy: number(confidence.selectiveAccuracy),
      coverage: number(confidence.coverage),
      bins: readBins(confidence.bins),
    },
    latency: { p50Ms: number(latency.p50Ms), p95Ms: number(latency.p95Ms) },
    tokens: {
      input: count(tokens.input),
      output: count(tokens.output),
      cached: count(tokens.cached),
      reasoning: count(tokens.reasoning),
    },
    estimatedCostUSD: number(x.estimatedCostUSD),
    budgetExhausted: count(x.budgetExhausted),
    cacheHits: count(x.cacheHits),
  };
};

/** A payload without a usable run reads the same as no run at all: null. */
export function readEvaluation(payload: unknown): EvaluationReport | null {
  if (!isObject(payload) || payload.available !== true) return null;
  const rows = (Array.isArray(payload.rows) ? payload.rows : [])
    .map(readRow)
    .filter((row): row is EvaluationRow => row !== null);
  if (!rows.length) return null;
  const prompts = isObject(payload.promptVersions)
    ? payload.promptVersions
    : {};
  return {
    runDate: text(payload.runDate),
    model: text(payload.model, 'unknown model'),
    effort: text(payload.effort, 'unknown'),
    scoring: text(payload.scoring, 'unknown'),
    promptVersions: Object.fromEntries(
      Object.entries(prompts).map(([k, v]) => [k, text(v)]),
    ),
    policyHash: text(payload.policyHash),
    calibrationHash: text(payload.calibrationHash),
    cacheHits: count(payload.cacheHits),
    aborted: payload.aborted === true,
    caveats: (Array.isArray(payload.caveats) ? payload.caveats : []).filter(
      (c): c is string => typeof c === 'string',
    ),
    rows,
  };
}

type Fetcher = (path: string) => Promise<Response>;
const failure = 'The evaluation report could not be read. Please retry.';

/**
 * Operator-only report. A 404 means the endpoint is not deployed yet, which
 * reads as "no results" rather than an error; other failures surface.
 */
export async function fetchEvaluation(
  fetcher: Fetcher = (path) => globalThis.fetch(path),
): Promise<EvaluationReport | null> {
  const res = await fetcher('/api/evaluation');
  if (res.status === 404) return null;
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    if (!res.ok) throw new Error(failure);
    return null;
  }
  if (!res.ok) {
    const error = isObject(payload) ? payload.error : null;
    throw new Error(typeof error === 'string' ? error : failure);
  }
  return readEvaluation(payload);
}
