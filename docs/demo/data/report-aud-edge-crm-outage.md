# Audit report `aud-edge-crm-outage`

**Outcome: INCONCLUSIVE**

- Call: `call-edge-crm-outage` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 2 | verified 1, mismatch 0, not found 0, unverifiable 1
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `d64a86253eec59ed5d8ba8d8...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): COULD NOT CHECK

- **Status:** `UNVERIFIABLE` | **Severity:** `MEDIUM`
- **Finding:** Could not be checked: crm could not be read. No verdict is better than a guessed one.
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM. I've sent a confirmation text to the number on file."
- **Fix:** Re-run the audit when the system of record is reachable.

Checks:
- `existence`: n/a. crm could not be read

### 2. ConfirmationSent (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Confirmation was sent to +15550101001 as claimed (msg_0001).
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM. I've sent a confirmation text to the number on file."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| recipient | +15550101001 | +15550101001 | yes |
| channel | sms | sms | yes |
| delivery | sent | sent | yes |
| stated_time | Tue Mar 3 2026, 2:30 PM EST | Tue Mar 3 2026, 2:30 PM EST | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The messages change history for the call window was fully observed.

Evidence:
- `messages` `msg_0001`: Message logged during the call (journal #7; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Coverage gaps

Some evidence could not be fully observed for this call. Every claim a gap touches stays UNVERIFIABLE, never a forced pass or fail.

- `crm` (unavailable): crm adapter is unavailable. Affects claim(s): `call-edge-crm-outage-c1`.

## Audit trail

1. `evidence` crm: UNAVAILABLE (crm adapter is unavailable). Will not guess.
2. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 2 structured claim(s); 2 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-edge-crm-outage-c2 (ConfirmationSent): exact match -> msg_0001
7. `verdict` call-edge-crm-outage-c1 (AppointmentBooked): UNVERIFIABLE
8. `verdict` call-edge-crm-outage-c2 (ConfirmationSent): VERIFIED
9. `reconcile` calendar: unclaimed-write check skipped, a claim that may own these writes could not be verified
10. `coverage` crm unavailable: 1 claim(s) capped at UNVERIFIABLE
11. `verdict` Outcome: INCONCLUSIVE

## Seal

- content `4bf8017d61ddfbd76e45ab5315cb933b48a9bc2d7b042a1f36c5a34ae3246b52`
- chain `d64a86253eec59ed5d8ba8d898080a0ca0fe2f1c7b34b6c502e1e13e46d37abe` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
