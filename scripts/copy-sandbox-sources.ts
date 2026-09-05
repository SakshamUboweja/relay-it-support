import 'dotenv/config';
import pg from 'pg';

// Run locally: SOURCE_DATABASE_URL is local, DATABASE_URL is the new cloud DB.
// Copy only explicitly synthetic public source material, never tickets or sessions.
if (!process.env.SOURCE_DATABASE_URL || !process.env.DATABASE_URL)
  throw new Error('Set SOURCE_DATABASE_URL and target DATABASE_URL.');
if (process.env.SOURCE_DATABASE_URL === process.env.DATABASE_URL)
  throw new Error('Source and target must be separate databases.');
const source = new pg.Client({
  connectionString: process.env.SOURCE_DATABASE_URL,
  connectionTimeoutMillis: 5000,
});
const target = new pg.Client({
  connectionString: process.env.DATABASE_URL,
  connectionTimeoutMillis: 5000,
});
try {
  await source.connect();
  await target.connect();
  const rows = (
    await source.query(
      "SELECT * FROM sources WHERE metadata->>'synthetic'='true' AND kind IN ('article','case') AND visibility='all' AND status='approved'",
    )
  ).rows;
  await target.query('BEGIN');
  let copied = 0;
  for (const s of rows) {
    const result = await target.query(
      'INSERT INTO sources(id,kind,title,body,service,visibility,location,status,metadata,embedding,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) ON CONFLICT(id) DO NOTHING',
      [
        s.id,
        s.kind,
        s.title,
        s.body,
        s.service,
        s.visibility,
        s.location,
        s.status,
        s.metadata,
        s.embedding,
        s.created_at,
        s.updated_at,
      ],
    );
    copied += result.rowCount ?? 0;
  }
  await target.query('COMMIT');
  console.log(
    JSON.stringify({ eligibleSyntheticSources: rows.length, inserted: copied }),
  );
} catch (error) {
  await target.query('ROLLBACK').catch(() => {});
  throw error;
} finally {
  await Promise.all([source.end(), target.end()]);
}
