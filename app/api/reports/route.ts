import { requesterReport } from '@/server/privacy';
import { NextResponse } from 'next/server';
import { authenticate } from '@/server/auth';
import { pool } from '@/server/db';
import { getReport } from '@/server/workflow';
import { errorResponse } from '@/server/http';
import { z } from 'zod';
export const dynamic = 'force-dynamic';
export async function GET(req: Request) {
  try {
    const user = await authenticate(req),
      url = new URL(req.url),
      id = url.searchParams.get('id');
    if (id) {
      z.string().uuid().parse(id);
      const report = await requesterReport(await getReport(id, user), user);
      const messages = (
        await pool.query(
          'SELECT * FROM messages WHERE report_id=$1 ORDER BY created_at,id',
          [id],
        )
      ).rows;
      const ids = [
        ...report.decision.sources,
        ...report.offered,
        ...(report.related_id ? [report.related_id] : []),
      ];
      const sources = (
        await pool.query(
          "SELECT id,title,body,kind,service,updated_at,metadata FROM sources WHERE id=ANY($1) AND (visibility='all' OR visibility=$2 OR $3='operator')",
          [ids, user.scope, user.role],
        )
      ).rows;
      const operations =
        user.role === 'operator'
          ? (
              await pool.query(
                'SELECT * FROM connector_operations WHERE report_id=$1 ORDER BY created_at',
                [id],
              )
            ).rows
          : [];
      const events =
        user.role === 'operator'
          ? (
              await pool.query(
                'SELECT * FROM operator_events WHERE report_id=$1 ORDER BY created_at DESC',
                [id],
              )
            ).rows
          : [];
      return NextResponse.json(
        { report, messages, sources, operations, events },
        { headers: { 'Cache-Control': 'no-store' } },
      );
    }
    const all = url.searchParams.get('all') === '1';
    if (all && user.role !== 'operator') throw new Error('Forbidden');
    const reports = (
      await pool.query(
        'SELECT * FROM reports WHERE owner_id=$1 OR $2 ORDER BY updated_at DESC LIMIT 100',
        [user.id, all],
      )
    ).rows;
    return NextResponse.json(
      {
        reports: await Promise.all(
          reports.map((r) => requesterReport(r, user)),
        ),
      },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  } catch (e) {
    return errorResponse(e);
  }
}
