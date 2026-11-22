# Audit report `aud-clean-handoff-topic`

**Outcome: CLEAN**

- Call: `call-clean-handoff-topic` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 1, mismatch 0, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `INFO`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `8b681550fa0e440e84b78622...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. HandoffCreated (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Handoff ticket exists as claimed (tkt_0001, priority normal).
- **What the caller heard (turn 2):** "I've created a ticket for the front desk about billing. Someone will call you back."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| priority | normal | normal | yes |
| topic | billing | billing | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The tickets change history for the call window was fully observed.

Evidence:
- `tickets` `tkt_0001`: Ticket created during the call. Compared the structured topic on the record. (journal #6; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Handoff fulfillment

A created ticket is a recorded promise, not a kept one. Each state below is backed by a journal row; missing evidence leaves the promise pending.

### Ticket `tkt_0001`: RECORDED (promise pending)

- The ticket was created and nothing further happened. Nobody is known to be calling back.
- Evidence `tickets` `tkt_0001`: Ticket created during the call (journal #6)

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-clean-handoff-topic-c1 (HandoffCreated): exact match -> tkt_0001
7. `verdict` call-clean-handoff-topic-c1 (HandoffCreated): VERIFIED
8. `fulfillment` ticket tkt_0001: recorded; the callback is still pending
9. `verdict` Outcome: CLEAN

## Seal

- content `49c47a0f32b18a4bc5c3be486a13b3bbdb864e92cb544cb41bfe5dbc5ab26248`
- chain `8b681550fa0e440e84b7862266564ca2237b8f84ed089fb6fe6415f339f76e25` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
