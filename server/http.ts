import { NextResponse } from 'next/server';
import { ZodError } from 'zod';
export function errorResponse(e: unknown) {
  const message =
    e instanceof ZodError
      ? e.issues.map((i) => i.message).join('; ')
      : e instanceof Error
        ? e.message
        : 'Request failed';
  const status =
    message === 'Unauthorized'
      ? 401
      : message.startsWith('Forbidden')
        ? 403
        : message === 'Not found'
          ? 404
          : e instanceof ZodError
            ? 400
            : 400;
  return NextResponse.json({ error: message }, { status });
}
