import { spawn } from 'node:child_process';
import { validateDeployment } from './deploy-env';

validateDeployment();
const web = spawn(
  process.execPath,
  ['node_modules/next/dist/bin/next', 'start', '--hostname', '0.0.0.0'],
  { stdio: 'inherit' },
);
web.on('error', () => {
  process.exitCode = 1;
});
web.on('exit', (code) => {
  process.exitCode = code ?? 0;
});
process.on('SIGTERM', () => web.kill('SIGTERM'));
process.on('SIGINT', () => web.kill('SIGINT'));
