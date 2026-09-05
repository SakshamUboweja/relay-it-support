import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { authenticate, assertOrigin, demoCookie } from '../server/auth';
import { pool } from '../server/db';
import { liveClient } from '../server/model';
after(() => pool.end());
test('forged operator session cannot bypass HMAC', async () => {
  await assert.rejects(
    authenticate(
      new Request('http://127.0.0.1:3000', {
        headers: { cookie: 'relay_session=alex.invalid' },
      }),
    ),
    /Unauthorized/,
  );
});
test('signed demo session resolves operator role from database', async () => {
  const u = await authenticate(
    new Request('http://127.0.0.1:3000', {
      headers: { cookie: 'relay_session=' + demoCookie('alex') },
    }),
  );
  assert.equal(u.role, 'operator');
});
test('state-changing cross-origin requests are rejected', () => {
  assert.throws(
    () =>
      assertOrigin(
        new Request('http://127.0.0.1:3000', {
          headers: { origin: 'https://untrusted.invalid' },
        }),
      ),
    /Forbidden/,
  );
});
test('live mode never falls back to a demo identity or missing model key', async () => {
  const previous = process.env.APP_MODE,
    secret = process.env.SESSION_SECRET,
    key = process.env.OPENAI_API_KEY;
  try {
    process.env.APP_MODE = 'live';
    process.env.SESSION_SECRET =
      'test-random-value-for-live-identity-01234567890123456789';
    delete process.env.OPENAI_API_KEY;
    await assert.rejects(
      authenticate(new Request('http://127.0.0.1:3000')),
      /Unauthorized/,
    );
    assert.throws(() => liveClient(), /requires OPENAI/);
  } finally {
    process.env.APP_MODE = previous;
    process.env.SESSION_SECRET = secret;
    if (key) process.env.OPENAI_API_KEY = key;
  }
});
