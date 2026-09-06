# Proposed multi-agent MVP

Relay now uses intake and independent verification model roles before requester approval. The worker handles delivery and is not an AI agent. This document proposes a broader architecture; implemented review behavior is documented in TICKET_REVIEW.md.

1. **Intake agent:** owns the conversation, extracts cited facts, distinguishes affected service from device, and asks at most one necessary clarification.
2. **Triage agent:** receives the evidence packet and can choose bounded, read-only searches of approved knowledge and the service catalog. It proposes routing, priority and an applicable procedure with source IDs.
3. **Review agent:** independently checks the original message against the proposal for missing facts, fabricated attempts and unsupported routing. It can accept, request one revision, or send the case to human review.

The orchestrator persists each handoff, tool call, typed result, token cost and latency. Agents have distinct instructions and tool permissions; they can choose investigation/revision steps within fixed budgets. Deterministic code retains authority over authorization, security gates and idempotent Jira writes.

Start by invoking triage and review only on ambiguous cases; use the existing model initially and cap the extra calls. Do not add framework or hosting services just to obtain agent labels. Compare against the existing workflow and a corrected single-agent baseline on an untouched holdout, measuring routing, fact fidelity, security recall, latency and cost per completed request. Keep the added agents only if they improve measured outcomes.

First retained development failure: hosted HELP-7 describes Wi-Fi failure on a work laptop; equal Network/Endpoint scores trigger Service Desk fallback. A triage agent should distinguish the failed service from the affected device, and a reviewer should catch the mismatch. Fixing a deterministic scoring problem may be cheaper than extra model calls, so include that alternative in the comparison.

The API and worker use Python; the static React interface retains TypeScript. The independent ticket verifier is now implemented as a second model role before requester approval; see TICKET_REVIEW.md. It has a distinct prompt and constrained review output, without write tools. The broader autonomous triage/investigation/revision architecture above remains a proposal.
