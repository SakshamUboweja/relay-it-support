import 'dotenv/config';
import pg from 'pg';
const g = globalThis as unknown as { relayPool?: pg.Pool };
export const pool =
  g.relayPool ??
  new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 8 });
g.relayPool = pool;
export async function transaction<T>(fn: (db: pg.PoolClient) => Promise<T>) {
  const db = await pool.connect();
  try {
    await db.query('BEGIN');
    const r = await fn(db);
    await db.query('COMMIT');
    return r;
  } catch (e) {
    await db.query('ROLLBACK');
    throw e;
  } finally {
    db.release();
  }
}
export const mode = () => {
  const m = process.env.APP_MODE ?? 'demo';
  if (m !== 'demo' && m !== 'live')
    throw new Error('APP_MODE must be demo or live');
  return m;
};
