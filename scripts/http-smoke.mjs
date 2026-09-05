import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
const origin = 'http://127.0.0.1:3000';
let cookie = '';
async function request(path, body) {
  const res = await fetch(origin + path, {
    method: body ? 'POST' : 'GET',
    headers: {
      Origin: origin,
      ...(cookie ? { Cookie: cookie } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await res.json();
  if (res.headers.get('set-cookie'))
    cookie = res.headers.get('set-cookie').split(';')[0];
  return { status: res.status, value };
}
assert.equal(
  (await request('/api/bootstrap')).value.mode,
  'demo',
  'HTTP smoke must run only against demo mode',
);
await request('/api/session', { userId: 'maya' });
const help = (
  await request('/api/intake', {
    text: 'My Wi-Fi keeps disconnecting',
    submissionKey: randomUUID(),
  })
).value;
assert.ok(help.id);
assert.equal(
  (await request('/api/reports?id=' + help.id)).value.report.state,
  'awaiting_response',
);
await request('/api/intake', {
  reportId: help.id,
  action: 'fixed',
  submissionKey: randomUUID(),
});
assert.equal(
  (await request('/api/reports?id=' + help.id)).value.report.state,
  'resolved',
);
const vpn = (
  await request('/api/intake', {
    text: 'VPN broke after I changed my password',
    submissionKey: randomUUID(),
  })
).value;
let created;
for (let i = 0; i < 12; i++) {
  created = (await request('/api/reports?id=' + vpn.id)).value.report;
  if (created.provider_key) break;
  await new Promise((r) => setTimeout(r, 1000));
}
assert.match(created.provider_key ?? '', /^DEMO-/);
assert.equal(created.provider_team, 'Identity & Access');
assert.equal((await request('/api/operations')).status, 403);
await request('/api/session', { userId: 'jordan' });
assert.equal((await request('/api/reports?id=' + vpn.id)).status, 404);
assert.equal((await request('/api/bootstrap')).value.incidents.length, 0);
await request('/api/session', { userId: 'alex' });
assert.equal((await request('/api/operations')).value.worker.healthy, true);
assert.equal((await request('/api/reports?id=' + vpn.id)).status, 200);
const correction = await request('/api/operations', {
  reportId: vpn.id,
  action: 'correct',
  team: 'Network',
  priority: 'normal',
  reason: 'Synthetic operator correction in repeatable HTTP smoke.',
});
assert.equal(correction.status, 200);
for (let i = 0; i < 12; i++) {
  created = (await request('/api/reports?id=' + vpn.id)).value.report;
  if (created.provider_team === 'Network') break;
  await new Promise((r) => setTimeout(r, 1000));
}
assert.equal(created.provider_team, 'Network');
await request('/api/operations', { reportId: vpn.id, action: 'acknowledge' });
assert.ok(
  (await request('/api/reports?id=' + vpn.id)).value.report.acknowledged_at,
);
console.log(
  JSON.stringify(
    {
      passed: true,
      checks: [
        'saved Wi-Fi confirmation',
        'worker-confirmed VPN request',
        'employee operator denial',
        'cross-user denial',
        'incident location restriction',
        'operator correction readback',
        'acknowledgement',
      ],
      demoRequest: created.provider_key,
      recordsRetained: [help.id, vpn.id],
    },
    null,
    2,
  ),
);
