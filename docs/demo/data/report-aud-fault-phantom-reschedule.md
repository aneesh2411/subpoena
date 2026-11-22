# Audit report `aud-fault-phantom-reschedule`

**Outcome: DISCREPANCIES**

- Call: `call-fault-phantom-reschedule` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `HIGH`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `c9fd33c1f49d9ba276f925ea...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. Rescheduled (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `HIGH`
- **Finding:** The agent claimed it moved Maya Chen from Tue Mar 3 2026, 10:00 AM EST to Thu Mar 5 2026, 11:00 AM EST, but nothing was changed during the call.
- **What the caller heard (turn 2):** "I've moved your appointment from Tuesday, March 3 at 10:00 AM to Thursday, March 5 at 11:00 AM."
- **Fix:** Move the appointment, or tell the patient the original time still stands.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to calendar in the call window. None moves this appointment.
- `calendar` `apt_0001`: Original appointment is untouched (current state)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-phantom-reschedule-c1 (Rescheduled): no write corresponds
7. `verdict` call-fault-phantom-reschedule-c1 (Rescheduled): NOT_FOUND
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `406fbf994efb6416d98924aea8f0176d6d3b25a039256a9fc1cacda6f997fa4f`
- chain `c9fd33c1f49d9ba276f925ea28a1f594c5884aa84b3045a7f096dec19189812b` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
