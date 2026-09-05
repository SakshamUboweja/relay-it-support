import 'dotenv/config';
import { pool, mode } from '../server/db';
import { embed } from '../server/model';
if (mode() !== 'live')
  throw new Error(
    'Embedding indexing requires live mode and will incur configured model usage.',
  );
try {
  for (const s of (
    await pool.query(
      "SELECT id,title,body FROM sources WHERE kind IN ('article','case') AND status='approved' AND embedding IS NULL",
    )
  ).rows) {
    await pool.query('UPDATE sources SET embedding=$2::vector WHERE id=$1', [
      s.id,
      JSON.stringify(await embed(s.title + '\n' + s.body)),
    ]);
    console.log('Indexed ' + s.id);
  }
} finally {
  await pool.end();
}
