import type { Report } from './domain';

export function ticketDescription(report: Report) {
  const d = report.decision;
  const value = (key: string) => d.facts[key]?.value ?? 'Not reported';
  const procedure = (id: string) =>
    d.procedure?.id === id
      ? `${d.procedure.title}: ${d.procedure.body}`
      : `Approved procedure ${id}`;
  return [
    'Reported issue',
    value('symptom'),
    '',
    `Started: ${value('started')}`,
    `Affected device: ${value('device')}`,
    `Impact: ${value('impact')}`,
    `Urgency: ${value('urgency')}`,
    `Workaround: ${value('workaround')}`,
    '',
    'Troubleshooting reported by the requester',
    value('attemptedSteps'),
    '',
    'Troubleshooting offered by Relay',
    ...(report.offered.length ? report.offered.map(procedure) : ['None']),
    '',
    'Requester-confirmed attempts of Relay procedures',
    ...(report.attempted.length
      ? report.attempted.map(procedure)
      : ['None confirmed in Relay']),
    '',
    `Support team: ${d.team}`,
    `Priority: ${d.priority} (application policy)`,
    ...(d.facts.security?.value
      ? [`Security evidence: ${value('security')}`]
      : []),
    ...(d.facts.rootCause?.value
      ? [`Possible cause (unconfirmed): ${value('rootCause')}`]
      : []),
  ].join('\n');
}
