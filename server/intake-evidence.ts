import type { z } from 'zod';
import type { Extraction } from './model';
import { fact, type Decision } from './domain';

// Consume validated quotes; never let model prose set provider fields or privileges.
export function applyExtraction(d: Decision, data: z.infer<typeof Extraction>) {
  const quotes = {
    impact: data.impactQuote,
    urgency: data.urgencyQuote,
    device: data.deviceQuote,
    started: data.startedQuote,
    workaround: data.workaroundQuote,
    attemptedSteps: data.attemptedStepsQuotes.length
      ? data.attemptedStepsQuotes.join('\n')
      : null,
    supportRequest: data.supportRequestQuote,
    procedureAttempted: data.procedureAttemptedQuote,
  };
  for (const [key, quote] of Object.entries(quotes)) {
    if (quote) d.facts[key] = fact(quote, 'user', data.evidenceIds);
  }
  if (data.impactQuote && d.priority === 'normal') {
    d.reasons = d.reasons.filter(
      (r) => r !== 'provisional-priority-impact-unknown',
    );
    d.reasons.push('normal-priority-reported-impact');
  }
  if (
    data.service &&
    d.service !== data.service &&
    d.visibility !== 'restricted'
  ) {
    d.accepted = false;
    d.team = 'Service Desk';
    d.procedure = null;
    d.reasons.push('model-catalog-conflict');
  }
  if (data.securityQuote) {
    d.facts.security = fact(data.securityQuote, 'user', data.evidenceIds);
    d.team = 'Security Review';
    d.escalation = 'security';
    d.priority = 'urgent';
    d.facts.priority = fact('urgent', 'policy_default', [d.version]);
    d.visibility = 'restricted';
    d.procedure = null;
    d.question = null;
    d.related = null;
    d.reasons.push('model-extracted-security-evidence');
  }
  return d;
}
