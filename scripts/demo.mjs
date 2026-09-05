import { existsSync, copyFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
if (!existsSync('.env')) copyFileSync('.env.example', '.env');
await import('dotenv/config');
if (process.env.APP_MODE === 'live')
  throw new Error(
    'npm run demo requires APP_MODE=demo; live mode uses separate setup/start/worker commands.',
  );
const setup = spawnSync('npm', ['run', 'setup'], { stdio: 'inherit' });
if (setup.status !== 0) {
  console.error(
    'Start PostgreSQL first: docker compose up -d --wait db. See README for native PostgreSQL.',
  );
  process.exit(1);
}
const worker = spawn('npm', ['run', 'worker'], { stdio: 'inherit' }),
  web = spawn('npm', ['run', 'dev'], { stdio: 'inherit' });
let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  worker.kill('SIGTERM');
  web.kill('SIGTERM');
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
web.on('exit', (code) => {
  stop();
  process.exitCode = code ?? 0;
});
