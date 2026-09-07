import type { TraceRun } from '@/lib/domain';
import { pipelineLabel } from '@/lib/confidence';
import { fmtMs, fmtTokens, fmtUsd } from '@/lib/format';
import { roleLabel, sumStepLatency, sumUsage } from '@/lib/trace';

/**
 * Per-step evidence for every agent run on a report, oldest run first. Native
 * <details> keeps whatever an operator opened expanded across the 4 s poll.
 */
export function AgentTrace({ runs }: { runs: TraceRun[] }) {
  return (
    <div className="agent-runs">
      {runs.map((run) => {
        const stepped = sumUsage(run.steps);
        const input = run.usage?.input ?? stepped.input;
        const output = run.usage?.output ?? stepped.output;
        return (
          <section className="agent-run" key={run.id}>
            <p className="agent-run-head">
              {pipelineLabel(run.pipeline)} · {run.status} ·{' '}
              {run.model ?? 'no model'} · {run.reasoningEffort ?? '—'}
            </p>
            <ol className="agent-trace">
              {run.steps.map((step) => {
                // Policy steps carry no model or prompt, so drop the orphan separator.
                const source = (
                  step.kind === 'tool_call'
                    ? [step.toolName]
                    : [step.model, step.promptVersion]
                )
                  .filter(Boolean)
                  .join(' · ');
                return (
                  <li key={run.id + step.seq}>
                    <span className="agent-trace-index" aria-hidden="true">
                      {step.seq}
                    </span>
                    <div className="agent-trace-body">
                      <div className="agent-trace-head">
                        <strong>{roleLabel(step.role)}</strong>
                        {source && <span className="small">{source}</span>}
                        {step.status !== 'ok' && (
                          <span className={'trace-status ' + step.status}>
                            {step.status}
                          </span>
                        )}
                        <dl className="trace-metrics">
                          <div>
                            <dt>Latency</dt>
                            <dd>{fmtMs(step.latencyMs)}</dd>
                          </div>
                          <div>
                            <dt>Tokens</dt>
                            <dd>
                              {`${fmtTokens(step.usage?.input)} in · ${fmtTokens(step.usage?.output)} out`}
                            </dd>
                          </div>
                          <div>
                            <dt>Cost</dt>
                            <dd>
                              {step.kind === 'policy'
                                ? '$0.00 (no model call)'
                                : fmtUsd(step.costUsd)}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      <p className="trace-summary">{step.outputSummary}</p>
                      {step.inputSummary && (
                        <details>
                          <summary>Input</summary>
                          <p className="trace-summary">{step.inputSummary}</p>
                        </details>
                      )}
                      {step.kind === 'tool_call' && step.toolArgs && (
                        <details className="trace-tools">
                          <summary>Arguments and result</summary>
                          <code className="trace-args">
                            {JSON.stringify(step.toolArgs)}
                          </code>
                          <p className="trace-summary">
                            {step.toolResultSummary ?? 'No result recorded.'}
                          </p>
                        </details>
                      )}
                      {step.error && (
                        <p className="review-caption">{step.error}</p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
            <p className="small trace-footer">
              Wall clock {fmtMs(run.latencyMs)} · steps sum{' '}
              {fmtMs(sumStepLatency(run.steps))} · {fmtTokens(input)} in /{' '}
              {fmtTokens(output)} out · {fmtUsd(run.costUsd)}
            </p>
          </section>
        );
      })}
    </div>
  );
}
