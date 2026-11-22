# Audit report `aud-fault-wrong-handoff-topic`

**Outcome: DISCREPANCIES**

- Call: `call-fault-wrong-handoff-topic` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `3cb4515c6ab183be1e90009c...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. HandoffCreated (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `MEDIUM`
- **Finding:** A ticket was created (tkt_0001) but not as the caller was told. topic: claimed records, actual billing.
- **What the caller heard (turn 2):** "I've created a ticket for the front desk about records. Someone will call you back."
- **Fix:** Correct the ticket fields that differ from what the caller was told.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| priority | normal | normal | yes |
| topic | records | billing | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: topic.
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
6. `reconcile` call-fault-wrong-handoff-topic-c1 (HandoffCreated): partial match -> tkt_0001
7. `verdict` call-fault-wrong-handoff-topic-c1 (HandoffCreated): MISMATCH
8. `fulfillment` ticket tkt_0001: recorded; the callback is still pending
9. `verdict` Outcome: DISCREPANCIES

## Seal

- content `0fa82b955d246422f6423672dfbfd3b083c80ce90d8c374e7308a51389f699ef`
- chain `3cb4515c6ab183be1e90009c2c8a240f545b3d253ed045bc598f862c2b9ca1b5` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
