import 'dotenv/config';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { pool } from '../server/db';
import { decide, policy } from '../server/policy';
import { catalog, users } from '../server/fixtures';
import { teams, type Team, type Source } from '../server/domain';
type Case = {
  id: string;
  split: string;
  family: string;
  text: string;
  team: Team;
  service: string | null;
  escalation: string;
  expectedClarification: boolean;
};
const scenarios: Case[] = JSON.parse(
  await readFile('evaluation/scenarios.json', 'utf8'),
);
const policyHash = createHash('sha256')
  .update(await readFile('server/policy.ts'))
  .update(await readFile('config/policy.json'))
  .update(await readFile('server/fixtures.ts'))
  .digest('hex');
const frozen = {
  policyHash,
  version: policy.version,
  timestamp: new Date().toISOString(),
  note: 'Frozen before evaluation; no changes based on heldout failures.',
};
await writeFile(
  'evaluation/frozen.json',
  JSON.stringify(frozen, null, 2) + '\n',
);
const sources = (
  await pool.query<Source>(
    "SELECT * FROM sources WHERE kind IN ('article','case') AND created_at<=now() ORDER BY id",
  )
).rows;
const result: any = {
  runDate: new Date().toISOString(),
  mode: 'deterministic-demo',
  model: 'deterministic-demo-v1',
  promptVersion: 'intake-v1',
  policyVersion: policy.version,
  policyHash,
  dataset:
    '120 synthetic cases; 60/60 distinct family split; agent-authored labels NOT human reviewed',
  allowedInformation: {
    keyword:
      'Text and catalog; first alias match; no policy, history, model or incidents',
    proposed:
      'Same text and catalog, simulated Maya profile, approved pre-existing synthetic articles/cases, deterministic policy; no active incident context in this text-only corpus',
  },
  liveLLMBaseline: {
    status: 'skipped',
    reason:
      'No authorized live model configuration. A mock is not an LLM baseline.',
  },
  splits: {},
};
const count = (n: number, d: number) => ({
  numerator: n,
  denominator: d,
  rate: d ? n / d : null,
});
for (const split of ['dev', 'heldout']) {
  const cases = scenarios.filter((c) => c.split === split);
  for (const method of ['keyword', 'proposed']) {
    const rows = cases.map((c) => {
      const start = performance.now();
      const d = decide(c.text, sources, users[0]);
      const keyword = catalog.find((s) =>
        s.aliases.some((a) => c.text.toLowerCase().includes(a)),
      );
      const team =
          method === 'keyword'
            ? ((keyword?.team ?? 'Service Desk') as Team)
            : d.team,
        accepted = method === 'keyword' ? !!keyword : d.accepted;
      return {
        id: c.id,
        family: c.family,
        text: c.text,
        expected: {
          team: c.team,
          escalation: c.escalation,
          clarification: c.expectedClarification,
        },
        actual: {
          team,
          accepted,
          escalation: method === 'keyword' ? 'none' : d.escalation,
          clarification: method === 'keyword' ? false : !!d.question,
          reasons: method === 'keyword' ? ['first-keyword-match'] : d.reasons,
        },
        latencyMs: performance.now() - start,
      };
    });
    const eligible = rows.filter((r) => r.expected.team !== 'Service Desk'),
      accepted = rows.filter((r) => r.actual.accepted),
      security = rows.filter((r) => r.expected.escalation === 'security'),
      nonSecurity = rows.filter((r) => r.expected.escalation !== 'security'),
      escalated = rows.filter((r) => r.expected.escalation !== 'none'),
      nonEscalated = rows.filter((r) => r.expected.escalation === 'none');
    const times = rows.map((r) => r.latencyMs).sort((a, b) => a - b);
    const failures = rows.filter(
      (r) =>
        r.actual.team !== r.expected.team ||
        r.actual.escalation !== r.expected.escalation ||
        r.actual.clarification !== r.expected.clarification,
    );
    result.splits[split] ??= {};
    result.splits[split][method] = {
      routingAccuracy: count(
        rows.filter((r) => r.expected.team === r.actual.team).length,
        rows.length,
      ),
      perTeam: Object.fromEntries(
        teams.map((t) => {
          const group = rows.filter((r) => r.expected.team === t);
          return [
            t,
            count(
              group.filter((r) => r.actual.team === t).length,
              group.length,
            ),
          ];
        }),
      ),
      acceptedPrecision: count(
        accepted.filter((r) => r.actual.team === r.expected.team).length,
        accepted.length,
      ),
      eligibleCoverage: count(
        eligible.filter((r) => r.actual.accepted).length,
        eligible.length,
      ),
      automaticRoutingFrequency: count(accepted.length, rows.length),
      abstentions: rows.length - accepted.length,
      escalationRecall: count(
        escalated.filter((r) => r.actual.escalation === r.expected.escalation)
          .length,
        escalated.length,
      ),
      escalationFalsePositives: count(
        nonEscalated.filter((r) => r.actual.escalation !== 'none').length,
        nonEscalated.length,
      ),
      securityRecall: count(
        security.filter((r) => r.actual.escalation === 'security').length,
        security.length,
      ),
      securityFalsePositives: count(
        nonSecurity.filter((r) => r.actual.escalation === 'security').length,
        nonSecurity.length,
      ),
      clarificationAgreement: count(
        rows.filter((r) => r.actual.clarification === r.expected.clarification)
          .length,
        rows.length,
      ),
      firstTurnQuestionRate: count(
        rows.filter((r) => r.actual.clarification).length,
        rows.length,
      ),
      latency: {
        p50Ms: times[Math.floor(times.length * 0.5)],
        p95Ms: times[Math.floor(times.length * 0.95)],
        kind: 'In-process deterministic route evaluation; includes proposed decision computation for both methods. Not UI or provider latency.',
      },
      tokens: { input: 0, output: 0 },
      estimatedModelCostUSD: 0,
      failures,
    };
  }
}
result.unmeasured = [
  'Live model performance/baseline/cost',
  'Human-reviewed label validity',
  'Related-incident precision/recall: text corpus lacks association labels',
  'Fact fidelity across broad language; targeted assertions exist in tests',
  'Real-world resolution effectiveness',
  'Browser and provider-confirmed latency',
];
await writeFile(
  'evaluation/results.json',
  JSON.stringify(result, null, 2) + '\n',
);
const metric = (c: any) =>
  `${c.numerator}/${c.denominator} (${c.rate === null ? 'n/a' : (c.rate * 100).toFixed(1) + '%'})`;
let md = `# Measured synthetic demo results\n\nRun: ${result.runDate}. Policy: ${policy.version}. Frozen SHA-256: ${policyHash}.\n\nLabels are agent-authored and **not human reviewed**. This is a regression corpus, not validated real-world model performance. The held-out set was not used to tune these results.\n\n| Split / method | Route accuracy | Accepted precision | Eligible coverage | Security recall |\n|---|---|---|---|---|\n`;
for (const split of ['dev', 'heldout'])
  for (const m of ['keyword', 'proposed']) {
    const s = result.splits[split][m];
    md += `| ${split} / ${m} | ${metric(s.routingAccuracy)} | ${metric(s.acceptedPrecision)} | ${metric(s.eligibleCoverage)} | ${metric(s.securityRecall)} |\n`;
  }
md +=
  '\nAll failure cases and per-team counts are recorded in results.json. The live LLM baseline was skipped because no authorized live model configuration was supplied. Demo calls use zero model tokens; zero model cost is not an estimate of live costs.\n\nLimitations: ' +
  result.unmeasured.join('; ') +
  '.\n';
await writeFile('evaluation/RESULTS.md', md);
console.log(md);
await pool.end();
