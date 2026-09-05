import 'dotenv/config';
import { issueSession } from '../server/auth';
import { pool, mode } from '../server/db';
if (mode() !== 'live')
  throw new Error(
    'Session issuance is for live mode. Demo uses seeded profiles.',
  );
const id = process.argv[2];
if (!id) throw new Error('Usage: npm run session -- USER_ID');
try {
  const token = await issueSession(id);
  console.log(
    'Paste this one-time-displayed, 8-hour session token into the Relay sign-in form. Treat it as a credential.\n' +
      token,
  );
} finally {
  await pool.end();
}
