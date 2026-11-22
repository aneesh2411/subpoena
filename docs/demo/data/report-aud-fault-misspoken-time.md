# Audit report `aud-fault-misspoken-time`

**Outcome: DISCREPANCIES**

- Call: `call-fault-misspoken-time` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 4 | verified 2, mismatch 0, not found 2, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 50% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `7fb1e034a750f61a885ac4c0...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Booking for Maya Chen on Tue Mar 3 2026, 2:30 PM EST exists exactly as claimed (apt_0001).
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Wednesday, March 4 at 2:30 PM. I just texted you a confirmation."

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
- **What the caller heard (turn 2):** "You're all set. I've booked your cleaning with Dr. Patel on Wednesday, March 4 at 2:30 PM. I just texted you a confirmation."

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

- **Status:** `NOT_FOUND` | **Severity:** `CRITICAL`
- **Finding:** The agent claimed it booked Maya Chen on Wed Mar 4 2026, 2:30 PM EST, but no appointment was created during the call.
- **What the caller heard (turn 2):** "I've booked your cleaning with Dr. Patel on Wednesday, March 4 at 2:30 PM."
- **Fix:** Create the appointment or call the patient back before they show up.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 1 agent write(s) to calendar in the call window. None creates this booking.

### 4. ConfirmationSent (said to caller, never logged): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `MEDIUM`
- **Finding:** The agent claimed it sent an SMS confirmation to +15550101001, but the message log shows nothing sent during the call.
- **What the caller heard (turn 2):** "I just texted you a confirmation."
- **Fix:** Send the confirmation the caller was promised.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The messages change history for the call window was fully observed.

Evidence:
- `journal` `messages 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 1 agent write(s) to messages in the call window. None is this confirmation.

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 2 structured claim(s); 2 recovered from the transcript, 2 of them never logged by the agent
6. `reconcile` call-fault-misspoken-time-c1 (AppointmentBooked): exact match -> apt_0001
7. `reconcile` call-fault-misspoken-time-c2 (ConfirmationSent): exact match -> msg_0001
8. `reconcile` call-fault-misspoken-time-t1 (AppointmentBooked): no write corresponds
9. `reconcile` call-fault-misspoken-time-t2 (ConfirmationSent): no write corresponds
10. `verdict` call-fault-misspoken-time-c1 (AppointmentBooked): VERIFIED
11. `verdict` call-fault-misspoken-time-c2 (ConfirmationSent): VERIFIED
12. `verdict` call-fault-misspoken-time-t1 (AppointmentBooked): NOT_FOUND
13. `verdict` call-fault-misspoken-time-t2 (ConfirmationSent): NOT_FOUND
14. `verdict` Outcome: DISCREPANCIES

## Seal

- content `74a37fb5b7c6873e7dc8ba84db32123bf5d78ba51e17f0df3d358da30be3f107`
- chain `7fb1e034a750f61a885ac4c088f4d2077d4126af17d2870f533fe4640ad10edc` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
