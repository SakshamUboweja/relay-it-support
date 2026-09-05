import { NextResponse } from 'next/server';
import { authenticate } from '@/server/auth';
import { pool, mode } from '@/server/db';
import { errorResponse } from '@/server/http';
import { catalog } from '@/server/fixtures';
export const dynamic = 'force-dynamic';
export async function GET(req: Request) {
  try {
    const user = await authenticate(req);
    const profiles =
      mode() === 'demo'
        ? (await pool.query('SELECT id,name,role FROM users ORDER BY role,id'))
            .rows
        : [];
    const incidents = (
      await pool.query(
        "SELECT id,title,body,service,updated_at,status FROM sources WHERE kind='incident' AND status='open' AND (visibility='all' OR visibility=$1) AND location=$2 AND updated_at>now()-interval '24 hours'",
        [user.scope, user.location],
      )
    ).rows;
    return NextResponse.json(
      { user, profiles, incidents, catalog, mode: mode() },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  } catch (e) {
    return errorResponse(e);
  }
}
