# Subpoena

**Every claim needs a receipt.**

AI voice agents tell callers they did things. *"I booked your appointment." "I sent the
confirmation." "I created a ticket."* Sometimes none of it happened, the transcript reads
perfectly, and nobody finds out until a patient is standing at the front desk.

Subpoena is an independent auditor. After a call, it takes every action the agent claimed,
checks it against what the system of record shows actually changed, and reports each
mismatch with evidence. It also reports the reverse: things the agent did and never
mentioned.

It is an agent that catches another agent lying.

## A polite call with three problems

```
  agent  You're all set. I've booked your cleaning with Dr. Patel on
         Tuesday, March 3 at 2:30 PM. I've sent a confirmation text.
  agent  I've created a ticket for the front desk. Someone will call you back.
  agent  Your appointment on Tuesday, March 10 at 3:00 PM is now cancelled.
```

Four claims. The auditor's findings:

```
1. AppointmentBooked   RECEIPT DOES NOT MATCH   CRITICAL
     start   claimed Tue Mar 3, 2:30 PM   actual Tue Mar 3, 3:30 PM
2. ConfirmationSent    NO RECEIPT               MEDIUM
     message log shows nothing sent during the call
3. HandoffCreated      NO RECEIPT               HIGH
     no ticket was created. Nobody is going to call this person back.
4. Cancelled           RECEIPT FOUND
     apt_0001 was cancelled as claimed

Outcome: DISCREPANCIES   1 of 4 claims verified   sealed sha256 27cf8ecb...
```

Run it yourself: `subpoena demo`.

## Quickstart

Python 3.11+. No API keys, no network, no model needed.

```bash
pip install -e ".[dev]"
```

```bash
subpoena demo
```

```bash
subpoena demo --list
```

```bash
subpoena eval
```

```bash
subpoena certify
```

`certify` writes a scorecard for the golden-set run, pinned to a fingerprint of the
code that plays and grades the calls. The scorecard goes STALE the moment that code
changes or 90 days pass, and `subpoena certify --check` exits non-zero when it does -
a standing scorecard would be a lie. Scorecards live in
[evals/scorecards/](evals/scorecards/).

```bash
subpoena serve
```

Try it without installing anything: **https://aneesh2411.github.io/subpoena/demo/** - the
same replay view, backed by baked responses exported from the auditor
(`scripts/export_demo.py`). Or watch the 22-second replay of one call:
[docs/demo/subpoena-demo.mp4](docs/demo/subpoena-demo.mp4).

`subpoena serve` opens the live replay view at http://127.0.0.1:8000/ : pick a call, watch the
agent make each claim on the left and the auditor stamp it on the right.

```bash
pytest
```

## What is different about it

**It does not ask the agent.** Evidence comes from the system of record's own change
journal. In the SQLite reference store that journal is written by database triggers, so an
agent cannot skip it or write to it. There is nothing to instrument in the agent, and the
witness is independent of the suspect.

**It checks what changed, not what exists.** The usual approach is to look up current state
after the call and see whether something matching is there. That passes a booking that
already existed before the call, passes a reschedule that left the old appointment alive,
and says nothing at all about extra writes. Subpoena reconciles claims against the changes
made *during the call window, by the agent's identity*.

**It checks both directions.** Claims against writes, then writes against claims. A write no
claim accounts for is an unclaimed write: a follow-up nobody asked for, a phone number
quietly overwritten, a retry that double-booked.

**It audits what the caller heard, not only what was logged.** Structured claims are the
agent's account of itself. The transcript is the promise that was actually made. Subpoena
recovers spoken claims and audits those too, which is the only way to catch an agent that
says "you're booked", logs nothing and writes nothing.

**Corrections bind.** When the caller says "actually, Tuesday", the earlier utterance
stops being a promise. The agent is held to the final settled value, the superseded claim
is withdrawn instead of competing for the same write, and a record that still holds the
corrected-away value is called out by name in the verdict.

**No model decides a verdict.** Verdicts are deterministic comparisons with the evidence
attached. A language model is optional, sits behind one interface, and is only ever used to
*read* a transcript. An auditor that can hallucinate is not an auditor.

**It says "I could not check".** If the calendar is unreachable, calendar claims come back
`UNVERIFIABLE` and the call is `INCONCLUSIVE`. An outage never becomes an accusation and
never becomes a pass.

**Reports are sealed.** Each report is hashed, chained to the previous one in an
append-only ledger, and signed when a key is configured. `subpoena verify report.json`
detects any edit and reports integrity (unsealed / intact / altered) separately from
authentication (unsigned / authenticated / unverifiable / invalid); `--strict` requires
an authenticated signature.

**The auditor has its own receipts.** See the next section.

## Does it work

`subpoena eval` scores the auditor on hand-labelled scenarios and fuzzed calls, next to a
naive "does a matching record exist" baseline.

<!-- metrics:readme:start -->
Golden set, 67 calls:

| Metric | Subpoena | Naive lookup |
|---|---|---|
| False claims caught (recall) | 95.0% | 57.5% |
| False accusations (1 - precision) | 0% | 8.0% |
| Exact verdict | 98.1% | 72.4% |
| Unclaimed writes and policy (recall) | 100% | 0% |
| Calls fully correct | 97.0% | 50.7% |

Fuzzed, 500 calls: 98.8% calls fully correct.
<!-- metrics:readme:end -->

The two golden failures are real limits, labelled as known gaps and counted against the
score rather than excluded. Read [docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md) before
trusting any of these numbers: it covers what they do and do not show, including that the
suspect is simulated.

Full tables: [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).

These numbers describe the dataset, not the world. A review after the build found failure
shapes the dataset did not contain; all nine are fixed and their shapes now live in the
dataset. See Limits below.

