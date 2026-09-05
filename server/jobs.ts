import { randomUUID } from 'node:crypto';
import { pool, transaction, mode } from './db';
import {
  connector,
  ConnectorError,
  type Connector,
  type Draft,
  type Ticket,
} from './connector';
import { enqueue, processIntake } from './workflow';
import { policy } from './policy';
import type { Operation, Report, User, Team } from './domain';
export async function saveTicket(reportId: string, t: Ticket) {
  await pool.query(
    "UPDATE reports SET provider_key=$2,provider_url=$3,provider_status=$4,provider_team=$5,provider_priority=$6,synced_at=now(),state=CASE WHEN lower($4) IN ('resolved','closed','done') THEN 'resolved' ELSE 'created' END,acknowledged_at=CASE WHEN lower($4) IN ('in progress','resolved','closed','done') THEN coalesce(acknowledged_at,now()) ELSE acknowledged_at END,updated_at=now() WHERE id=$1",
    [reportId, t.key, t.url, t.status, t.team, t.priority],
  );
}
export async function processOperation(id: string, provider?: Connector) {
  const lock = await pool.connect();
  let acquired = false;
  try {
    acquired = (
      await lock.query('SELECT pg_try_advisory_lock(hashtext($1)) locked', [id])
    ).rows[0].locked;
    if (!acquired) return;
    const op = (
      await pool.query<Operation>(
        'SELECT * FROM connector_operations WHERE id=$1',
        [id],
      )
    ).rows[0];
    if (!op || !['pending', 'unknown'].includes(op.state)) return;
    const reportMode = (
      await pool.query('SELECT mode FROM reports WHERE id=$1', [op.report_id])
    ).rows[0]?.mode;
    if (reportMode !== mode()) return;
    if (new Date(op.next_attempt_at) > new Date()) return;
    let api: Connector;
    try {
      api = provider ?? (await connector());
    } catch (e) {
      await fail(op, e);
      return;
    }
    try {
      let ticket: Ticket;
      const payload = op.payload as unknown as Draft;
      const report = (
        await pool.query<Report>('SELECT * FROM reports WHERE id=$1', [
          op.report_id,
        ])
      ).rows[0];
      if (op.kind === 'create') {
        if (report.provider_key) {
          ticket = await api.read(report.provider_key);
        } else if (op.state === 'unknown') {
          const found = await api.reconcile(payload.marker);
          if (found.length === 1) ticket = found[0];
          else {
            await pool.query(
              "UPDATE connector_operations SET attempts=attempts+1,state=$2,last_error=$3,next_attempt_at=now()+interval '30 seconds',updated_at=now() WHERE id=$1",
              [
                id,
                found.length > 1 || op.attempts >= 5 ? 'failed' : 'unknown',
                found.length > 1
                  ? 'Multiple correlation matches; operator review required.'
                  : 'No indexed correlation match yet. Creation will not be retried.',
              ],
            );
            if (found.length > 1 || op.attempts >= 5)
              await pool.query(
                "UPDATE reports SET state='operator_review' WHERE id=$1 AND provider_key IS NULL",
                [op.report_id],
              );
            return;
          }
        } else {
          await api.validate(payload);
          await pool.query(
            "UPDATE connector_operations SET state='unknown',attempts=attempts+1,updated_at=now() WHERE id=$1",
            [id],
          );
          ticket = await api.create(payload);
        }
        await transaction(async (db) => {
          await db.query(
            "UPDATE connector_operations SET state='succeeded',external_key=$2,last_error=NULL,updated_at=now() WHERE id=$1",
            [id, ticket.key],
          );
          await db.query(
            "UPDATE reports SET provider_key=$2,provider_url=$3,provider_status=$4,provider_team=$5,provider_priority=$6,synced_at=now(),state='created',updated_at=now() WHERE id=$1",
            [
              report.id,
              ticket.key,
              ticket.url,
              ticket.status,
              ticket.team,
              ticket.priority,
            ],
          );
          if (
            ticket.team !== payload.team ||
            ticket.priority !== payload.priority
          ) {
            const user = (
              await db.query<User>('SELECT * FROM users WHERE id=$1', [
                report.owner_id,
              ])
            ).rows[0];
            await enqueue(
              db,
              { ...report, provider_key: ticket.key },
              user,
              'update',
              { expected: { team: ticket.team, priority: ticket.priority } },
            );
          }
        });
      } else {
        if (!op.external_key)
          throw new ConnectorError(
            'Update operation has no saved provider key.',
          );
        ticket = await api.update(
          op.external_key,
          payload.team,
          payload.priority,
          op.payload.expected as
            | { team: string | null; priority: string | null }
            | undefined,
        );
        await saveTicket(op.report_id, ticket);
        await pool.query(
          "UPDATE connector_operations SET state='succeeded',last_error=NULL,updated_at=now() WHERE id=$1",
          [id],
        );
      }
    } catch (e) {
      if (e instanceof ConnectorError && e.ambiguous) {
        await pool.query(
          "UPDATE connector_operations SET state='unknown',last_error=$2,next_attempt_at=now()+interval '5 seconds',updated_at=now() WHERE id=$1",
          [id, e.message],
        );
      } else if (
        e instanceof ConnectorError &&
        (e.retryable || e.status === 429 || e.status >= 500) &&
        op.attempts < 4
      ) {
        await pool.query(
          "UPDATE connector_operations SET state=$4,attempts=attempts+1,last_error=$2,next_attempt_at=now()+($3||' seconds')::interval WHERE id=$1",
          [
            id,
            e.message,
            Math.max(e.retryAfter, 2 ** op.attempts),
            op.state === 'unknown' ? 'unknown' : 'pending',
          ],
        );
      } else await fail(op, e);
    }
  } finally {
    if (acquired)
      await lock.query('SELECT pg_advisory_unlock(hashtext($1))', [id]);
    lock.release();
  }
}
async function fail(op: Operation, e: unknown) {
  const error = e instanceof Error ? e.message : 'Connector failure';
  await pool.query(
    "UPDATE connector_operations SET state='failed',last_error=$2,updated_at=now() WHERE id=$1",
    [op.id, error],
  );
  if (op.kind === 'create')
    await pool.query(
      "UPDATE reports SET state='operator_review',updated_at=now() WHERE id=$1 AND provider_key IS NULL",
      [op.report_id],
    );
}
export async function reviewTimers() {
  await pool.query(
    "INSERT INTO operator_events(id,report_id,kind,detail,dedupe_key) SELECT gen_random_uuid(),id,'review-window-elapsed',jsonb_build_object('minutes',$1::int),'review:'||id FROM reports WHERE state IN ('created','submission_pending','operator_review') AND acknowledged_at IS NULL AND created_at<now()-($1||' minutes')::interval ON CONFLICT(dedupe_key) DO NOTHING",
    [policy.reviewMinutes],
  );
}
export async function syncRequests(provider?: Connector) {
  const api = provider ?? (await connector());
  for (const r of (
    await pool.query<Report>(
      "SELECT * FROM reports WHERE provider_key IS NOT NULL AND state<>'resolved' AND mode=$1 AND (synced_at IS NULL OR synced_at<now()-interval '60 seconds') LIMIT 50",
      [mode()],
    )
  ).rows) {
    try {
      await saveTicket(r.id, await api.read(r.provider_key!));
    } catch (e) {
      await pool.query(
        'INSERT INTO operator_events(id,report_id,kind,detail) VALUES($1,$2,$3,$4)',
        [
          randomUUID(),
          r.id,
          'sync-failed',
          { message: e instanceof Error ? e.message : 'Sync failed' },
        ],
      );
    }
  }
}
export async function resumeIntakes() {
  for (const r of (
    await pool.query<Report>(
      "SELECT * FROM reports WHERE state='processing' AND mode=$1 ORDER BY created_at LIMIT 10",
      [mode()],
    )
  ).rows) {
    const user = (
      await pool.query<User>('SELECT * FROM users WHERE id=$1', [r.owner_id])
    ).rows[0];
    await processIntake(r.id, user);
  }
}
