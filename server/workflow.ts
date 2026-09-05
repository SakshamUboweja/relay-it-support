import { sanitize } from './sanitize';
import { randomUUID, createHash } from 'node:crypto';
import type pg from 'pg';
import { pool, transaction, mode } from './db';
import { decide } from './policy';
import { retrieve } from './retrieval';
import { extractLive } from './model';
import {
  fact,
  inputSchema,
  type Report,
  type User,
  type Decision,
} from './domain';
import { draft } from './connector';
export async function enqueue(
  db: pg.PoolClient,
  report: Report,
  user: User,
  kind: 'create' | 'update' = 'create',
  extra: Record<string, unknown> = {},
) {
  const payload = { ...draft(report, user.external_account), ...extra };
  const key =
    kind === 'create'
      ? `create:${report.id}`
      : `update:${report.id}:${randomUUID()}`;
  await db.query(
    "INSERT INTO connector_operations(id,report_id,operation_key,kind,state,payload,payload_hash,external_key) VALUES($1,$2,$3,$4,'pending',$5,$6,$7) ON CONFLICT(operation_key) DO NOTHING",
    [
      randomUUID(),
      report.id,
      key,
      kind,
      payload,
      createHash('sha256').update(JSON.stringify(payload)).digest('hex'),
      kind === 'update' ? report.provider_key : null,
    ],
  );
}
export async function getReport(id: string, user: User) {
  const r = (
    await pool.query<Report>(
      "SELECT * FROM reports WHERE id=$1 AND (owner_id=$2 OR $3='operator')",
      [id, user.id, user.role],
    )
  ).rows[0];
  if (!r) throw new Error('Not found');
  return r;
}
export async function intake(raw: unknown, user: User) {
  const input = inputSchema.parse(raw);
  input.text = sanitize(input.text);
  return transaction(async (db) => {
    await db.query('SELECT pg_advisory_xact_lock(hashtext($1))', [
      user.id + input.submissionKey,
    ]);
    const receipt = (
      await db.query(
        'SELECT report_id FROM action_receipts WHERE owner_id=$1 AND submission_key=$2',
        [user.id, input.submissionKey],
      )
    ).rows[0];
    if (receipt) return receipt.report_id;
    let r: Report | undefined;
    if (input.reportId) {
      r = (
        await db.query<Report>(
          'SELECT * FROM reports WHERE id=$1 AND owner_id=$2 FOR UPDATE',
          [input.reportId, user.id],
        )
      ).rows[0];
      if (!r) throw new Error('Not found');
      if (r.mode !== mode())
        throw new Error('This report belongs to another application mode.');
    } else if (!['message', 'support'].includes(input.action))
      throw new Error('Start a report first.');
    if (
      r &&
      ['resolved', 'created', 'submission_pending', 'operator_review'].includes(
        r.state,
      ) &&
      input.action !== 'support'
    )
      throw new Error(
        'This report already has a saved outcome. Start a new conversation for a new issue.',
      );
    const id = r?.id ?? randomUUID(),
      messageId = randomUUID();
    let d: Decision;
    let state = r?.state ?? 'draft',
      reply = '',
      text = input.text;
    if (!r) {
      d = decide(text, [], user);
      r = {
        id,
        owner_id: user.id,
        summary: text.slice(0, 120),
        state: 'draft',
        decision: d,
        clarifications: 0,
        offered: [],
        attempted: [],
        provider_key: null,
        provider_url: null,
        provider_status: null,
        provider_team: null,
        provider_priority: null,
        synced_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        acknowledged_at: null,
        related_id: null,
        mode: mode(),
      };
      await db.query(
        'INSERT INTO reports(id,owner_id,submission_key,summary,state,decision,mode) VALUES($1,$2,$3,$4,$5,$6,$7)',
        [
          id,
          user.id,
          input.submissionKey,
          r.summary,
          r.state,
          r.decision,
          r.mode,
        ],
      );
    }
    if (text && input.action === 'support') {
      const existing = (
        await db.query(
          "SELECT body FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at",
          [id],
        )
      ).rows
        .map((m) => m.body)
        .join('\n');
      r.decision = decide(
        [existing, text].filter(Boolean).join('\n'),
        [],
        user,
        r.clarifications,
      );
      for (const f of Object.values(r.decision.facts))
        f.evidenceIds = f.evidenceIds.map((e) =>
          e === 'current-message' ? messageId : e,
        );
      await db.query('UPDATE reports SET decision=$2 WHERE id=$1', [
        id,
        r.decision,
      ]);
      await db.query('INSERT INTO decisions VALUES($1,$2,$3,now())', [
        randomUUID(),
        id,
        r.decision,
      ]);
    }
    if (text)
      await db.query(
        "INSERT INTO messages(id,report_id,role,body) VALUES($1,$2,'user',$3)",
        [messageId, id, text],
      );
    if (input.action === 'fixed') {
      if (!r.offered.length || r.state !== 'awaiting_response')
        throw new Error('No offered procedure is awaiting confirmation.');
      state = 'resolved';
      r.attempted = [...r.offered];
      reply =
        'Glad that helped. Your confirmed resolution is saved. No support ticket was created.';
    } else if (input.action === 'follow') {
      if (!r.decision.related || r.state !== 'related_suggested')
        throw new Error('No permitted related incident is available.');
      const allowed = (
        await db.query(
          "SELECT id FROM sources WHERE id=$1 AND status='open' AND (visibility='all' OR visibility=$2) AND location=$3",
          [r.decision.related.id, user.scope, user.location],
        )
      ).rowCount;
      if (!allowed)
        throw new Error('This advisory is no longer available to follow.');
      state = 'related_reported';
      r.related_id = r.decision.related.id;
      reply =
        'Your individual report is saved and linked to this advisory. Following here does not subscribe you to Jira notifications. You can still send a separate request to support.';
    } else if (input.action === 'support' || input.action === 'broken') {
      if (input.action === 'broken') {
        if (!r.offered.length || r.state !== 'awaiting_response')
          throw new Error('No offered procedure is awaiting feedback.');
        r.attempted = [...r.offered];
      }
      if (!r.provider_key) {
        state =
          r.decision.visibility === 'restricted'
            ? 'operator_review'
            : 'submission_pending';
        await enqueue(db, r, user);
        reply =
          state === 'operator_review'
            ? 'Your report is saved for restricted Security Review. External delivery is blocked until a restricted destination is configured.'
            : 'Your report is saved and queued for support. The request reference will appear when the provider confirms creation.';
      } else {
        state = 'created';
        reply = `Your request ${r.provider_key} is already saved.`;
      }
    } else {
      state = 'processing';
      reply =
        'Your message is saved. Checking approved help and service context…';
    }
    await db.query(
      'UPDATE reports SET state=$2,offered=$3,attempted=$4,related_id=$5,updated_at=now() WHERE id=$1',
      [
        id,
        state,
        JSON.stringify(r.offered),
        JSON.stringify(r.attempted),
        r.related_id,
      ],
    );
    await db.query(
      "INSERT INTO messages(id,report_id,role,body) VALUES($1,$2,'assistant',$3)",
      [randomUUID(), id, reply],
    );
    await db.query('INSERT INTO action_receipts VALUES($1,$2,$3)', [
      user.id,
      input.submissionKey,
      id,
    ]);
    return id;
  });
}
export async function processIntake(id: string, user: User) {
  const lock = await pool.connect();
  try {
    const locked = (
      await lock.query('SELECT pg_try_advisory_lock(hashtext($1)) locked', [
        'intake:' + id,
      ])
    ).rows[0].locked;
    if (!locked) return;
    const r = await getReport(id, user);
    if (r.state !== 'processing') return;
    const texts = (
      await pool.query(
        "SELECT id,body FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at",
        [id],
      )
    ).rows;
    const text = texts.map((m) => m.body).join('\n');
    let sources: Awaited<ReturnType<typeof retrieve>> = [];
    let modelError: string | null = null;
    try {
      sources = await retrieve(text, user);
    } catch {
      modelError =
        'Model/retrieval unavailable; known facts saved for general intake.';
    }
    let d = decide(text, sources, user, r.clarifications);
    if (mode() === 'live') {
      try {
        if (modelError) throw new Error(modelError);
        const result = await extractLive(
          text,
          texts.map((m) => m.id),
        );
        d.model = process.env.OPENAI_MODEL!;
        d.usage = result.usage;
        if (result.data.service && d.service !== result.data.service) {
          d.accepted = false;
          d.team = 'Service Desk';
          d.reasons.push('model-catalog-conflict');
        }
        if (result.data.securityQuote) {
          d.team = 'Security Review';
          d.escalation = 'security';
          d.priority = 'urgent';
          d.visibility = 'restricted';
          d.procedure = null;
          d.question = null;
          d.related = null;
          d.reasons.push('model-extracted-security-evidence');
        }
      } catch {
        modelError = 'Live model failed. Report saved for human intake.';
        d.model = 'live-failed';
        if (d.visibility !== 'restricted') {
          d.team = 'Service Desk';
          d.accepted = false;
        }
        d.procedure = null;
        d.question = null;
        d.reasons.push('model-unavailable');
      }
    }
    for (const f of Object.values(d.facts))
      f.evidenceIds = f.evidenceIds.flatMap((e) =>
        e === 'current-message' ? texts.map((m) => m.id) : [e],
      );
    if (mode() === 'live') {
      d.facts.device = fact<string>(null);
      d.facts.location = fact<string>(null);
    }
    let state = 'submission_pending',
      reply = 'Your report is saved and queued for support.';
    let clarification = r.clarifications;
    const offered = [...r.offered];
    if (d.visibility === 'restricted') {
      state = 'operator_review';
      reply =
        'This may be a security concern. I’ve saved it for restricted Security Review and stopped routine troubleshooting. External handoff awaits a verified restricted destination.';
    } else if (d.question && clarification < 1) {
      state = 'awaiting_clarification';
      clarification++;
      reply =
        d.question +
        ' You can also send the details you have directly to support.';
    } else if (d.related) {
      state = 'related_suggested';
      reply = `There’s an active advisory for ${d.related.title}. You can follow this advisory here or send a separate request to support. Your individual report is saved either way.`;
    } else if (d.procedure && offered.length === 0 && r.clarifications === 0) {
      state = 'awaiting_response';
      offered.push(d.procedure.id);
      reply = d.procedure.body;
    }
    if (modelError) reply = modelError + ' Your original message is preserved.';
    await transaction(async (db) => {
      const current = (
        await db.query<Report>('SELECT * FROM reports WHERE id=$1 FOR UPDATE', [
          id,
        ])
      ).rows[0];
      if (current.state !== 'processing') return;
      await db.query(
        'UPDATE reports SET state=$2,decision=$3,clarifications=$4,offered=$5,updated_at=now() WHERE id=$1',
        [id, state, d, clarification, JSON.stringify(offered)],
      );
      await db.query('INSERT INTO decisions VALUES($1,$2,$3,now())', [
        randomUUID(),
        id,
        d,
      ]);
      await db.query('INSERT INTO context_snapshots VALUES($1,$2,$3,now())', [
        randomUUID(),
        id,
        {
          simulated: mode() === 'demo',
          directory: mode() === 'demo' ? user : null,
          retrievedSourceIds: sources.slice(0, 5).map((s) => s.id),
          capturedAt: new Date().toISOString(),
        },
      ]);
      await db.query(
        "INSERT INTO messages VALUES($1,$2,'assistant',$3,now())",
        [randomUUID(), id, reply],
      );
      if (['submission_pending', 'operator_review'].includes(state))
        await enqueue(db, { ...current, state, decision: d, offered }, user);
    });
  } finally {
    await lock.query('SELECT pg_advisory_unlock(hashtext($1))', [
      'intake:' + id,
    ]);
    lock.release();
  }
}
