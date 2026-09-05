import { NextResponse } from 'next/server';
import { authenticate, assertOrigin } from '@/server/auth';
import { intake, processIntake } from '@/server/workflow';
import { errorResponse } from '@/server/http';
export async function POST(req: Request) {
  try {
    assertOrigin(req);
    const user = await authenticate(req);
    if (Number(req.headers.get('content-length') ?? 0) > 15000)
      throw new Error('Message too large');
    const id = await intake(await req.json(), user);
    await processIntake(id, user);
    return NextResponse.json({ id });
  } catch (e) {
    return errorResponse(e);
  }
}
