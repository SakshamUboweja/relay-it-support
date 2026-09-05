import 'dotenv/config';
import { randomUUID } from 'node:crypto';
import { pool, mode } from '../server/db';
import { connector, type Draft } from '../server/connector';
if (mode() !== 'live' || process.env.ALLOW_LIVE_SMOKE !== '1')
  throw new Error(
    'Opt-in only: use a dedicated authorized sandbox, APP_MODE=live and ALLOW_LIVE_SMOKE=1.',
  );
try {
  const api = await connector();
  await api.discover();
  const d: Draft = {
    summary: '[Relay sandbox smoke] synthetic VPN incident',
    description:
      'Synthetic test. Impact and urgency unknown. No real employee data.',
    team: 'Network',
    priority: 'normal',
    restricted: false,
    externalAccount: null,
    marker: 'relay' + randomUUID().replaceAll('-', ''),
  };
  await api.validate(d);
  console.log('Correlation marker (retain if response is lost):', d.marker);
  const created = await api.create(d);
  console.log('Created sandbox request:', created.key);
  const confirmed = await api.update(created.key, d.team, d.priority, {
    team: created.team,
    priority: created.priority,
  });
  console.log(
    JSON.stringify({ status: 'verified', ticket: confirmed }, null, 2),
  );
} finally {
  await pool.end();
}
