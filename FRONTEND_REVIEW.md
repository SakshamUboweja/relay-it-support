# Jira request experience

Reviewed and updated September 5, 2026. The visual reference was the actual **Get IT help** form in the connected Jira Service Management portal, viewed in Chrome. This is Relay's review surface, not an embedded Jira page.

## Findings and changes

1. **Conversation to draft — improved.** Previously, the request form appeared below the entire conversation while unrelated service status occupied the sidebar. The review stage now has a dedicated two-column workspace: original issue and expandable history on the left, request form on the right. Regular troubleshooting and clarification retain the chat layout.
2. **Request review — improved.** Uses the portal's field hierarchy, compact labels, required asterisks, request-type card, bordered inputs, and attachment area. The requester is explicitly identified as the Relay account; the UI does not claim to change Jira's reporter. Request type and routing remain read only. Actual fields and required constraints still come from Jira. The description remains plain text, matching the existing API payload, rather than suggesting unsupported rich-text formatting.
3. **Edit and verify — checked.** Empty required fields show inline errors. Unsaved changes disable approval and file changes, survive polling, and require Save and recheck. Verification details and the included correlation reference are expandable. The reference remains in the approved payload.
4. **Attachments and submission — preserved.** Adds a one-file-at-a-time drop target using the existing bounded attachment endpoint, plus keyboard-accessible file browsing. The approval action follows all fields and files. There is no automatic submission or change to backend approval checks. Submitted requests show the real Jira reference, status, link, locked fields, and file-delivery state. Older tickets without a review snapshot retain their Jira link.

## Verification

- 144 Python tests passed, including approval, attachment, ownership and duplicate-delivery tests.
- TypeScript and production static build passed. Both changed TSX files pass Oxlint; existing effect/dependency warnings in the main page were addressed.
- Chrome: created a synthetic keyboard draft, cleared its required summary with keyboard input, confirmed approval/file changes were disabled, edited the summary, saved and reverified, and reopened the persisted draft after reload. No Jira submission was made for this design test.
- Chrome: inspected HELP-10's submitted form and existing attachment. Confirmed draft/submitted status distinctions and collapsible conversation/history.
- A pinned action bar initially covered a field; visual inspection caught this and it was replaced with an action area after the form.
- Screenshot evidence is local and ignored: `.local/ui-audit/01-relay-before.jpg`, `02-jira-reference.jpg`, `03-draft-desktop.png`. These contain only the scoped app/reference surfaces.

## Limits

Chrome's automation extension blocks file selection (`Not allowed`), so the actual chooser opens but automated file selection was not verified. Attachment API tests pass. Drag/drop handler is implemented with the same limits, but an OS file drag was not exercised.

Responsive styles stack the layout below 760px and wrap controls. The browser viewport override did not apply (DOM remained 1512px); no mobile screenshot is claimed. Keyboard-driven field edits and explicit labels were checked, but a full accessibility audit was not performed.

More company-specific Jira fields require configuration in the actual request type. This redesign does not invent fields or alter the Jira project, request type, provider payload, AI model, database schema, or hosting plan.
