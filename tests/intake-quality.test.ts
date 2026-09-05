import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { pool } from '../server/db';
import { users } from '../server/fixtures';
import { getReport, intake, processIntake } from '../server/workflow';
import { Extraction, validateExtraction, modelSettings } from '../server/model';
import { draft } from '../server/connector';
import type { Source } from '../server/domain';

// This test process uses a demo database and injected model/retrieval calls.
// It never dispatches the connector outbox or calls a live provider.
process.env.APP_MODE = 'live';
process.env.OPENAI_MODEL = 'gpt-5.6-terra';
process.env.OPENAI_REASONING_EFFORT = 'high';
process.env.OPENAI_MAX_OUTPUT_TOKENS = '8192';
const ids: string[] = [];
const text =
  'My external monitor flickers through the USB-C dock. It started this morning. I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens. Only I am affected. I can work on the laptop screen. This is not urgent. Please send this to IT support.';
const extracted = Extraction.parse({
  summary: 'External monitor flickers through USB-C dock',
  service: 'laptop',
  serviceQuote: 'external monitor',
  symptomQuote: 'My external monitor flickers through the USB-C dock.',
  impactQuote: 'Only I am affected.',
  urgencyQuote: 'This is not urgent.',
  deviceQuote: 'external monitor',
  startedQuote: 'It started this morning.',
  workaroundQuote: 'I can work on the laptop screen.',
  attemptedStepsQuotes: [
    'I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens.',
  ],
  supportRequestQuote: 'Please send this to IT support.',
  procedureAttemptedQuote:
    'I restarted the laptop, reconnected the cables, and checked the monitor input, but it still happens.',
  securityQuote: null,
  evidenceIds: ['test-message'],
});
async function run(
  data = extracted,
  action = 'message',
  message = text,
  fail = false,
) {
  const id = await intake(
    { text: message, action, submissionKey: randomUUID() },
    users[0],
  );
  ids.push(id);
  await processIntake(id, users[0], {
    retrieve: async () =>
      (
        await pool.query<Source>(
          "SELECT * FROM sources WHERE kind IN ('article','case')",
        )
      ).rows,
    extractLive: async (body, evidenceIds) => {
      if (fail) throw new Error('Model unavailable');
      return {
        data: validateExtraction({ ...data, evidenceIds }, body, evidenceIds),
        usage: { input: 100, output: 100 },
      };
    },
  });
  return getReport(id, users[0]);
}
after(async () => {
  for (const id of ids)
    await pool.query('DELETE FROM reports WHERE id=$1', [id]);
  await pool.end();
});
test('monitor report preserves known facts, skips repeated steps, and writes readable outbox payload', async () => {
  const r = await run();
  assert.equal(r.state, 'submission_pending');
  assert.equal(r.summary, extracted.summary);
  assert.equal(r.decision.team, 'Endpoint');
  assert.equal(r.decision.priority, 'normal');
  assert.equal(r.decision.facts.impact.value, 'Only I am affected.');
  assert.equal(r.decision.facts.urgency.value, 'This is not urgent.');
  assert.equal(r.decision.facts.workaround.value, extracted.workaroundQuote);
  assert.equal(r.decision.facts.device.value, 'external monitor');
  assert.equal(r.decision.facts.location.value, null);
  assert.equal(r.decision.reasoningEffort, 'high');
  assert.equal(r.offered.length, 0);
  assert.equal(
    r.attempted.length,
    0,
    'Reported attempts must not impersonate feedback on a Relay procedure',
  );
  const payload = (
    await pool.query(
      'SELECT payload FROM connector_operations WHERE report_id=$1',
      [r.id],
    )
  ).rows[0].payload;
  assert.equal(payload.summary, extracted.summary);
  assert.match(payload.description, /Impact: Only I am affected/);
  assert.match(
    payload.description,
    /Troubleshooting reported by the requester\nI restarted/,
  );
  assert.ok(payload.description.includes(text));
  assert.ok(!payload.description.includes('"evidenceIds"'));
  assert.ok(
    !r.decision.reasons.includes('provisional-priority-impact-unknown'),
  );
  await processIntake(r.id, users[0]);
  assert.equal(
    (
      await pool.query(
        'SELECT count(*)::int n FROM connector_operations WHERE report_id=$1',
        [r.id],
      )
    ).rows[0].n,
    1,
  );
});
test('explicit support request bypasses a procedure even without prior attempts', async () => {
  const r = await run({
    ...extracted,
    attemptedStepsQuotes: [],
    procedureAttemptedQuote: null,
  });
  assert.equal(r.state, 'submission_pending');
  assert.equal(r.offered.length, 0);
});
test('already attempted procedure skips repetition without an explicit support request', async () => {
  const r = await run({ ...extracted, supportRequestQuote: null });
  assert.equal(r.state, 'submission_pending');
  assert.equal(r.offered.length, 0);
});
test('an untried approved procedure remains available', async () => {
  const r = await run({
    ...extracted,
    supportRequestQuote: null,
    procedureAttemptedQuote: null,
    attemptedStepsQuotes: [],
  });
  assert.equal(r.state, 'awaiting_response');
  assert.equal(r.offered.length, 1);
  const description = draft(r, null).description;
  assert.match(description, /None confirmed in Relay/);
});
test('Send to support button also uses live extraction before queuing', async () => {
  const r = await run(
    { ...extracted, supportRequestQuote: null, procedureAttemptedQuote: null },
    'support',
  );
  assert.equal(r.state, 'submission_pending');
  assert.equal(r.summary, extracted.summary);
  assert.equal(r.decision.model, 'gpt-5.6-terra');
});
test('security restrictions survive a request to send directly to routine support', async () => {
  const message = text + ' I approved an MFA prompt I did not initiate.';
  const r = await run(
    {
      ...extracted,
      securityQuote: 'I approved an MFA prompt I did not initiate.',
    },
    'message',
    message,
  );
  assert.equal(r.state, 'operator_review');
  assert.equal(r.decision.team, 'Security Review');
  assert.equal(r.decision.visibility, 'restricted');
  assert.equal(r.decision.priority, 'urgent');
  assert.equal(r.decision.facts.priority.value, 'urgent');
});
test('model failure preserves the original report and queues general intake', async () => {
  const r = await run(extracted, 'message', text, true);
  assert.equal(r.state, 'submission_pending');
  assert.equal(r.decision.team, 'Service Desk');
  assert.equal(r.decision.model, 'live-failed');
  assert.equal(r.decision.facts.device.value, null);
  assert.ok(draft(r, null).description.includes(text));
});
test('new quote fields reject fabricated attempts and support requests', () => {
  for (const data of [
    { ...extracted, attemptedStepsQuotes: ['I replaced the dock'] },
    { ...extracted, supportRequestQuote: 'Escalate to the CEO' },
    { ...extracted, workaroundQuote: 'I have a spare monitor' },
  ])
    assert.throws(
      () => validateExtraction(data, text, ['test-message']),
      /unsupported evidence/,
    );
});
test('Terra uses configured high reasoning and a bounded output budget', () => {
  assert.deepEqual(modelSettings(), { effort: 'high', maxOutputTokens: 8192 });
});
