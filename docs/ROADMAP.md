# Roadmap

Where Subpoena is, what comes next, and the plan for the product surfaces: a web console
and a mobile app.

## Where it stands (v0.2)

Working today:

- Simulated receptionist with 26 fault modes.
- SQLite system of record with a trigger-written, append-only change journal.
- Auditor: claim verdicts with evidence, unclaimed writes, policy checks, sealed reports.
  Caller corrections bind into supersedes chains ([#55](https://github.com/aneesh2411/subpoena/issues/55)):
  the final settled value is judged, the superseded utterance is withdrawn, and a write
  that still holds the corrected-away value is named as evidence.
- Verdict revision journal: re-audits chain verdict changes into HMAC-signed receipts
  ([#129](https://github.com/aneesh2411/subpoena/issues/129)).
- 61 golden scenarios at 96.7% strict accuracy, 500-call fuzzing, naive baseline,
  ablations. Identity-labelled (v2 semantic) scoring runs over both the golden set
  and the fuzzer, beside the positional headline.
- Claim accuracy by claim type on the evals page ([#193](https://github.com/aneesh2411/subpoena/issues/193)).
- FastAPI service, CLI demo, single-page replay view with 62 sealed demo audits.
- 905 tests, lint clean.

Not done:

- No real CRM / SMS adapters (Google Calendar is implemented).
- The live-model transcript reader has not been run against a real model.
- An independent code review found open correctness issues. They are listed in
  [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md) section 4 and are the first item below.

## Done: make the verdicts trustworthy (v0.2, shipped)

Nothing else matters until a `VERIFIED` can be relied on. All nine review findings are
fixed, each with a regression test and, where the shape was missing, a golden scenario and
a fuzzer entry: wrong-verdict shapes
([#1](https://github.com/aneesh2411/subpoena/issues/1)-[#6](https://github.com/aneesh2411/subpoena/issues/6),
[#17](https://github.com/aneesh2411/subpoena/issues/17)), findings scored by code with
undefined precision reported as n/a
([#7](https://github.com/aneesh2411/subpoena/issues/7)), and API hardening
([#8](https://github.com/aneesh2411/subpoena/issues/8)). The review's reproductions all
fail to reproduce, and the eval still passes.

## Next: touch reality (v0.3)

1. **One real adapter**: Google Calendar reader with a snapshot cache for before-images.
   Re-run the golden set through it. **Done** ([#10](https://github.com/aneesh2411/subpoena/issues/10)):
   `sor/google_calendar.py` implements sync-token change feeds with snapshot
   before-images and actor attribution, and the golden set replays through it
   with identical verdicts and findings - except `fault-rollback`, pinned as a
   known delta: the API cannot distinguish a delete from a cancellation, so a
   create-then-delete inside the call window reads as create-then-cancel.
2. **Live-model transcript reader, measured.** Real, messy transcripts. Precision tracked
   separately from recall. A model that invents claims is worse than one that misses them.
3. **Overlapping calls.** Decide and test attribution when one agent identity handles two
   calls at once. **Done** ([#33](https://github.com/aneesh2411/subpoena/issues/33)):
   platform-stamped `agent:call-id` sessions are direct evidence; identity plus window
   alone is correlation, never attribution. Attribution strength is explicit
   (direct / sole / ambiguous / foreign), ambiguous matches cannot verify, and one
   mutation cannot independently verify two calls.
4. **Ingest from voice platforms.** Webhook receivers that turn a platform's end-of-call
   payload into a `CallRecord`.
5. **Fuzz multi-intent and multi-fault calls.** **Done**
   ([#38](https://github.com/aneesh2411/subpoena/issues/38)): about 30% of fuzzed calls
   chain two or three distinct intents with a fault drawn per step, so honest and
   dishonest requests mix inside one call. Same-kind chains and two whole-call faults
   stay excluded until reconcile pairs claims with writes by assignment instead of
   claim order ([#39](https://github.com/aneesh2411/subpoena/issues/39)).

## Later: make it a product (v0.4+)

1. **Remediation queue.** **Done** for the ops demo ([#191](https://github.com/aneesh2411/subpoena/issues/191)):
   ranked repair tasks with suggested fixes and resolution state.
2. **Agent scorecards over time.** **Done** for trends from committed eval history
   ([#192](https://github.com/aneesh2411/subpoena/issues/192)). Per-agent-version honesty
   trends are open ([#218](https://github.com/aneesh2411/subpoena/issues/218)).
3. **Alerting.** Critical findings to the on-call human within a minute.
4. **Multi-tenant auth, roles, retention policy** for transcripts and patient data.
5. **More claim types.** Payments taken: **Done** ([#205](https://github.com/aneesh2411/subpoena/issues/205)). Open: records updated ([#215](https://github.com/aneesh2411/subpoena/issues/215)), prescriptions refilled ([#216](https://github.com/aneesh2411/subpoena/issues/216)).
6. **Structured topics for handoffs.** **Done** ([#181](https://github.com/aneesh2411/subpoena/issues/181)):
   compare ticket content without a model on the verdict path.

## Web console plan

**Who it is for.** The clinic operations lead, and the engineer who owns the voice agent.
One wants to know which patients need a call back. The other wants to know what broke.

**Principle.** The console is a triage tool, not a dashboard. It opens on work to do.

Screens:

1. **Inbox.** Calls with discrepancies, worst first. Each row: caller, what went wrong in
   one line, severity, age. Clean calls are hidden by default.
2. **Call.** The replay view that exists today, plus a fix panel: "create the booking",
   "send the confirmation", "mark called back". Evidence stays side by side with the claim.
3. **Agent.** Honesty rate over time, by claim type and by agent version. Top failure
   modes. Links back into the inbox, filtered.
4. **Ledger.** Every sealed report, chain status, export for compliance.
5. **Connections.** Which systems of record are connected, last successful read, and
   which are currently unreadable.

Build order:

1. Inbox and call screen on top of the existing API (`/audits`, `/audit/{id}/report`).
2. Fix actions, which need write adapters and an approval step.
3. Agent trends, which need audits stored over time.
4. Ledger export and connections.

Stack: keep the server-rendered-data, small-client approach. React with TypeScript once
there is more than one screen with shared state; the API is already JSON with a stable
envelope. No model calls from the browser.

Not in the console: editing verdicts. A verdict is evidence. A human can add a note or
resolve a task, and that is recorded next to the verdict, not over it.

## Mobile app plan

**Who it is for.** The person on call for the clinic: the practice manager, evenings and
weekends. They are not at a desk and they need to act on one thing quickly.

**Why a separate app, and why second.** The console is where patterns are understood. The
phone is where a single critical finding gets fixed before the patient is affected. It only
becomes useful once alerting and remediation exist, so it follows the console.

Core flow, one screen each:

1. **Alert.** Push notification: "Maya Chen was told she is booked Tue 2:30 PM. No
   appointment exists." Critical and high severity only.
2. **Evidence.** What the agent said, what the record shows, one table. Same content as the
   console's call screen, cut down to the failing claim.
3. **Act.** Up to three buttons, chosen by the finding: call the patient, apply the
   suggested fix, hand to a colleague. Applying a fix asks for confirmation and shows what
   will be written.
4. **Done.** The action is recorded against the audit, and the finding is re-checked
   against the system of record. The task closes only when the re-check passes.

Also: a short list of open findings, and a quiet-hours setting.

Deliberately left out: scorecards, ledger browsing, scenario replay, settings beyond
notifications. Those belong on the console.

Technical choices:

- **React Native with Expo.** One codebase, shared types with the console, fast to ship.
  Nothing here needs native performance.
- **No patient data at rest on the device.** Fetch on open, keep in memory, clear on
  background. Notifications carry a finding id and severity; names appear only after the
  app is unlocked.
- **Biometric unlock**, short session lifetime, per-user API tokens.
- **Offline**: show the last alert list as stale, queue nothing. A fix applied from stale
  evidence is exactly the failure this product exists to catch.

What the backend needs first:

1. User accounts and device registration.
2. A notifications service keyed on severity.
3. Remediation endpoints with an approval record.
4. A re-audit endpoint for a single claim.

Milestones:

1. Read-only: alerts, evidence, open list.
2. Call-the-patient and hand-off actions.
3. Apply-fix actions with confirmation and re-check.

## Recommendation

v0.2 is shipped. Next: the first real adapter, then the web console inbox and call screen.
Start the mobile app only after remediation exists on the console: an alert with no way to
act on it trains people to ignore alerts.