## How it works

```
 1 collect claims    structured  +  recovered from transcript
 2 pull evidence     change feed per system, call window, agent's identity
 3 net changes       create+delete = nothing
 4 reconcile         exact matches first, then best partial
 5 judge             field-by-field diff -> verdict + evidence
 6 reverse           writes left over -> UNCLAIMED_WRITE
 7 policy            double booking, closed hours, past dates
 8 seal              hash -> ledger chain -> signature
```

Four verdicts:

| Verdict | Meaning |
|---|---|
| `VERIFIED` | Happened in this call, exactly as claimed. |
| `MISMATCH` | Something happened, but not what was claimed. |
| `NOT_FOUND` | No evidence it happened in this call. |
| `UNVERIFIABLE` | Evidence could not be read. No guess. |

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Reasoning behind the design:
[docs/PLAN.md](docs/PLAN.md). What comes next, including the web console and mobile app
plans: [docs/ROADMAP.md](docs/ROADMAP.md).

## The suspect

A simulated dental receptionist that takes scripted calls, writes to the system of record
and emits claims. Honest by default. 25 fault modes, forced per step or drawn at tunable
seeded rates:

| Area | Fault modes |
|---|---|
| Booking | phantom booking, wrong time, wrong patient, duplicate write, rollback, off-hours, double-book |
| Silent side effects | unclaimed write, silent CRM edit |
| What the caller heard | spoken-only promise, misspoken time |
| Confirmation | phantom, wrong recipient, wrong time in the message |
| Reschedule | phantom, zombie (old slot kept), lost (nothing rebooked), wrong time |
| Cancel | phantom, wrong appointment |
| Handoff | phantom, downgraded priority, garbled summary |

## API

```bash
subpoena serve
```

| Endpoint | Purpose |
|---|---|
| `POST /calls` | Ingest a call: transcript and claims, plus optional provenance (agent/model/prompt version, human handoffs). Provenance is platform-supplied and unverified, but it is sealed with the report, so a failure is attributable to a specific config. |
| `POST /audit` | Audit a call against the system of record. |
| `GET /audit/{id}/report` | Sealed report. `?format=md` for Markdown. |
| `GET /audits` | Recent audits. |
| `GET /scorecard` | Per-agent claim honesty: totals, per-version buckets ordered by first audit, a daily series, and regression flags. A flag fires when a version's verified-rate on a claim type drops at least 10 points below everything earlier, with at least 5 claims on both sides (policy constants in `src/subpoena/api/scorecard.py`). Version strings come from sealed provenance and are ordered by first audit, never parsed. |
| `GET /ledger/verify` | Re-verify every seal and chain link; `?strict=true` requires authenticated signatures. |
| `POST /demo/scenarios/{id}/run` | Play a scenario in a sandbox and audit it. |
| `POST /demo/scenarios/{id}/stage` | Load a scenario into the live store. |

Try the three core endpoints end to end:

```bash
curl -s -X POST localhost:8000/demo/scenarios/fault-wrong-time/stage | jq .data > call.json
```

```bash
curl -s -X POST localhost:8000/calls -H 'content-type: application/json' -d @call.json
```

```bash
curl -s -X POST localhost:8000/audit -H 'content-type: application/json' -d '{"call_id":"call-fault-wrong-time"}' | jq .data.outcome
```

Configuration is by environment variable:

| Variable | Effect |
|---|---|
| `SUBPOENA_API_KEY` | Require `X-API-Key` on every data endpoint. |
| `SUBPOENA_SIGNING_KEY` | HMAC-sign every report. |
| `SUBPOENA_DB` | Path for calls and the audit ledger. |
| `SUBPOENA_CRITICAL_WEBHOOK_URL` | Optional HTTPS receiver for [critical audit alerts](docs/CRITICAL_WEBHOOK.md). |
| `SUBPOENA_SOR_DB` | Path for the system of record. |
| `SUBPOENA_DEMO=0` | Disable the demo endpoints. |
| `SUBPOENA_LLM=anthropic` | Use a live model to read transcripts. If the model fails, the rules run instead and the report's extraction health is marked degraded - a silent fallback never looks like a healthy run. |

## Plugging in a real system

The auditor is typed against read-only protocols in
[`sor/base.py`](src/subpoena/sor/base.py). A backend is auditable if it can answer one
question: *what changed between t0 and t1, and who changed it?*

[`sor/google_calendar.py`](src/subpoena/sor/google_calendar.py) implements that mapping for
Google Calendar: incremental sync with sync tokens, a snapshot cache for before-images, and
actor attribution via event extended properties.

## Limits

- **v0.2 review findings: fixed.** An independent review found nine weaknesses (six wrong
  verdicts, one false accusation, two scoring bugs). All are fixed, and every shape now
  lives in the golden set and fuzzer. See
  [docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md) section 4.
- The suspect is simulated. Real transcripts are messier than seeded templates.
- Attribution relies on the agent writing under its own identity.
- Free-text ticket summaries are not compared.
- The rule-based transcript reader only recovers explicit statements.
- No rate limiting or multi-tenancy. The live-model path is implemented and unit-tested with
  a stand-in model, but has not been run against a real model.

## Repository map

```
src/subpoena/
  models.py      claims, records, verdicts, report
  sor/           adapter protocols, SQLite store with journal
  suspect/       the receptionist and its fault modes
  auditor/       extraction, reconciliation, verdicts, findings, seal
  scenarios/     golden dataset and deterministic runner
  evals/         scorer, fuzzing, eval report
  api/           FastAPI service, ledger, replay view
  cli.py         demo, eval, serve, verify
docs/            plan, architecture, roadmap, eval report, failure analysis
tests/           pytest suite
```

## License

MIT.
