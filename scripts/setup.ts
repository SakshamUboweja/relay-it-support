import 'dotenv/config';
import { readFile } from 'node:fs/promises';
import { pool, mode } from '../server/db';
import { articles, users, catalog } from '../server/fixtures';
await pool.query(
  await readFile(
    new URL('../migrations/001_initial.sql', import.meta.url),
    'utf8',
  ),
);
if (mode() === 'live') {
  console.log(
    'Live schema migrated. Synthetic demo fixtures were not installed. Provision authorized users and approved sources separately.',
  );
  await pool.end();
  process.exit(0);
}
for (const u of users)
  await pool.query(
    'INSERT INTO users(id,name,role,location,device,scope) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING',
    [u.id, u.name, u.role, u.location, u.device, u.scope],
  );
for (const [i, a] of articles.entries())
  await pool.query(
    "INSERT INTO sources(id,kind,title,body,service,metadata,created_at,updated_at) VALUES($1,'article',$2,$3,$4,$5,now()-interval '30 days',now()) ON CONFLICT(id) DO NOTHING",
    [`kb-${i + 1}`, a[1], a[2], a[0], { synthetic: true, procedure: a[3] }],
  );
for (let i = 0; i < 100; i++) {
  const s = catalog[i % 5];
  const auth = s.id === 'vpn' && i % 2 === 0;
  const title = auth
    ? 'VPN authentication rejected after password change'
    : `${s.name} ${['connection failure', 'unavailable', 'intermittent failure', 'error', 'not responding'][Math.floor(i / 5) % 5]}`;
  await pool.query(
    "INSERT INTO sources(id,kind,title,body,service,metadata,created_at,updated_at) VALUES($1,'case',$2,$3,$4,$5,now()-interval '60 days',now()-interval '31 days') ON CONFLICT DO NOTHING",
    [
      `case-${i + 1}`,
      title,
      `${title}. Sanitized synthetic historical case. No inference about this employee's completed steps.`,
      s.id,
      {
        synthetic: true,
        reviewed: true,
        team: auth ? 'Identity & Access' : s.team,
      },
    ],
  );
}
for (const [id, status, scope, location, title, age] of [
  [
    'inc-atlas-sf',
    'open',
    'sf',
    'San Francisco',
    'Atlas is responding slowly',
    0,
  ],
  [
    'inc-atlas-london',
    'closed',
    'london',
    'London',
    'Atlas dashboard disruption',
    48,
  ],
  [
    'inc-vpn-restricted',
    'open',
    'operators',
    'San Francisco',
    'Private VPN investigation',
    0,
  ],
] as const)
  await pool.query(
    "INSERT INTO sources(id,kind,title,body,service,visibility,location,status,metadata,created_at,updated_at) VALUES($1,'incident',$2,$3,$4,$5,$6,$7,$8,now()-($9||' hours')::interval,now()-($9||' hours')::interval) ON CONFLICT DO NOTHING",
    [
      id,
      title,
      'Approved shared summary. Engineers are investigating intermittent loading failures. No individual reports are disclosed.',
      id.includes('atlas') ? 'atlas' : 'vpn',
      scope,
      location,
      status,
      { synthetic: true, curated: true },
      age,
    ],
  );
console.log(
  'Database ready: 3 profiles, 20 articles, 100 historical cases, 3 incident fixtures.',
);
await pool.end();
