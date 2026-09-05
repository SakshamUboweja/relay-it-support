import { pool } from './db';
import type { Report, User, Source } from './domain';
/** Re-resolve embedded evidence; historical operator snapshots never grant access. */
export async function requesterReport(report: Report, user: User) {
  if (user.role === 'operator') return report;
  const safe = structuredClone(report);
  const ids = [safe.decision.procedure?.id, safe.decision.related?.id].filter(
    Boolean,
  );
  const allowed = (
    await pool.query<Source>(
      "SELECT * FROM sources WHERE id=ANY($1) AND (visibility='all' OR visibility=$2) AND (kind<>'incident' OR location=$3)",
      [ids, user.scope, user.location],
    )
  ).rows;
  safe.decision.procedure =
    allowed.find((s) => s.id === safe.decision.procedure?.id) ?? null;
  safe.decision.related =
    allowed.find((s) => s.id === safe.decision.related?.id) ?? null;
  return safe;
}
