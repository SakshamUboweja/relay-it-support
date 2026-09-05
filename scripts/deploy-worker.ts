import { validateDeployment } from './deploy-env';

validateDeployment();
await import('./worker');
