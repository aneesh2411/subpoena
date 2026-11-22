# Audit report `aud-fault-spoken-only-with-confirmation`

**Outcome: DISCREPANCIES**

- Call: `call-fault-spoken-only-with-confirmation` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 2 | verified 0, mismatch 0, not found 2, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `3664804451979b0a992a0721...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `CRITICAL`
- **Finding:** The agent claimed it booked Maya Chen on Tue Mar 3 2026, 2:30 PM EST, but no appointment was created during the call.
- **What the caller heard (turn 2):** "I scheduled your cleaning with Dr. Patel for Tuesday, March 3 at 2:30 PM."
- **Fix:** Create the appointment or call the patient back before they show up.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to calendar in the call window. None creates this booking.

### 2. ConfirmationSent (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `MEDIUM`
- **Finding:** The agent claimed it sent an SMS confirmation to +15550101001, but the message log shows nothing sent during the call.
- **What the caller heard (turn 2):** "I've sent a confirmation text to the number on file."
- **Fix:** Send the confirmation the caller was promised.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The messages change history for the call window was fully observed.

Evidence:
- `journal` `messages 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to messages in the call window. None is this confirmation.

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 0 structured claim(s); 2 recovered from the transcript, 2 of them never logged by the agent
6. `reconcile` call-fault-spoken-only-with-confirmation-t1 (AppointmentBooked): no write corresponds
7. `reconcile` call-fault-spoken-only-with-confirmation-t2 (ConfirmationSent): no write corresponds
8. `verdict` call-fault-spoken-only-with-confirmation-t1 (AppointmentBooked): NOT_FOUND
9. `verdict` call-fault-spoken-only-with-confirmation-t2 (ConfirmationSent): NOT_FOUND
10. `verdict` Outcome: DISCREPANCIES

## Seal

- content `313b47c533638b76ea3fdfe84c0d7825df18f8351285ee1214818860c981b322`
- chain `3664804451979b0a992a0721b17e04a0018bf35a88354f21b5ebd8496eb90993` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
