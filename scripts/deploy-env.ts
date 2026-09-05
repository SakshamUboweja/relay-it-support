import { JiraConfig } from '../server/connector';
import { modelSettings } from '../server/model';

export function validateDeployment() {
  for (const key of [
    'DATABASE_URL',
    'SESSION_SECRET',
    'APP_ORIGIN',
    'OPENAI_API_KEY',
    'OPENAI_MODEL',
    'OPENAI_EMBEDDING_MODEL',
    'JIRA_EMAIL',
    'JIRA_API_TOKEN',
    'JIRA_CONFIG_JSON',
  ]) {
    if (!process.env[key])
      throw new Error(`Missing deployment variable: ${key}`);
  }
  if (process.env.APP_MODE !== 'live')
    throw new Error('Cloud deployment requires APP_MODE=live.');
  if (
    process.env.SESSION_SECRET!.length < 32 ||
    process.env.SESSION_SECRET!.startsWith('local-demo')
  )
    throw new Error('Cloud deployment requires a strong SESSION_SECRET.');
  const origin = new URL(process.env.APP_ORIGIN!);
  if (origin.protocol !== 'https:' || origin.origin !== process.env.APP_ORIGIN)
    throw new Error('APP_ORIGIN must be an HTTPS origin without a path.');
  try {
    JiraConfig.parse(JSON.parse(process.env.JIRA_CONFIG_JSON!));
  } catch {
    throw new Error('JIRA_CONFIG_JSON must contain a valid Jira mapping.');
  }
  modelSettings();
}
