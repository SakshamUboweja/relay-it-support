'use client';
import { useCallback, useEffect, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  armColor,
  bestKey,
  bestPerColumn,
  costPerCase,
  fetchEvaluation,
  type EvaluationReport,
  type EvaluationRow,
  type Metric,
} from '@/lib/evaluation';
import {
  fmtDateTime,
  fmtMs,
  fmtPct,
  fmtRate,
  fmtTokens,
  fmtUsd,
} from '@/lib/format';

const armLabels: Record<string, string> = {
  'rules-v1': 'Rules v1',
  'rules-v2': 'Rules v2',
  single: 'Single agent',
  multi: 'Multi-agent',
};
const armLabel = (arm: string) => armLabels[arm] ?? arm;
const splitLabels: Record<string, string> = {
  dev: 'Dev',
  heldout: 'Held-out',
};
const splitLabel = (split: string) => splitLabels[split] ?? split;
const HELDOUT = 'heldout';
/** Charts stop being readable past the fixed palette, so the table stands alone. */
const MAX_CHART_ARMS = 4;

const decimal = (value: number | null) =>
  value == null ? '—' : value.toFixed(3);
const percentTick = (value: number) => `${Math.round(value * 100)}%`;
const firstSeen = (values: string[]) => [...new Set(values)];
const orderSplits = (rows: EvaluationRow[]) => {
  const seen = firstSeen(rows.map((row) => row.split));
  const known = ['dev', HELDOUT].filter((split) => seen.includes(split));
  return [...known, ...seen.filter((split) => !known.includes(split))];
};

function Meta({ report }: { report: EvaluationReport }) {
  const prompts = Object.entries(report.promptVersions)
    .map(([name, version]) => `${name} ${version}`)
    .join(', ');
  return (
    <div className="comparison-meta">
      <p>
        {report.model} · effort {report.effort} · scoring {report.scoring} ·{' '}
        {fmtDateTime(report.runDate)} · prompts {prompts || 'none recorded'}
      </p>
      {report.aborted && (
        <p className="comparison-warning">
          This run was aborted before it finished, so the numbers below cover
          part of the set only.
        </p>
      )}
      {report.caveats.length > 0 && (
        <ul className="small">
          {report.caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ComparisonTable({ report }: { report: EvaluationReport }) {
  const splits = orderSplits(report.rows);
  return (
    <div className="operator-table comparison-table">
      <Table>
        <caption className="sr-only">
          Routing, calibration, latency and cost for every evaluated arm, by
          split. The best value in each column of a split is in bold.
        </caption>
        <TableHeader>
          <TableRow>
            <TableHead>Split</TableHead>
            <TableHead>Arm</TableHead>
            <TableHead>Routing acc.</TableHead>
            <TableHead>Accepted prec.</TableHead>
            <TableHead>Security recall</TableHead>
            <TableHead>Escalation recall</TableHead>
            <TableHead>ECE</TableHead>
            <TableHead>Brier</TableHead>
            <TableHead>AUROC</TableHead>
            <TableHead>p50</TableHead>
            <TableHead>p95</TableHead>
            <TableHead>Cost/case</TableHead>
            <TableHead>Tokens in/out</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {splits.flatMap((split) => {
            const best = bestPerColumn(report.rows, split);
            const mark = (row: EvaluationRow, metric: Metric, text: string) =>
              best.has(bestKey(row.arm, metric)) ? (
                <strong>{text}</strong>
              ) : (
                text
              );
            return report.rows
              .filter((row) => row.split === split)
              .map((row) => (
                <TableRow key={split + row.arm}>
                  <TableCell>{splitLabel(split)}</TableCell>
                  <TableCell>{armLabel(row.arm)}</TableCell>
                  <TableCell>
                    {mark(row, 'routingAccuracy', fmtRate(row.routingAccuracy))}
                  </TableCell>
                  <TableCell>
                    {mark(
                      row,
                      'acceptedPrecision',
                      fmtRate(row.acceptedPrecision),
                    )}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'securityRecall', fmtRate(row.securityRecall))}
                  </TableCell>
                  <TableCell>
                    {mark(
                      row,
                      'escalationRecall',
                      fmtRate(row.escalationRecall),
                    )}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'ece', decimal(row.confidence.ece))}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'brier', decimal(row.confidence.brier))}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'auroc', decimal(row.confidence.auroc))}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'p50Ms', fmtMs(row.latency.p50Ms))}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'p95Ms', fmtMs(row.latency.p95Ms))}
                  </TableCell>
                  <TableCell>
                    {mark(row, 'costPerCase', fmtUsd(costPerCase(row)))}
                  </TableCell>
                  <TableCell>
                    {`${fmtTokens(row.tokens.input)} / ${fmtTokens(row.tokens.output)}`}
                  </TableCell>
                </TableRow>
              ));
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function RoutingChart({
  report,
  arms,
  config,
}: {
  report: EvaluationReport;
  arms: string[];
  config: ChartConfig;
}) {
  const data = orderSplits(report.rows).map((split) => {
    const point: Record<string, string | number | null> = {
      split: splitLabel(split),
    };
    for (const arm of arms) {
      const row = report.rows.find((r) => r.split === split && r.arm === arm);
      point[arm] = row?.routingAccuracy.rate ?? null;
      point[`${arm}-detail`] = row ? fmtRate(row.routingAccuracy) : 'no run';
    }
    return point;
  });
  return (
    <figure className="comparison-figure">
      <figcaption>Routing accuracy by split</figcaption>
      <ChartContainer config={config} className="comparison-chart">
        <BarChart accessibilityLayer data={data}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="split"
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--chart-axis)' }}
          />
          <YAxis
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tickFormatter={percentTick}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--chart-axis)' }}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(value, name, item) => {
                  const payload = item?.payload as
                    | Record<string, string>
                    | undefined;
                  const detail = payload?.[`${String(name)}-detail`];
                  return (
                    <span className="comparison-tip">
                      <span
                        className="comparison-swatch"
                        style={{
                          background: armColor(
                            String(name),
                            arms.indexOf(String(name)),
                          ),
                        }}
                      />
                      {armLabel(String(name))}
                      <strong>{detail ?? fmtPct(Number(value))}</strong>
                    </span>
                  );
                }}
              />
            }
          />
          <ChartLegend content={<ChartLegendContent />} />
          {arms.map((arm, index) => (
            <Bar
              key={arm}
              dataKey={arm}
              name={arm}
              fill={armColor(arm, index)}
              maxBarSize={24}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
            >
              <LabelList
                dataKey={arm}
                position="top"
                fill="var(--chart-axis)"
                fontSize={11}
                formatter={(value: unknown) =>
                  typeof value === 'number' ? percentTick(value) : ''
                }
              />
            </Bar>
          ))}
        </BarChart>
      </ChartContainer>
    </figure>
  );
}

