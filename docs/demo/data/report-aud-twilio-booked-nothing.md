# Audit report `aud-twilio-booked-nothing`

**Outcome: DISCREPANCIES**

- Call: `CA8f3e2d1c4b5a69708192a3b4c5d6e7f8` | Agent: `brightside-reception-agent` | Caller: Rosa Delgado
- Window: 2026-11-19T18:03:07+00:00 to 2026-11-19T18:06:41+00:00
- Claims: 2 | verified 1, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 50% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-11-19T18:07:41+00:00
- Extraction: `rules`
- Seal: sha256 chain `65973d39c77e1fd22e640f69...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `v3.2.1` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `CRITICAL`
- **Finding:** The agent claimed it booked Rosa Delgado on Tue Nov 24 2026, 3:00 PM EST, but no appointment was created during the call.
- **Fix:** Create the appointment or call the patient back before they show up.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-11-19T18:03:07+00:00 .. 2026-11-19T18:07:11+00:00`: 0 agent write(s) to calendar in the call window. None creates this booking.

### 2. ConfirmationSent (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Confirmation was sent to +14805550142 as claimed (msg_0001).

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| recipient | +14805550142 | +14805550142 | yes |
| channel | sms | sms | yes |
| delivery | sent | sent | yes |
| stated_time | Tue Nov 24 2026, 3:00 PM EST | no time stated in the message | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The messages change history for the call window was fully observed.

Evidence:
- `messages` `msg_0001`: Message logged during the call (journal #2; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 2 structured claim(s); 0 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` tool-log-7f3b (ConfirmationSent): exact match -> msg_0001
7. `reconcile` tool-log-7f3a (AppointmentBooked): no write corresponds
8. `verdict` tool-log-7f3a (AppointmentBooked): NOT_FOUND
9. `verdict` tool-log-7f3b (ConfirmationSent): VERIFIED
10. `verdict` Outcome: DISCREPANCIES

## Seal

- content `fc1417e8f1b6b481caa946e129194ecdd762a78bf3456c9bfeb0d40cc5167a05`
- chain `65973d39c77e1fd22e640f69226669208dd85afd01e2d02f8d46717a047ae13f` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
