import { pool, mode } from './db';
import type { Source, User } from './domain';
import { embed } from './model';
export async function retrieve(text: string, user: User) {
  const visibility = [
    user.scope,
    'all',
    ...(user.role === 'operator' ? ['sf', 'london', 'operators'] : []),
  ];
  const lexical = (
    await pool.query<Source>(
      "SELECT * FROM sources WHERE visibility=ANY($1) AND created_at<=now() AND ((kind IN ('article','case') AND status='approved') OR kind='incident') ORDER BY ts_rank(to_tsvector('english',title||' '||body),plainto_tsquery('english',$2)) DESC,id LIMIT 125",
      [visibility, text],
    )
  ).rows;
  if (mode() === 'demo') return lexical;
  const vector = await embed(text);
  const semantic = (
    await pool.query<Source>(
      "SELECT * FROM sources WHERE visibility=ANY($1) AND kind IN ('article','case') AND status='approved' AND created_at<=now() AND embedding IS NOT NULL ORDER BY embedding <=> $2::vector LIMIT 5",
      [visibility, JSON.stringify(vector)],
    )
  ).rows;
  return [...new Map([...semantic, ...lexical].map((s) => [s.id, s])).values()];
}