function CalibrationChart({
  report,
  arms,
  config,
}: {
  report: EvaluationReport;
  arms: string[];
  config: ChartConfig;
}) {
  const heldout = report.rows.filter((row) => row.split === HELDOUT);
  const curves = arms
    .map((arm, index) => ({
      arm,
      color: armColor(arm, index),
      // The arm names the y field so the legend and tooltip resolve its label.
      points: (heldout.find((row) => row.arm === arm)?.confidence.bins ?? [])
        .filter((bin) => bin.count > 0)
        .map((bin) => ({
          meanConfidence: bin.meanConfidence,
          [arm]: bin.accuracy,
        })),
    }))
    .filter((curve) => curve.points.length > 0);
  // One dataset ordered by confidence: arms bin at their own means, and a row
  // holding a single arm's key is what lets the tooltip find a point to report.
  const curveData = curves
    .flatMap((curve) => curve.points)
    .sort((a, b) => a.meanConfidence - b.meanConfidence);

  if (curves.length > 0) {
    return (
      <figure className="comparison-figure">
        <figcaption>Reliability, held-out</figcaption>
        <ChartContainer config={config} className="comparison-chart">
          <LineChart accessibilityLayer data={curveData}>
            <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
            <XAxis
              type="number"
              dataKey="meanConfidence"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={percentTick}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--chart-axis)' }}
            />
            <YAxis
              type="number"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={percentTick}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--chart-axis)' }}
            />
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke="var(--chart-grid)"
              label={{
                value: 'Perfect calibration',
                position: 'insideBottomRight',
                fill: 'var(--chart-axis)',
                fontSize: 11,
              }}
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  labelFormatter={(_, payload) =>
                    `Confidence ${fmtPct(
                      Number(
                        (payload?.[0]?.payload as { meanConfidence?: number })
                          ?.meanConfidence,
                      ),
                    )}`
                  }
                  formatter={(value, name) => (
                    <span className="comparison-tip">
                      <span
                        className="comparison-swatch"
                        style={{
                          background: armColor(
                            String(name),
                            arms.indexOf(String(name)),
                          ),
                        }}
                      />
                      {armLabel(String(name))}
                      <strong>{fmtPct(Number(value))} correct</strong>
                    </span>
                  )}
                />
              }
            />
            <ChartLegend content={<ChartLegendContent />} />
            {curves.map((curve) => (
              <Line
                key={curve.arm}
                dataKey={curve.arm}
                name={curve.arm}
                type="linear"
                connectNulls
                stroke={curve.color}
                strokeWidth={2}
                dot={{ r: 4, strokeWidth: 2, stroke: '#fff' }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ChartContainer>
      </figure>
    );
  }

  // Per-bar colour rides on the datum: recharts merges a row's own fill over the Bar's.
  const eces = arms
    .map((arm, index) => ({
      arm,
      label: armLabel(arm),
      fill: armColor(arm, index),
      ece: heldout.find((row) => row.arm === arm)?.confidence.ece ?? null,
    }))
    .filter((x): x is typeof x & { ece: number } => x.ece != null);
  if (!eces.length)
    return (
      <figure className="comparison-figure">
        <figcaption>Calibration, held-out</figcaption>
        <p className="small">Calibration not measured in this run.</p>
      </figure>
    );
  return (
    <figure className="comparison-figure">
      <figcaption>
        Expected calibration error, held-out (lower is better)
      </figcaption>
      <ChartContainer config={config} className="comparison-chart">
        <BarChart
          accessibilityLayer
          layout="vertical"
          data={eces}
          margin={{ right: 44 }}
        >
          <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
          <XAxis
            type="number"
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--chart-axis)' }}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={96}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--chart-axis)' }}
          />
          <Bar
            dataKey="ece"
            maxBarSize={24}
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="ece"
              position="right"
              fill="var(--chart-axis)"
              fontSize={11}
              formatter={(value: unknown) =>
                typeof value === 'number' ? value.toFixed(3) : ''
              }
            />
          </Bar>
        </BarChart>
      </ChartContainer>
    </figure>
  );
}

