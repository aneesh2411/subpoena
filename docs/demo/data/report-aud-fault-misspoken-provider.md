# Audit report `aud-fault-misspoken-provider`

**Outcome: DISCREPANCIES**

- Call: `call-fault-misspoken-provider` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 3 | verified 2, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 67% | Highest severity: `LOW`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `e9162c08beae8db07e49f412...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Booking for Maya Chen on Tue Mar 3 2026, 2:30 PM EST exists exactly as claimed (apt_0001).
- **What the caller heard (turn 2):** "Okay, I've got you down with Dr. Kim on Tuesday, March 3 at 2:30 PM. I just texted you a confirmation."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Tue Mar 3 2026, 2:30 PM EST | Tue Mar 3 2026, 2:30 PM EST | yes |
| provider | Dr. Patel | Dr. Patel | yes |
| duration | 30 min | 30 min | yes |
| reason | cleaning | cleaning | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0001`: Appointment written by the agent during the call (journal #6; net: created)

### 2. ConfirmationSent (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Confirmation was sent to +15550101001 as claimed (msg_0001).
- **What the caller heard (turn 2):** "Okay, I've got you down with Dr. Kim on Tuesday, March 3 at 2:30 PM. I just texted you a confirmation."

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

### 3. AppointmentBooked (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `LOW`
- **Finding:** The agent claimed it booked Maya Chen on Tue Mar 3 2026, 2:30 PM EST, but wrote nothing during this call. An identical appointment (apt_0001) already existed: the caller's outcome holds, the claim does not.
- **What the caller heard (turn 2):** "Okay, I've got you down with Dr. Kim on Tuesday, March 3 at 2:30 PM."
- **Fix:** No patient impact. Flag the agent for taking credit for a write it did not make.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 1 agent write(s) to calendar in the call window. None creates this booking.
- `calendar` `apt_0001`: Already on the books before this call started (current state)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 2 structured claim(s); 2 recovered from the transcript, 1 of them never logged by the agent
6. `reconcile` call-fault-misspoken-provider-c1 (AppointmentBooked): exact match -> apt_0001
7. `reconcile` call-fault-misspoken-provider-c2 (ConfirmationSent): exact match -> msg_0001
8. `reconcile` call-fault-misspoken-provider-t1 (AppointmentBooked): no write corresponds
9. `verdict` call-fault-misspoken-provider-c1 (AppointmentBooked): VERIFIED
10. `verdict` call-fault-misspoken-provider-c2 (ConfirmationSent): VERIFIED
11. `verdict` call-fault-misspoken-provider-t1 (AppointmentBooked): NOT_FOUND
12. `verdict` Outcome: DISCREPANCIES

## Seal

- content `8bf5a4e89ddca9dfa44ca4a648085affef8ddef28e918c94910b539bb5916413`
- chain `e9162c08beae8db07e49f412b51e199dbf689d79ef09fbdfb6b41eafc7339e2c` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
