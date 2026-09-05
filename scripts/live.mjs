import 'dotenv/config';
import { spawn } from 'node:child_process';

if (process.env.APP_MODE !== 'live') {
  throw new Error('npm run live requires APP_MODE=live in .env.');
}

// Child processes inherit NODE_OPTIONS loaded from .env before Node starts.
const worker = spawn('npm', ['run', 'worker'], { stdio: 'inherit' });
const web = spawn('npm', ['run', 'dev'], { stdio: 'inherit' });
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  worker.kill('SIGTERM');
  web.kill('SIGTERM');
}
for (const child of [worker, web]) {
  child.on('error', () => stop(1));
  child.on('exit', (code) => stop(code ?? 0));
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