/**
 * Operator-only view of the last offline evaluation: one table of every arm and
 * split, plus charts for the two questions arms are chosen on — did routing land
 * on the right team, and is the confidence it reports honest.
 */
export default function PipelineComparison() {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');

  // Kept out of the effect body so the first read never sets state synchronously.
  const run = useCallback(() => {
    let cancelled = false;
    fetchEvaluation()
      .then((next) => {
        if (cancelled) return;
        setReport(next);
        setState('ready');
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const reload = useCallback(() => {
    setState('loading');
    run();
  }, [run]);

  useEffect(() => run(), [run]);

  if (state === 'loading') return <p className="small">Loading comparison…</p>;
  if (state === 'error')
    return (
      <div className="review-alert comparison-error">
        <span>{error}</span>
        <button className="secondary" onClick={reload}>
          Retry
        </button>
      </div>
    );
  if (!report)
    return (
      <div className="comparison-body">
        <div className="empty compact">
          <p>
            No pipeline comparison yet. Run{' '}
            <code>npm run eval -- --arm all</code> to generate one.
          </p>
          <button className="secondary" onClick={reload}>
            Reload
          </button>
        </div>
      </div>
    );

  const arms = firstSeen(report.rows.map((row) => row.arm));
  const config: ChartConfig = Object.fromEntries(
    arms.map((arm, index) => [
      arm,
      { label: armLabel(arm), color: armColor(arm, index) },
    ]),
  );
  return (
    <div className="comparison-body">
      <div className="comparison-head">
        <Meta report={report} />
        <button className="secondary" onClick={reload}>
          Reload
        </button>
      </div>
      <ComparisonTable report={report} />
      {arms.length <= MAX_CHART_ARMS && (
        <div className="comparison-charts">
          <RoutingChart report={report} arms={arms} config={config} />
          <CalibrationChart report={report} arms={arms} config={config} />
        </div>
      )}
    </div>
  );
}
