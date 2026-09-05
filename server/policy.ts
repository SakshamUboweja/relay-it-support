import { z } from 'zod';
import policyFile from '../config/policy.json';
import { catalog } from './fixtures';
import {
  fact,
  type Decision,
  type Source,
  type User,
  type Team,
  type Service,
} from './domain';
export const policy = z
  .object({
    version: z.string(),
    defaultPriority: z.literal('normal'),
    reviewMinutes: z.number().positive(),
    criticalServices: z.array(z.string()),
    routingMinimum: z.number().nonnegative(),
    routingMargin: z.number().nonnegative(),
    contextMaxAgeHours: z.number().positive(),
    relatedWindowHours: z.number().positive(),
    retentionDays: z.number().positive(),
    maxOutputTokens: z.number().positive(),
    maxModelRetries: z.number().max(1),
  })
  .parse(policyFile);
export function securityEvidence(text: string) {
  const clauses = text.toLowerCase().split(/[.!?;\n]+/);
  return clauses.some((c) => {
    if (
      /\b(how (do|can|should)|what (is|are)|training|example of|simulation|drill)\b/.test(
        c,
      )
    )
      return false;
    if (
      /\b(no|not|never|haven't|didn't|wasn't|isn't)\b.{0,35}\b(compromis|phish|suspicious|expos|leak|click)/.test(
        c,
      )
    )
      return false;
    return /\b(unexpected|unsolicited|unrequested|suspicious|unfamiliar)\b.{0,35}\b(mfa|approval|login|sign.in|prompt)|\b(mfa|approval|login)\b.{0,35}\b(didn.t request|did not request|didn.t initiate|don.t recognize|unexpected|unsolicited)|\b(account|credentials?)\b.{0,25}\b(compromised|hacked|stolen)|\b(phishing|phish)\b.{0,50}\b(clicked|entered|opened)|\b(clicked|entered|approved)\b.{0,50}\b(phishing|suspicious|fake|didn.t request)|\b(data|customer (data|records)|confidential (data|file))\b.{0,30}\b(exposed|leaked|public|sent outside)|\b(ransomware|encrypted my files)\b/.test(
      c,
    );
  });
}
export function decide(
  text: string,
  sources: Source[],
  user: User,
  clarifications = 0,
  now = new Date(),
): Decision {
  const start = performance.now(),
    t = text.toLowerCase(),
    msg = 'current-message';
  const security = securityEvidence(t),
    unsupported =
      /\b(new (access|account|laptop)|request access|access (to|approval)|grant|permission to|procure|purchase|buy |onboard|payroll|vacation|hr request|how (do|can|should) i report|what is phishing)\b/.test(
        t,
      );
  let candidates = catalog.filter((s) => s.aliases.some((a) => t.includes(a)));
  const vpnAuth =
    t.includes('vpn') &&
    ((/password/.test(t) && /chang|reset|reject|auth/.test(t)) ||
      /authentication failed|invalid credentials/.test(t));
  if (candidates.some((s) => s.id === 'vpn'))
    candidates = candidates.filter((s) => s.id !== 'sso');
  const allowed = sources.filter(
    (s) =>
      (s.visibility === 'all' ||
        s.visibility === user.scope ||
        user.role === 'operator') &&
      new Date(s.created_at) <= now,
  );
  const ranked = candidates
    .map((s) => {
      const team = (
        vpnAuth && s.id === 'vpn' ? 'Identity & Access' : s.team
      ) as Team;
      const matches = allowed.filter(
        (x) =>
          x.kind === 'case' &&
          x.service === s.id &&
          x.metadata.reviewed === true &&
          x.metadata.team === team,
      );
      return { service: s.id, team, score: 3 + (matches.length ? 1 : 0) };
    })
    .sort((a, b) => b.score - a.score);
  const top = ranked[0];
  const accepted =
    !!top &&
    !unsupported &&
    (ranked.length === 1 ||
      top.score - ranked[1].score >= policy.routingMargin) &&
    top.score >= policy.routingMinimum;
  let service: Service | null = accepted ? top.service : null;
  let team: Team = accepted ? top.team : 'Service Desk';
  const reasons = [
    unsupported
      ? 'unsupported-workflow'
      : accepted
        ? vpnAuth
          ? 'catalog-vpn-password-exception'
          : 'catalog-and-evidence'
        : 'insufficient-or-conflicting-evidence',
  ];
  const broad =
    /\b(everyone|entire (team|office|company)|all (employees|users|staff)|whole (team|office)|company.wide|organization.wide)\b/.test(
      t,
    ) && !/not (everyone|the entire|all)/.test(t);
  const blocked =
    /\b(blocked|cannot work|can.t work|unable to work|work has stopped)\b/.test(
      t,
    ) &&
    /no (usable )?workaround|no (other|alternative)|nothing else|without a workaround/.test(
      t,
    );
  const incidents = allowed.filter(
    (s) =>
      s.kind === 'incident' &&
      s.status === 'open' &&
      s.service === service &&
      s.location === user.location &&
      s.metadata.curated === true &&
      now.getTime() - new Date(s.updated_at).getTime() <=
        policy.relatedWindowHours * 3600000,
  );
  const related =
    !security && !unsupported && incidents.length === 1 ? incidents[0] : null;
  let priority: Decision['priority'] = 'normal',
    escalation: Decision['escalation'] = 'none';
  if (security) {
    team = 'Security Review';
    priority = 'urgent';
    escalation = 'security';
    reasons.push('possible-compromise-restricted-review');
  } else if (
    service &&
    policy.criticalServices.includes(service) &&
    (related || broad)
  ) {
    priority = 'urgent';
    escalation = 'urgent';
    reasons.push(
      related
        ? 'authoritative-active-advisory'
        : 'user-reported-broad-critical-loss',
    );
  } else if (blocked) {
    priority = 'elevated';
    escalation = 'elevated';
    reasons.push('explicit-work-blocked-no-workaround');
  } else reasons.push('provisional-priority-impact-unknown');
  const procedure =
    !unsupported && !security && escalation === 'none' && accepted
      ? (allowed.find(
          (s) =>
            s.kind === 'article' &&
            s.service === service &&
            s.metadata.procedure &&
            (s.metadata.procedure === 'wifi'
              ? /disconnect|won.t connect|cannot connect|can.t connect/.test(t)
              : /external (display|monitor)|monitor|display cable/.test(t)),
        ) ?? null)
      : null;
  const question =
    !accepted && !unsupported && !security && clarifications === 0
      ? 'Which service is affected: VPN, sign-in, Wi-Fi, your laptop, or Atlas?'
      : null;
  const facts: Decision['facts'] = {
    symptom: fact(text, 'user', [msg]),
    service: fact(service, service ? 'user' : 'unknown', service ? [msg] : []),
    impact: fact(
      broad ? 'broad loss reported' : null,
      broad ? 'user' : 'unknown',
      broad ? [msg] : [],
    ),
    urgency: fact(
      blocked ? 'work blocked; no workaround reported' : null,
      blocked ? 'user' : 'unknown',
      blocked ? [msg] : [],
    ),
    device: fact(user.device, 'context', ['demo-device']),
    location: fact(user.location, 'context', ['demo-directory']),
    passwordChange: fact(
      /(?:changed|reset).{0,20}password|password.{0,20}(?:changed|reset)/.test(
        t,
      )
        ? 'password change reported'
        : null,
      /password/.test(t) && vpnAuth ? 'user' : 'unknown',
      vpnAuth ? [msg] : [],
    ),
    rootCause: fact(
      vpnAuth ? 'cached credentials may be involved' : null,
      vpnAuth ? 'hypothesis' : 'unknown',
      vpnAuth ? ['catalog-vpn-password-exception'] : [],
    ),
    priority: fact(priority, 'policy_default', [policy.version]),
  };
  return {
    team,
    service,
    accepted: security || accepted,
    reasons,
    alternatives: ranked.map(({ team, score }) => ({ team, score })),
    priority,
    escalation,
    visibility: security ? 'restricted' : 'private',
    facts,
    sources: allowed
      .filter((s) => s.service === service && s.kind !== 'incident')
      .slice(0, 5)
      .map((s) => s.id),
    procedure,
    related,
    question,
    model: 'deterministic-demo-v1',
    version: policy.version,
    latencyMs: performance.now() - start,
    usage: { input: 0, output: 0 },
  };
}
