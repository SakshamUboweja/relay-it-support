import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  JiraConnector,
  JiraConfig,
  ConnectorError,
  type Draft,
} from '../server/connector';
process.env.JIRA_EMAIL = 'test@example.invalid';
process.env.JIRA_API_TOKEN = 'stub-credential';
const mapping = JiraConfig.parse({
  site: 'https://test.atlassian.net',
  projectKey: 'HELP',
  serviceDeskId: '1',
  requestTypeId: '2',
  generalRequestTypeId: '3',
  supportTeamFieldId: 'customfield_10001',
  teamOptions: {
    'Service Desk': '10',
    'Identity & Access': '11',
    Network: '12',
    Endpoint: '13',
    'Business Applications': '14',
    'Security Review': '15',
  },
  priorityIds: { normal: '3', elevated: '2', urgent: '1' },
  reporterMode: 'integration-account',
});
const draft: Draft = {
  summary: 'VPN cannot connect',
  description: 'Impact unknown. No steps attempted.',
  team: 'Network',
  priority: 'normal',
  restricted: false,
  externalAccount: null,
  marker: 'relay' + 'a'.repeat(32),
};
const json = (v: unknown, status = 200, headers = {}) =>
  new Response(JSON.stringify(v), { status, headers });
const fields = {
  requestTypeFields: [
    { fieldId: 'summary', required: true },
    { fieldId: 'description', required: true },
  ],
  canRaiseOnBehalfOf: false,
};
test('network errors are retryable and dispatched creates remain ambiguous', async () => {
  const api = new JiraConnector(mapping, (async () => {
    throw new Error('network reset');
  }) as typeof fetch);
  await assert.rejects(
    api.request('/read'),
    (e: unknown) => e instanceof ConnectorError && e.retryable && !e.ambiguous,
  );
  await assert.rejects(
    api.request('/create', 'POST', {}, true),
    (e: unknown) => e instanceof ConnectorError && e.retryable && e.ambiguous,
  );
});
test('JSM create payload and actual initial fields are read; no generic issue create', async () => {
  const calls: { url: string; method: string; body: any }[] = [];
  const transport = async (url: any, init: any) => {
    calls.push({
      url: String(url),
      method: init.method,
      body: init.body ? JSON.parse(init.body) : null,
    });
    if (String(url).endsWith('/field')) return json(fields);
    if (init.method === 'POST')
      return json(
        {
          issueKey: 'HELP-1',
          _links: { web: 'https://test.atlassian.net/HELP-1' },
          currentStatus: { status: 'Open' },
        },
        201,
      );
    if (String(url).includes('/rest/api/3/issue/'))
      return json({
        fields: { customfield_10001: { id: '10' }, priority: { id: '3' } },
      });
    return json({
      issueKey: 'HELP-1',
      currentStatus: { status: 'Open' },
      _links: { web: 'https://test.atlassian.net/HELP-1' },
    });
  };
  const t = await new JiraConnector(mapping, transport as typeof fetch).create(
    draft,
  );
  assert.equal(t.team, 'Service Desk');
  assert.equal(t.priority, 'normal');
  const post = calls.find((c) => c.method === 'POST')!;
  assert.ok(post.url.endsWith('/rest/servicedeskapi/request'));
  assert.equal(post.body.serviceDeskId, '1');
  assert.equal(post.body.requestTypeId, '2');
  assert.ok(post.body.requestFieldValues.description.includes(draft.marker));
  assert.equal(post.body.raiseOnBehalfOf, undefined);
});
test('fallback required fields cannot be invented', async () => {
  const api = new JiraConnector(mapping, (async () =>
    json({
      requestTypeFields: [{ fieldId: 'customfield_required', required: true }],
    })) as typeof fetch);
  await assert.rejects(api.validate(draft), /no truthful value/);
});
test('missing normal field falls back to valid general intake', async () => {
  const api = new JiraConnector(mapping, (async (url) =>
    json(
      String(url).includes('/requesttype/2/')
        ? { requestTypeFields: [{ fieldId: 'asset', required: true }] }
        : fields,
    )) as typeof fetch);
  assert.equal((await api.payload(draft)).requestTypeId, '3');
});
test('on-behalf-of requires explicit permissions and employee mapping', async () => {
  const api = new JiraConnector(
    { ...mapping, reporterMode: 'on-behalf-of' },
    (async () => json(fields)) as typeof fetch,
  );
  await assert.rejects(
    api.validate({ ...draft, externalAccount: 'employee' }),
    /permission/,
  );
});
test('restricted reports never leave the local boundary', async () => {
  let called = false;
  const api = new JiraConnector(mapping, (async () => {
    called = true;
    return json({});
  }) as typeof fetch);
  await assert.rejects(
    api.create({ ...draft, restricted: true }),
    /Security destination/,
  );
  assert.equal(called, false);
});
test('timeout create is ambiguous and does not retry HTTP', async () => {
  let posts = 0;
  const api = new JiraConnector(mapping, (async (_u, init) => {
    if (init?.method === 'GET') return json(fields);
    posts++;
    throw new Error('timeout');
  }) as typeof fetch);
  await assert.rejects(
    api.create(draft),
    (e) => e instanceof ConnectorError && e.ambiguous,
  );
  assert.equal(posts, 1);
});
test('429 Retry-After is surfaced to durable retry policy', async () => {
  const api = new JiraConnector(mapping, (async () =>
    json({}, 429, { 'Retry-After': '45' })) as typeof fetch);
  await assert.rejects(
    api.discover(),
    (e) =>
      e instanceof ConnectorError &&
      e.retryAfter === 45 &&
      e.status === 429 &&
      !e.ambiguous,
  );
});
test('routing uses validated single-select values and confirms readback', async () => {
  let updated = false,
    put: any;
  const api = new JiraConnector(mapping, (async (url, init) => {
    if (String(url).endsWith('/editmeta'))
      return json({
        fields: {
          customfield_10001: {
            operations: ['set'],
            allowedValues: [{ id: '12' }],
          },
          priority: { operations: ['set'], allowedValues: [{ id: '3' }] },
        },
      });
    if (init?.method === 'PUT') {
      put = JSON.parse(init.body as string);
      updated = true;
      return new Response(null, { status: 204 });
    }
    if (String(url).includes('/rest/api/3/issue/'))
      return json({
        fields: {
          customfield_10001: { id: updated ? '12' : '10' },
          priority: { id: '3' },
        },
      });
    return json({ issueKey: 'HELP-1', currentStatus: { status: 'Open' } });
  }) as typeof fetch);
  const t = await api.update('HELP-1', 'Network', 'normal', {
    team: 'Service Desk',
    priority: 'normal',
  });
  assert.equal(t.team, 'Network');
  assert.deepEqual(put, {
    fields: { customfield_10001: { id: '12' }, priority: { id: '3' } },
  });
});
test('changed provider assignment is not overwritten by stale correction', async () => {
  const api = new JiraConnector(mapping, (async (url) =>
    String(url).includes('/rest/api/3/issue/')
      ? json({
          fields: { customfield_10001: { id: '13' }, priority: { id: '3' } },
        })
      : json({
          issueKey: 'HELP-1',
          currentStatus: { status: 'Open' },
        })) as typeof fetch);
  await assert.rejects(
    api.update('HELP-1', 'Network', 'normal', {
      team: 'Service Desk',
      priority: 'normal',
    }),
    /changed since/,
  );
});
test('correlation reconciliation filters approximate and wrong-project matches', async () => {
  let searched = false;
  const api = new JiraConnector(mapping, (async (url, init) => {
    if (String(url).endsWith('/search/jql')) {
      searched = true;
      return json({
        issues: [
          {
            key: 'OTHER-1',
            fields: { description: `Intake correlation: ${draft.marker}` },
          },
          { key: 'HELP-2', fields: { description: 'similar marker' } },
        ],
      });
    }
    throw new Error('Should not read non-matches');
  }) as typeof fetch);
  assert.deepEqual(await api.reconcile(draft.marker), []);
  assert.equal(searched, true);
});
