# Ticket verification and requester approval

Relay now uses two model roles in live mode: the intake agent extracts grounded facts, then an independent verifier checks the proposed Jira ticket against the original messages, explicit requester edits and current Jira fields. They use distinct instructions and structured outputs with the configured `gpt-5.6-terra`/high model. This is an application-orchestrated two-agent workflow; neither agent can approve or write to Jira. Demo verification is explicitly deterministic, without an AI call.

## Employee flow

1. Describe the problem. Requesting support prepares a draft; it does not create a ticket.
2. Review Jira's actual request-type fields inside Relay. Summary and description are filled from the intake, and other supported required fields appear when configured in Jira. Unknown required values must be supplied truthfully. Intended support team and application priority are shown separately; routing is applied after Jira confirms creation.
3. Edit fields and choose **Save and recheck**. The verifier checks the new version. Missing fields, unsupported claims, security concerns and verifier failures block approval. This is a consistency check, not a guarantee that every fact is correct.
4. Attach up to three PNG, JPEG, PDF, UTF-8 TXT or LOG files, up to 5 MiB each. Files are staged in Relay and are not sent to the verifier. If Jira requires an attachment, at least one is necessary.
5. Choose **Approve and submit**. Only the report owner can approve. The exact field values, version and file selection are frozen and audited, then the worker creates the Jira request. Open the resulting Jira link to inspect it. Attachment delivery has its own visible status.

The user selected review inside Relay. This is a form generated from Jira metadata, not an embedded Jira portal or browser automation. Actual Jira fields are discovered through the [request-type field API](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/). Attachments use Jira's [issue attachment API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-attachments/). Linked Atlassian Forms/Assets and complex picker fields are not generically implemented; unsupported required fields block submission rather than disappear.

## Guarantees and operational behavior

- An additive migration introduces versioned reviews and attachment storage. Existing tickets, sessions and already-authorized legacy operations remain intact.
- New reports require approval at API, outbox enqueue, database trigger and worker boundaries. The database gate also prevents an older worker from sending a new unapproved report during deployment overlap.
- Approval is atomic with the create operation. Stale versions and changed Jira schemas fail closed. Repeated approval produces one create operation; an ambiguous external create reconciles rather than blindly replaying.
- Attachments get a unique ID prefix in their Jira filename for exact filename/size reconciliation. An uncertain upload is checked, never automatically re-uploaded. File failure does not hide or recreate the ticket. The UI reports failure so the user/operator can inspect Jira.
- File bytes are stored in the existing private PostgreSQL database, with a 50 MiB per-owner and 500 MiB overall staging cap. Confirmed uploads clear the local bytes. Unsubmitted/failed local files expire after seven days. File signatures and UTF-8 validation are checked; this is not malware scanning or comprehensive DLP.
- The verifier adds one bounded model call per valid initial review or saved edit, with at most one retry and a 45-second per-call timeout. It adds no Railway service or framework dependency. Approval and attachment changes do not call the model again. Required-field failures can stop before a model call.

## Verification

144 automated tests pass, including 66 new tests for verifier grounding, real-field contracts, ownership, stale approvals, database guards, schema changes, file validation/quotas, upload reconciliation, edit sanitation and failure recovery. The original 120 deterministic policy cases still pass unchanged; this feature does not claim improved routing accuracy.

Local live browser/API test created **HELP-10** after explicit approval. Intake and independent verification used Terra/high. Browser edits required rechecking; a synthetic file was staged through the HTTP endpoint (Chrome extension file selection lacked file-URL permission). Before approval there were no Jira operations. Jira read-back matched the approved summary and description exactly, confirmed Endpoint/Medium, and showed one 205-byte attachment. Local file bytes were cleared after delivery. The browser retained the locked approved preview and attachment success after reload.
