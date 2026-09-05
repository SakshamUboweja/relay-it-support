import { NextResponse } from 'next/server';
import { randomUUID } from 'node:crypto';
import { authenticate, assertOrigin } from '@/server/auth';
import { pool, transaction, mode } from '@/server/db';
import { errorResponse } from '@/server/http';
import { correctionSchema, type User } from '@/server/domain';
import { getReport, enqueue } from '@/server/workflow';
import { connector } from '@/server/connector';
import { saveTicket } from '@/server/jobs';
export const dynamic = 'force-dynamic';
export async function GET(req: Request) {
  try {
    const u = await authenticate(req);
    if (u.role !== 'operator') throw new Error('Forbidden');
    const worker = (
      await pool.query(
        "SELECT last_seen, last_seen>now()-interval '15 seconds' healthy FROM worker_health WHERE id='main'",
      )
    ).rows[0] ?? { healthy: false };
    const counts = (
      await pool.query(
        'SELECT state,count(*)::int count FROM connector_operations GROUP BY state',
      )
    ).rows;
    return NextResponse.json({
      mode: mode(),
      worker,
      counts,
      jira:
        mode() === 'demo'
          ? 'Simulated'
          : process.env.JIRA_API_TOKEN
            ? 'Configured · sandbox verification pending'
            : 'Missing credentials',
      model:
        mode() === 'demo'
          ? 'Deterministic demo'
          : process.env.OPENAI_API_KEY
            ? 'Configured · verification pending'
            : 'Missing credentials',
      security: 'Local restricted review only',
      versions: { policy: 'northstar-1.0', prompt: 'intake-v1' },
    });
  } catch (e) {
    return errorResponse(e);
  }
}
export async function POST(req: Request) {
  try {
    assertOrigin(req);
    const u = await authenticate(req);
    if (u.role !== 'operator') throw new Error('Forbidden');
    const data = correctionSchema.parse(await req.json());
    const r = await getReport(data.reportId, u);
    if (r.mode !== mode())
      throw new Error('This report belongs to another application mode.');
    if (data.action === 'refresh') {
      if (!r.provider_key) throw new Error('No provider request to refresh');
      await saveTicket(r.id, await (await connector()).read(r.provider_key));
    } else
      await transaction(async (db) => {
        await db.query('SELECT id FROM reports WHERE id=$1 FOR UPDATE', [r.id]);
        if (data.action === 'acknowledge')
          await db.query(
            'UPDATE reports SET acknowledged_at=coalesce(acknowledged_at,now()) WHERE id=$1',
            [r.id],
          );
        if (data.action === 'correct') {
          if (!data.team || !data.priority || !data.reason?.trim())
            throw new Error(
              'Team, priority, and a correction reason are required.',
            );
          if (!r.provider_key)
            throw new Error('Correction requires a created provider request.');
          if (
            r.decision.visibility === 'restricted' ||
            data.team === 'Security Review'
          )
            throw new Error(
              'Restricted destination is unverified. Keep this report in local Security Review.',
            );
          const owner = (
            await db.query<User>('SELECT * FROM users WHERE id=$1', [
              r.owner_id,
            ])
          ).rows[0];
          await enqueue(db, r, owner, 'update', {
            team: data.team,
            priority: data.priority,
            expected: { team: r.provider_team, priority: r.provider_priority },
          });
        }
        if (data.action === 'retry') {
          await db.query(
            "UPDATE connector_operations SET state=CASE WHEN kind='create' AND attempts>0 AND external_key IS NULL THEN 'unknown' ELSE 'pending' END,next_attempt_at=now(),last_error=NULL WHERE report_id=$1 AND state='failed'",
            [r.id],
          );
        }
        await db.query(
          'INSERT INTO operator_events(id,report_id,actor_id,kind,detail) VALUES($1,$2,$3,$4,$5)',
          [randomUUID(), r.id, u.id, data.action, data],
        );
      });
    return NextResponse.json({ ok: true });
  } catch (e) {
    return errorResponse(e);
  }
}
