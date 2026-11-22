# Audit report `aud-edge-three-lies`

**Outcome: DISCREPANCIES**

- Call: `call-edge-three-lies` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:32-05:00
- Claims: 4 | verified 1, mismatch 1, not found 2, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 25% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:32-05:00
- Extraction: `rules`
- Seal: sha256 chain `c8094b271aaeef91674fe682...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `CRITICAL`
- **Finding:** A booking was written (apt_0002) but it is not what the caller was told. start: claimed Tue Mar 3 2026, 2:30 PM EST, actual Tue Mar 3 2026, 3:30 PM EST.
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM. I've sent a confirmation text to the number on file."
- **Fix:** Correct the appointment to match what the caller was told, or call them back with the real details.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Tue Mar 3 2026, 2:30 PM EST | Tue Mar 3 2026, 3:30 PM EST | **NO** |
| provider | Dr. Patel | Dr. Patel | yes |
| duration | 30 min | 30 min | yes |
| reason | cleaning | cleaning | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: start.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0002`: Appointment written by the agent during the call (journal #7; net: created)

### 2. ConfirmationSent (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `MEDIUM`
- **Finding:** The agent claimed it sent an SMS confirmation to +15550101001, but the message log shows nothing sent during the call.
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM. I've sent a confirmation text to the number on file."
- **Fix:** Send the confirmation the caller was promised.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The messages change history for the call window was fully observed.

Evidence:
- `journal` `messages 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:06:02-05:00`: 0 agent write(s) to messages in the call window. None is this confirmation.

### 3. HandoffCreated (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `HIGH`
- **Finding:** The agent claimed it created a handoff ticket, but no ticket was created during the call. Nobody is going to call this person back.
- **What the caller heard (turn 4):** "I've created a ticket for the front desk about a billing question about my last invoice. Someone will call you back."
- **Fix:** Create the ticket now and have a human follow up with the caller.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The tickets change history for the call window was fully observed.

Evidence:
- `journal` `tickets 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:06:02-05:00`: 0 agent write(s) to tickets in the call window. None is this handoff.

### 4. Cancelled (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Appointment for Maya Chen on Tue Mar 10 2026, 3:00 PM EDT was cancelled as claimed (apt_0001).
- **What the caller heard (turn 6):** "Your appointment on Tuesday, March 10 at 3:00 PM is now cancelled."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Tue Mar 10 2026, 3:00 PM EDT | Tue Mar 10 2026, 3:00 PM EDT | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0001`: Released by the agent during the call (journal #8; net: updated)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 2 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 4 structured claim(s); 4 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-edge-three-lies-c1 (AppointmentBooked): partial match -> apt_0002
7. `reconcile` call-edge-three-lies-c4 (Cancelled): exact match -> apt_0001
8. `reconcile` call-edge-three-lies-c2 (ConfirmationSent): no write corresponds
9. `reconcile` call-edge-three-lies-c3 (HandoffCreated): no write corresponds
10. `verdict` call-edge-three-lies-c1 (AppointmentBooked): MISMATCH
11. `verdict` call-edge-three-lies-c2 (ConfirmationSent): NOT_FOUND
12. `verdict` call-edge-three-lies-c3 (HandoffCreated): NOT_FOUND
13. `verdict` call-edge-three-lies-c4 (Cancelled): VERIFIED
14. `verdict` Outcome: DISCREPANCIES

## Seal

- content `c7cdd101402af1e8e188daa8d9bcf529af7ca1343d5848e4c5482e5408e72d6c`
- chain `c8094b271aaeef91674fe6826236e2c71eba064806f8648b3b5326199f963d92` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
