# Audit report `aud-fault-spoken-only`

**Outcome: DISCREPANCIES**

- Call: `call-fault-spoken-only` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `7417100ef55b671e5d38880e...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `CRITICAL`
- **Finding:** The agent claimed it booked Maya Chen on Tue Mar 3 2026, 2:30 PM EST, but no appointment was created during the call.
- **What the caller heard (turn 2):** "I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM."
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
6. `reconcile` call-fault-spoken-only-t1 (AppointmentBooked): no write corresponds
7. `verdict` call-fault-spoken-only-t1 (AppointmentBooked): NOT_FOUND
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `fb66af7f4e485c9582cd280cddf180b5f5f5234a3b194b1095b5a58bf9a18985`
- chain `7417100ef55b671e5d38880e7d5795b26ca42b1e4f6e0413c9cd174a5ad29cab` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
