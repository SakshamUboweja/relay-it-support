import 'dotenv/config';
import { z } from 'zod';
import { pool, mode } from '../server/db';

try {
  if (mode() !== 'live')
    throw new Error('User provisioning requires APP_MODE=live.');
  const [id, name, role = 'employee'] = process.argv.slice(2);
  const user = z
    .object({
      id: z.string().min(1).max(100),
      name: z.string().min(1).max(200),
      role: z.enum(['employee', 'operator']),
    })
    .parse({ id, name, role });
  const result = await pool.query(
    "INSERT INTO users(id,name,role,location,device,scope) VALUES($1,$2,$3,'Unknown','Unknown',$4) ON CONFLICT(id) DO NOTHING RETURNING id",
    [
      user.id,
      user.name,
      user.role,
      user.role === 'operator' ? 'operators' : 'employees',
    ],
  );
  console.log(
    result.rowCount
      ? 'User provisioned. Issue a session separately with npm run session.'
      : 'User already exists; profile and permissions unchanged.',
  );
} finally {
  await pool.end();
}
