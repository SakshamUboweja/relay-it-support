import {
  createHash,
  createHmac,
  timingSafeEqual,
  randomBytes,
} from 'node:crypto';
import { pool, mode } from './db';
import type { User } from './domain';
const hash = (s: string) => createHash('sha256').update(s).digest('hex');
function secret() {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32)
    throw new Error('Set SESSION_SECRET to at least 32 random characters');
  if (mode() === 'live' && value.startsWith('local-demo'))
    throw new Error('Replace demo SESSION_SECRET before live use');
  return value;
}
export function demoCookie(id: string) {
  return `${id}.${createHmac('sha256', secret()).update(id).digest('hex')}`;
}
export async function authenticate(req: Request): Promise<User> {
  const raw = req.headers
    .get('cookie')
    ?.split(';')
    .map((x) => x.trim())
    .find((x) => x.startsWith('relay_session='))
    ?.slice(14);
  let id: string | undefined;
  if (mode() === 'demo') {
    if (!raw) id = 'maya';
    else {
      const [uid, sig] = raw.split('.'),
        expected = demoCookie(uid).split('.')[1];
      if (
        !sig ||
        sig.length !== expected.length ||
        !timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
      )
        throw new Error('Unauthorized');
      id = uid;
    }
  } else {
    secret();
    if (!raw) throw new Error('Unauthorized');
    id = (
      await pool.query(
        'SELECT user_id FROM sessions WHERE token_hash=$1 AND expires_at>now()',
        [hash(raw)],
      )
    ).rows[0]?.user_id;
  }
  if (!id) throw new Error('Unauthorized');
  const u = (await pool.query<User>('SELECT * FROM users WHERE id=$1', [id]))
    .rows[0];
  if (!u) throw new Error('Unauthorized');
  return u;
}
export function assertOrigin(req: Request) {
  const origin = req.headers.get('origin');
  if (!origin || origin !== (process.env.APP_ORIGIN ?? 'http://127.0.0.1:3000'))
    throw new Error('Forbidden origin');
}
export async function issueSession(id: string) {
  secret();
  const token = randomBytes(32).toString('hex');
  await pool.query(
    "INSERT INTO sessions VALUES($1,$2,now()+interval '8 hours')",
    [hash(token), id],
  );
  return token;
}
export async function validateSessionToken(token: string) {
  return (
    (
      await pool.query(
        'SELECT 1 FROM sessions WHERE token_hash=$1 AND expires_at>now()',
        [hash(token)],
      )
    ).rowCount === 1
  );
}
