import 'dotenv/config';
import { pool, transaction } from '../server/db';
import { policy } from '../server/policy';
const ids = (
  await pool.query(
    "SELECT id FROM reports WHERE state='resolved' AND updated_at<now()-($1||' days')::interval",
    [policy.retentionDays],
  )
).rows.map((r) => r.id);
console.log(
  `${ids.length} resolved reports older than ${policy.retentionDays} days qualify for local retention deletion. Active/pending reports are preserved.`,
);
if (process.argv.includes('--apply'))
  await transaction(async (db) => {
    for (const id of ids)
      await db.query('DELETE FROM reports WHERE id=$1', [id]);
    console.log('Applied local retention. Provider requests are unchanged.');
  });
else console.log('Dry run. Use --apply only after reviewing retention policy.');
await pool.end();
