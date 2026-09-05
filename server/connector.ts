import { join } from 'node:path';
import { z } from 'zod';
import { readFile } from 'node:fs/promises';
import { pool, mode } from './db';
import { teams, type Team, type Report } from './domain';
import { ticketDescription } from './ticket-description';
export type Ticket = {
  key: string;
  url: string | null;
  status: string;
  team: string | null;
  priority: string | null;
};
export class ConnectorError extends Error {
  constructor(
    message: string,
    public ambiguous = false,
    public retryAfter = 0,
    public status = 0,
    public retryable = false,
  ) {
    super(message);
  }
}
export type Draft = {
  summary: string;
  description: string;
  team: Team;
  priority: 'normal' | 'elevated' | 'urgent';
  restricted: boolean;
  externalAccount: string | null;
  marker: string;
};
export interface Connector {
  discover(): Promise<unknown>;
  validate(d: Draft): Promise<void>;
  create(d: Draft): Promise<Ticket>;
  read(key: string): Promise<Ticket>;
  update(
    key: string,
    team: Team,
    priority: Draft['priority'],
    expected?: { team: string | null; priority: string | null },
  ): Promise<Ticket>;
  reconcile(marker: string): Promise<Ticket[]>;
}
export class DemoConnector implements Connector {
  async discover() {
    return {
      mode: 'demo',
      provider: 'Simulated Jira',
      requiredFields: ['summary', 'description'],
      restricted: false,
    };
  }
  async validate(d: Draft) {
    if (!d.summary || !d.description)
      throw new ConnectorError('Summary and description are required.');
    if (d.restricted)
      throw new ConnectorError(
        'Restricted external destination is not configured. Saved for operator review.',
      );
  }
  async create(d: Draft) {
    await this.validate(d);
    const n = (await pool.query("SELECT nextval('demo_ticket_seq') n")).rows[0]
      .n;
    const r = await pool.query(
      'INSERT INTO demo_tickets(key,marker,summary,team,priority) VALUES($1,$2,$3,$4,$5) ON CONFLICT(marker) DO UPDATE SET marker=EXCLUDED.marker RETURNING *',
      [`DEMO-${n}`, d.marker, d.summary, d.team, d.priority],
    );
    if (process.env.DEMO_LOST_CREATE_RESPONSE === '1')
      throw new ConnectorError(
        'Simulated connection lost after provider accepted creation.',
        true,
      );
    return this.ticket(r.rows[0]);
  }
  ticket(r: Record<string, string>): Ticket {
    return {
      key: r.key,
      url: null,
      status: r.status,
      team: r.team,
      priority: r.priority,
    };
  }
  async read(key: string) {
    const r = (
      await pool.query('SELECT * FROM demo_tickets WHERE key=$1', [key])
    ).rows[0];
    if (!r) throw new ConnectorError('Demo request not found');
    return this.ticket(r);
  }
  async update(
    key: string,
    team: Team,
    priority: Draft['priority'],
    expected?: { team: string | null; priority: string | null },
  ) {
    const old = await this.read(key);
    if (
      expected &&
      (old.team !== expected.team || old.priority !== expected.priority) &&
      !(old.team === team && old.priority === priority)
    )
      throw new ConnectorError(
        'Provider values changed. Review the human update before correcting.',
      );
    if (process.env.DEMO_FAIL_UPDATE === '1')
      throw new ConnectorError('Simulated routing update failure.');
    const r = await pool.query(
      'UPDATE demo_tickets SET team=$2,priority=$3 WHERE key=$1 RETURNING *',
      [key, team, priority],
    );
    return this.ticket(r.rows[0]);
  }
  async reconcile(marker: string) {
    return (
      await pool.query('SELECT * FROM demo_tickets WHERE marker=$1', [marker])
    ).rows.map((r) => this.ticket(r));
  }
}
export const JiraConfig = z.object({
  site: z
    .string()
    .url()
    .refine(
      (v) => /^https:\/\/[^/]+\.atlassian\.net\/?$/.test(v),
      'Use a dedicated HTTPS atlassian.net sandbox site',
    ),
  projectKey: z.string().regex(/^[A-Z][A-Z0-9_]+$/),
  serviceDeskId: z.string().regex(/^\d+$/),
  requestTypeId: z.string().regex(/^\d+$/),
  generalRequestTypeId: z.string().regex(/^\d+$/),
  supportTeamFieldId: z.string().regex(/^customfield_\d+$/),
  teamOptions: z.record(z.enum(teams), z.string().min(1)),
  priorityIds: z.object({
    normal: z.string(),
    elevated: z.string(),
    urgent: z.string(),
  }),
  defaults: z.record(z.string(), z.unknown()).default({}),
  reporterMode: z.enum(['integration-account', 'on-behalf-of']),
  securityEnforced: z.literal(false).default(false),
});
export type JiraMapping = z.infer<typeof JiraConfig>;
export class JiraConnector implements Connector {
  constructor(
    public config: JiraMapping,
    private transport: typeof fetch = fetch,
  ) {}
  async request(
    path: string,
    method = 'GET',
    body?: unknown,
    create = false,
  ): Promise<any> {
    const email = process.env.JIRA_EMAIL,
      token = process.env.JIRA_API_TOKEN;
    if (!email || !token)
      throw new ConnectorError('JIRA_EMAIL and JIRA_API_TOKEN are missing.');
    let r: Response;
    try {
      r = await this.transport(this.config.site.replace(/\/$/, '') + path, {
        method,
        headers: {
          Authorization: `Basic ${Buffer.from(`${email}:${token}`).toString('base64')}`,
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(20000),
        redirect: 'error',
      });
    } catch {
      throw new ConnectorError(
        'Jira network failure or timeout.',
        create,
        0,
        0,
        true,
      );
    }
    if (!r.ok) {
      const retry = r.headers.get('retry-after');
      const seconds = retry
        ? Number.isFinite(Number(retry))
          ? Number(retry)
          : Math.max(0, (Date.parse(retry) - Date.now()) / 1000)
        : 0;
      throw new ConnectorError(
        r.status === 401 || r.status === 403
          ? 'Jira authentication or permissions need operator attention.'
          : `Jira ${r.status}: ${r.status === 400 ? 'Invalid provider fields; run discovery and check mappings.' : 'Provider request failed.'}`,
        create && r.status >= 500,
        seconds,
        r.status,
      );
    }
    return r.status === 204 ? {} : r.json();
  }
  async fields(typeId: string) {
    return this.request(
      `/rest/servicedeskapi/servicedesk/${this.config.serviceDeskId}/requesttype/${typeId}/field`,
    );
  }
  async discover() {
    const normal = await this.fields(this.config.requestTypeId),
      general = await this.fields(this.config.generalRequestTypeId);
    const missing = (f: any) =>
      (f.requestTypeFields ?? [])
        .filter(
          (x: any) =>
            x.required &&
            !['summary', 'description'].includes(x.fieldId) &&
            this.config.defaults[x.fieldId] === undefined,
        )
        .map((x: any) => x.fieldId);
    return {
      normal,
      general,
      missingNormal: missing(normal),
      missingGeneral: missing(general),
      reporterMode: this.config.reporterMode,
      security: 'blocked: no verified restricted destination',
    };
  }
  async payload(d: Draft) {
    if (d.restricted)
      throw new ConnectorError(
        'Security destination permissions are unverified. Restricted report stays local.',
      );
    let type = this.config.requestTypeId;
    let fields = await this.fields(type);
    const values: Record<string, unknown> = {
      ...this.config.defaults,
      summary: d.summary,
      description: `${d.description}\n\nIntake correlation: ${d.marker}`,
    };
    const missing = (f: any) =>
      (f.requestTypeFields ?? []).filter(
        (x: any) =>
          x.required &&
          (values[x.fieldId] === undefined ||
            values[x.fieldId] === null ||
            values[x.fieldId] === ''),
      );
    if (missing(fields).length) {
      type = this.config.generalRequestTypeId;
      fields = await this.fields(type);
    }
    if (missing(fields).length)
      throw new ConnectorError(
        `Required Jira fields have no truthful value/default: ${missing(fields)
          .map((x: any) => x.fieldId)
          .join(', ')}`,
      );
    for (const field of fields.requestTypeFields ?? []) {
      const value = values[field.fieldId];
      if (value !== undefined && field.validValues?.length) {
        const id =
          typeof value === 'object' && value !== null
            ? ((value as any).id ?? (value as any).value)
            : value;
        if (!field.validValues.some((v: any) => String(v.value) === String(id)))
          throw new ConnectorError(
            `Invalid configured value for ${field.fieldId}`,
          );
      }
    }
    const body: any = {
      serviceDeskId: this.config.serviceDeskId,
      requestTypeId: type,
      isAdfRequest: false,
      requestFieldValues: values,
    };
    if (this.config.reporterMode === 'on-behalf-of') {
      if (!fields.canRaiseOnBehalfOf || !d.externalAccount)
        throw new ConnectorError(
          'Raising on behalf requires provider permission and employee account mapping.',
        );
      body.raiseOnBehalfOf = d.externalAccount;
    }
    return body;
  }
  async validate(d: Draft) {
    await this.payload(d);
  }
  async create(d: Draft) {
    const body = await this.payload(d);
    const r = await this.request(
      '/rest/servicedeskapi/request',
      'POST',
      body,
      true,
    );
    if (!r.issueKey)
      throw new ConnectorError('Jira create response omitted its key.', true);
    try {
      return await this.read(r.issueKey);
    } catch {
      throw new ConnectorError(
        'Request created but its initial routing snapshot could not be read. Reconcile before continuing.',
        true,
      );
    }
  }
  safeUrl(url: unknown) {
    return typeof url === 'string' &&
      new URL(url, this.config.site).origin === new URL(this.config.site).origin
      ? new URL(url, this.config.site).href
      : null;
  }
  async read(key: string) {
    if (!/^[A-Z][A-Z0-9_]+-\d+$/.test(key))
      throw new ConnectorError('Invalid Jira key');
    const r = await this.request(`/rest/servicedeskapi/request/${key}`);
    const fields = (
      await this.request(
        `/rest/api/3/issue/${key}?fields=${this.config.supportTeamFieldId},priority`,
      )
    ).fields;
    const teamId = fields?.[this.config.supportTeamFieldId]?.id,
      priorityId = fields?.priority?.id;
    return {
      key: r.issueKey,
      url: this.safeUrl(r._links?.web),
      status: r.currentStatus?.status ?? 'Unknown',
      team:
        Object.entries(this.config.teamOptions).find(
          ([, v]) => v === teamId,
        )?.[0] ??
        teamId ??
        null,
      priority:
        Object.entries(this.config.priorityIds).find(
          ([, v]) => v === priorityId,
        )?.[0] ??
        priorityId ??
        null,
    };
  }
  async update(
    key: string,
    team: Team,
    priority: Draft['priority'],
    expected?: { team: string | null; priority: string | null },
  ) {
    const current = await this.read(key);
    if (current.team === team && current.priority === priority) return current;
    if (
      expected &&
      (current.team !== expected.team || current.priority !== expected.priority)
    )
      throw new ConnectorError(
        'Provider routing changed since this correction. Review before retrying.',
      );
    const meta = await this.request(`/rest/api/3/issue/${key}/editmeta`);
    for (const [field, id] of [
      [this.config.supportTeamFieldId, this.config.teamOptions[team]],
      ['priority', this.config.priorityIds[priority]],
    ]) {
      const m = meta.fields?.[field];
      if (
        !m ||
        !m.operations?.includes('set') ||
        !m.allowedValues?.some((v: any) => v.id === id)
      )
        throw new ConnectorError(
          `Field ${field} or configured option is not writable.`,
        );
    }
    await this.request(`/rest/api/3/issue/${key}`, 'PUT', {
      fields: {
        [this.config.supportTeamFieldId]: { id: this.config.teamOptions[team] },
        priority: { id: this.config.priorityIds[priority] },
      },
    });
    const actual = await this.read(key);
    if (actual.team !== team || actual.priority !== priority)
      throw new ConnectorError(
        'Provider has not confirmed the requested routing values.',
      );
    return actual;
  }
  async reconcile(marker: string) {
    if (!/^relay[a-f0-9]{32}$/.test(marker))
      throw new ConnectorError('Invalid correlation marker');
    let token: string | undefined;
    const matches: Ticket[] = [];
    for (let page = 0; page < 5; page++) {
      const r = await this.request('/rest/api/3/search/jql', 'POST', {
        jql: `project = "${this.config.projectKey}" AND description ~ "${marker}"`,
        fields: ['description'],
        maxResults: 100,
        ...(token ? { nextPageToken: token } : {}),
      });
      for (const issue of r.issues ?? []) {
        if (
          issue.key.startsWith(this.config.projectKey + '-') &&
          JSON.stringify(issue.fields?.description).includes(
            `Intake correlation: ${marker}`,
          )
        )
          matches.push(await this.read(issue.key));
      }
      token = r.nextPageToken;
      if (!token) return matches;
    }
    throw new ConnectorError(
      'Correlation search exceeded bounded pagination. Operator review required.',
    );
  }
}
export async function connector(): Promise<Connector> {
  if (mode() === 'demo') return new DemoConnector();
  let data: unknown;
  try {
    data = JSON.parse(
      process.env.JIRA_CONFIG_JSON ||
        (await readFile(
          /* turbopackIgnore: true */ join(
            process.cwd(),
            'config',
            'jira.json',
          ),
          'utf8',
        )),
    );
  } catch {
    throw new ConnectorError(
      'Set valid JIRA_CONFIG_JSON or create config/jira.json from config/jira.example.json.',
    );
  }
  return new JiraConnector(JiraConfig.parse(data));
}
export function draft(report: Report, externalAccount: string | null): Draft {
  return {
    summary: report.summary,
    description: ticketDescription(report),
    team: report.decision.team,
    priority: report.decision.priority,
    restricted: report.decision.visibility === 'restricted',
    externalAccount,
    marker: 'relay' + report.id.replaceAll('-', ''),
  };
}
