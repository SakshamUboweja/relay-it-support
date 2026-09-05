# Integration implementation notes — 2026-09-04

Read-only research from official current documentation; live account/model access and actual sandbox configuration remain unverified.

## JSM discovery, create, read

Discover `GET /rest/servicedeskapi/servicedesk`, then `GET /rest/servicedeskapi/servicedesk/{desk}/requesttype`, then `GET /rest/servicedeskapi/servicedesk/{desk}/requesttype/{type}/field`. Field metadata contains `requestTypeFields: [{fieldId, name, required, validValues:[{value,label,children}], jiraSchema, visible}]`, `canRaiseOnBehalfOf`, `canAddRequestParticipants`. Check normal and fallback types. Hidden fields are visible only to service-desk administrators. [Discovery reference](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/)

Application-generated minimal create payload:

```ts
const body = {
  serviceDeskId: config.deskId,
  requestTypeId: mapping.requestTypeId,
  raiseOnBehalfOf: authorizedEmployeeAccountId,
  isAdfRequest: false,
  requestFieldValues: {
    summary: draft.summary,
    description: `${draft.description}\n\nIntake correlation: ${marker}`,
  },
};
```

`POST /rest/servicedeskapi/request` returns 201 with `issueId`, `issueKey`, `_links.web`, `currentStatus`. Add `raiseOnBehalfOf` only when discovery permits; otherwise visibly block employee impersonation. Do not add participants. `GET /rest/servicedeskapi/request/{issueIdOrKey}` reads the request, but hides hidden fields. `GET .../{issueIdOrKey}/status` returns status history, newest first. The current docs also expose non-mutating `POST /rest/servicedeskapi/request/validate`, using the same payload: 200 with `valid:true`; 400 with `valid:false`, `fieldErrors`, `formErrors`, `errorMessages`. [Request reference](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/)

JSM plain descriptions accept strings. Single-select custom fields accept `{id: configuredOptionId}` or `{value: configuredOptionLabel}`; prefer discovered IDs. Authentication base URL differs: basic auth uses the configured HTTPS site; OAuth 3LO uses `https://api.atlassian.com/ex/jira/{cloudId}`. [Field formats and authentication](https://developer.atlassian.com/cloud/jira/service-desk/rest/intro/)

## Controlled routing updates

Use `GET /rest/api/3/issue/{key}/editmeta` for field availability/schema, `operations`, `allowedValues`. Update only allowlisted fields with `PUT /rest/api/3/issue/{key}`:

```ts
{ fields: {
  [config.supportTeamFieldId]: { id: teamOptionId },
  priority: { id: priorityId },
} }
```

Check for 204 or use `?returnIssue=true`. Read back `GET /rest/api/3/issue/{key}?fields=priority,customfield_...` to confirm. Jira v3 multiline description edits require ADF; avoid rewriting descriptions during routing updates. `editmeta` requires Browse Projects; editable fields require Edit Issues and applicable issue-security access. [Issue reference](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/)

Keep create and update operation records distinct. Once a key is saved, a failed routing update never queues another create. Before retrying an old update, compare current provider values to the expected prior snapshot; unexpected human edits require review. Security routing must be blocked locally until actual provider access restrictions are verified.

## Lost response reconciliation

Use `POST /rest/api/3/search/jql` with `{jql, fields:["description"], maxResults:100}` and follow `nextPageToken`. Search the configured project plus a generated alphanumeric correlation token in description. Verify exact marker occurrence and configured project on returned candidates; JQL text matching is candidate discovery, not exact identity. [Enhanced search reference](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)

Search is eventually consistent, potentially seconds to minutes. `reconcileIssues` accepts known numeric issue IDs (up to 50), so it cannot recover an ID lost with a create response. An empty immediate search does not authorize recreate. [Consistency documentation](https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/)

Recommended application contract: persist stable key, payload hash and pending operation before HTTP; atomic queue/outbox; crash after dispatch => unknown; network timeout or 5xx create => unknown; reconcile one exact match => succeeded; multiple => operator review; zero => bounded delayed reconciliation, then operator review. No generic HTTP retry wrapper around creation. Unique DB key and row locking prevent simultaneous deliveries. Persist reconciliation schedules; don't sleep inside transactions. Safe GET/set operations may use bounded exponential backoff with jitter, respecting `Retry-After` as minimum delay on 429/503. [Rate limits](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/)

## OpenAI Responses

Current official JavaScript helper:

```ts
import OpenAI from 'openai';
import { zodTextFormat } from 'openai/helpers/zod';
const client = new OpenAI({ apiKey, maxRetries: 0, timeout: 20000 });
const response = await client.responses.parse({
  model: config.model,
  store: false,
  max_output_tokens: config.maxOutputTokens,
  input: [{role:'system',content: extractionInstructions},
          {role:'user',content: JSON.stringify(sanitizedAllowedContext)}],
  text: { format: zodTextFormat(ExtractionSchema, 'intake_extraction') },
});
```

Use required nullable properties instead of optional properties; object schemas reject additional properties. Handle non-completed response status, `incomplete_details`, message content of type `refusal`, absent `output_parsed`, and schema failures. Structured format guarantees do not validate facts. [Official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

Application schema recommendation: each extracted fact has nullable value, origin enum, evidence IDs. After parsing, validate service/team/source IDs against input allowlists; reject asserted user/context facts without valid evidence. The provider receives no tools or credentials and cannot mutate tickets. Configuration owns model choice. A small opt-in preflight extraction establishes actual model access and schema support; model-list existence alone is insufficient. Preserve original intake if bounded calls fail. An embedding smoke test should check configured output dimensions against the database vector dimension. Never report deterministic demo extraction as measured live model results.

## Minimal contract tests

- Field discovery: hidden/required field absent, valid option, invalid option, employee mapping forbidden, fallback validated.
- Create 201 then update failure: saved key remains; retry touches only update.
- Timeout after provider accepted: marker found; exactly one create invocation.
- Empty first reconciliation then one later; two matches force review.
- Lost response with stale search does not resend create after restart.
- 429 schedules at least Retry-After; 401/403 produce actionable configuration failure.
- Responses completed/nullable facts, refusal, incomplete, invalid evidence ID, unknown model, network failure.
