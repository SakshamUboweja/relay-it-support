import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateDeployment } from '../scripts/deploy-env';
import { connector, JiraConnector } from '../server/connector';
import { pool } from '../server/db';

const mapping = JSON.parse(
  readFileSync(new URL('../config/jira.example.json', import.meta.url), 'utf8'),
);
const before = { ...process.env };
after(async () => {
  process.env = before;
  await pool.end();
});
function configure() {
  Object.assign(process.env, {
    APP_MODE: 'live',
    DATABASE_URL: 'postgresql://test:only@localhost/test',
    SESSION_SECRET: 'test-only-secret-with-more-than-32-characters',
    APP_ORIGIN: 'https://relay.example.com',
    OPENAI_API_KEY: 'test-only',
    OPENAI_MODEL: 'gpt-5.6-terra',
    OPENAI_REASONING_EFFORT: 'high',
    OPENAI_MAX_OUTPUT_TOKENS: '8192',
    OPENAI_EMBEDDING_MODEL: 'text-embedding-3-small',
    JIRA_EMAIL: 'test@example.com',
    JIRA_API_TOKEN: 'test-only',
    JIRA_CONFIG_JSON: JSON.stringify(mapping),
  });
}
test('deployment rejects accidental demo mode, insecure origin and missing secrets', () => {
  configure();
  assert.doesNotThrow(validateDeployment);
  process.env.APP_MODE = 'demo';
  assert.throws(validateDeployment, /APP_MODE=live/);
  configure();
  process.env.APP_ORIGIN = 'http://relay.example.com';
  assert.throws(validateDeployment, /HTTPS/);
  configure();
  delete process.env.JIRA_API_TOKEN;
  assert.throws(validateDeployment, /JIRA_API_TOKEN/);
});
test('cloud mapping is loaded from the environment without a local mapping file', async () => {
  configure();
  const provider = await connector();
  assert.ok(provider instanceof JiraConnector);
  process.env.JIRA_CONFIG_JSON = '{invalid JSON';
  await assert.rejects(connector(), /JIRA_CONFIG_JSON/);
});
