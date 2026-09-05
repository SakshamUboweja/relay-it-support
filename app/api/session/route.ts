import { NextResponse } from 'next/server';
import { z } from 'zod';
import { assertOrigin, demoCookie, validateSessionToken } from '@/server/auth';
import { pool, mode } from '@/server/db';
import { errorResponse } from '@/server/http';
export async function POST(req: Request) {
  try {
    assertOrigin(req);
    const body = z
      .object({
        userId: z.string().optional(),
        token: z.string().max(200).optional(),
      })
      .parse(await req.json());
    let value: string;
    if (mode() === 'demo') {
      const id = body.userId;
      if (
        !id ||
        !(await pool.query('SELECT 1 FROM users WHERE id=$1', [id])).rowCount
      )
        throw new Error('Unknown demo profile');
      value = demoCookie(id);
    } else {
      if (!body.token || !(await validateSessionToken(body.token)))
        throw new Error('Unauthorized');
      value = body.token;
    }
    const res = NextResponse.json({ ok: true });
    res.cookies.set('relay_session', value, {
      httpOnly: true,
      sameSite: 'strict',
      secure: process.env.APP_ORIGIN?.startsWith('https:'),
      path: '/',
      maxAge: 8 * 3600,
    });
    return res;
  } catch (e) {
    return errorResponse(e);
  }
}
