import 'dotenv/config';
import { PgBoss } from 'pg-boss';
import { pool } from '../server/db';
import {
  processOperation,
  reviewTimers,
  syncRequests,
  resumeIntakes,
} from '../server/jobs';
const boss = new PgBoss(process.env.DATABASE_URL!);
boss.on('error', (e) => console.error('Worker queue:', e.message));
await boss.start();
await boss.createQueue('relay-connectors');
await boss.work<{ operationId: string }>(
  'relay-connectors',
  { batchSize: 1, pollingIntervalSeconds: 1 },
  async (jobs) => {
    for (const job of jobs) await processOperation(job.data.operationId);
  },
);
let running = false,
  lastSync = 0;
async function tick() {
  if (running) return;
  running = true;
  try {
    await pool.query(
      "INSERT INTO worker_health VALUES('main',now()) ON CONFLICT(id) DO UPDATE SET last_seen=now()",
    );
    await resumeIntakes();
    const ops = await pool.query(
      "SELECT id FROM connector_operations WHERE state IN ('pending','unknown') AND next_attempt_at<=now() LIMIT 50",
    );
    for (const op of ops.rows)
      await boss.send(
        'relay-connectors',
        { operationId: op.id },
        { singletonKey: op.id, singletonSeconds: 3, retryLimit: 2 },
      );
    await reviewTimers();
    if (Date.now() - lastSync > 60000) {
      lastSync = Date.now();
      await syncRequests();
    }
  } catch (e) {
    console.error('Worker tick:', e instanceof Error ? e.message : e);
  } finally {
    running = false;
  }
}
await tick();
const timer = setInterval(tick, 2000);
console.log(
  'Relay worker running: durable connector outbox, intake recovery, status sync, review timer.',
);
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  clearInterval(timer);
  await boss.stop();
  await pool.end();
  process.exit(0);
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
