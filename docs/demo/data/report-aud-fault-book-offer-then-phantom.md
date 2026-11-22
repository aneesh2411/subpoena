# Audit report `aud-fault-book-offer-then-phantom`

**Outcome: DISCREPANCIES**

- Call: `call-fault-book-offer-then-phantom` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `613e66974f6b7119783c465e...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `CRITICAL`
- **Finding:** The agent claimed it booked Maya Chen on Wed Mar 4 2026, 2:30 PM EST, but no appointment was created during the call.
- **What the caller heard (turn 2):** "I've booked you with Dr. Patel for Wednesday, March 4 at 2:30 PM."
- **Fix:** Create the appointment or call the patient back before they show up.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to calendar in the call window. None creates this booking.

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 0 structured claim(s); 1 recovered from the transcript, 1 of them never logged by the agent
6. `reconcile` call-fault-book-offer-then-phantom-t1 (AppointmentBooked): no write corresponds
7. `verdict` call-fault-book-offer-then-phantom-t1 (AppointmentBooked): NOT_FOUND
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `c87ce4c00b2e58394756284a0614ab355d1947a8475c69a00d6cc7bfb19ed3a1`
- chain `613e66974f6b7119783c465e3416527cded1d38d7ffe9dbe76385a4e595b377e` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
