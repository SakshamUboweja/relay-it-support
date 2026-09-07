import { Check } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { AgentTrace } from '@/components/agent-trace';
import { ConfidenceBadge } from '@/components/confidence-badge';
import { PipelineChip } from '@/components/pipeline-chip';
import { isConfidence } from '@/lib/confidence';
import type { Report, Trace } from '@/lib/domain';
import { fmtMs, fmtPct, fmtTokens } from '@/lib/format';

/** Operator view of why a report went where it did, and what the agents did. */
export function DecisionRecord({
  report,
  trace,
}: {
  report: Report;
  trace?: Trace;
}) {
  const decision = report.decision;
  const { confidence, proposal, reviewer } = decision;
  const candidates = [...decision.alternatives].sort(
    (a, b) => b.score - a.score,
  );
  const top = candidates.reduce((max, c) => Math.max(max, c.score), 0);
  const share = (score: number) =>
    top > 0 ? `${Math.min(100, Math.max(0, (score / top) * 100))}%` : '0%';
  return (
    <div className="decision-panel">
      <div className="section-heading">
        <h2>Decision record</h2>
        <span className="small decision-meta">
          <PipelineChip pipeline={decision.pipeline} />
          <ConfidenceBadge confidence={confidence} showScore />
          {decision.model} · {decision.version}
        </span>
      </div>
      {isConfidence(confidence) && (
        <div className="decision-confidence">
          <p className="small">{confidence.why}</p>
          <ul className="confidence-signals">
            {confidence.signals
              .filter((s) => s.label.trim())
              .map((s, i) => (
                <li key={`${s.kind}-${i}`}>
                  {s.label}
                  {s.value != null && (
                    <span className="signal-value">{s.value.toFixed(2)}</span>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}
      <div className="fact-grid">
        {Object.entries(decision.facts).map(([key, f]) => (
          <div key={key}>
            <span className="fact-label">{key.replace(/([A-Z])/g, ' $1')}</span>
            <strong>{f.value ?? 'Unknown'}</strong>
            <span className={'origin ' + f.origin}>
              {f.origin.replace('_', ' ')}
            </span>
            <span className="small">
              {f.evidenceIds.join(', ') || 'No evidence supplied'}
            </span>
          </div>
        ))}
      </div>
      <div className="reason-codes">
        <strong>Policy reasons</strong>
        {decision.reasons.map((x) => (
          <code key={x}>{x}</code>
        ))}
      </div>
      <h3 className="decision-subheading">Candidate teams</h3>
      <div className="operator-table candidates">
        {candidates.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>TEAM</TableHead>
                <TableHead>SCORE</TableHead>
                <TableHead>
                  <span className="sr-only">Score relative to the leader</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidates.map((c) => {
                const selected = c.team === decision.team;
                return (
                  <TableRow
                    key={c.team}
                    aria-current={selected ? 'true' : undefined}
                    className={selected ? 'selected-row' : undefined}
                  >
                    <TableCell>
                      {c.team}
                      {selected && (
                        <span className="candidate-selected">
                          <Check size={13} aria-hidden="true" />
                          Selected
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {c.score.toFixed(1)}
                    </TableCell>
                    <TableCell>
                      <span className="score-bar" aria-hidden="true">
                        <i style={{ width: share(c.score) }} />
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
              {!decision.accepted && (
                <TableRow>
                  <TableCell className="candidate-abstain" colSpan={3}>
                    Abstained to Service Desk
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        ) : (
          <p className="empty-table">No alternatives recorded.</p>
        )}
      </div>
      <p className="small">
        Candidate scores are uncalibrated ranking values; the confidence above
        is calibrated.
      </p>
      {proposal && (
        <div className="agent-proposal">
          <strong>Agent proposal</strong>
          <p>
            {proposal.team} ({proposal.service ?? 'no service'}) ·{' '}
            {Math.round(proposal.probability * 100)}% ·{' '}
            {proposal.abstain ? 'abstained' : 'proposed'}
          </p>
          <p className="small">{proposal.rationale}</p>
        </div>
      )}
      {reviewer && (
        <div className="agent-reviewer">
          <p>
            Reviewer: {reviewer.verdict} · agreement{' '}
            {fmtPct(reviewer.agreementProbability)}
          </p>
          {reviewer.issues.length > 0 && (
            <ul className="reviewer-issues">
              {reviewer.issues.map((issue, i) => (
                <li key={`${issue.field}-${i}`}>
                  {issue.field}: {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <p className="small">
        Suggested steps: {report.offered.join(', ') || 'None'} · Confirmed
        attempted: {report.attempted.join(', ') || 'None'}
      </p>
      <h3 className="decision-subheading">Agent timeline</h3>
      {trace?.runs?.length ? (
        <AgentTrace runs={trace.runs} />
      ) : (
        <p className="small">
          {decision.model} · {decision.promptVersion ?? 'no prompt version'} ·{' '}
          {fmtMs(decision.latencyMs)} · {fmtTokens(decision.usage?.input)} in /{' '}
          {fmtTokens(decision.usage?.output)} out
        </p>
      )}
    </div>
  );
}
