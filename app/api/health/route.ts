import { pool } from '@/server/db';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const result = await pool.query(
      "SELECT to_regclass('public.reports') IS NOT NULL AS ready",
    );
    if (!result.rows[0]?.ready) throw new Error('Schema unavailable');
    return Response.json(
      { status: 'ok' },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  } catch {
    return Response.json(
      { status: 'unavailable' },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
