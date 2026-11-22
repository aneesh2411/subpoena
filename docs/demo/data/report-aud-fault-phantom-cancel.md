# Audit report `aud-fault-phantom-cancel`

**Outcome: DISCREPANCIES**

- Call: `call-fault-phantom-cancel` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `9889841bdac0a2b11bbe8056...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. Cancelled (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `MEDIUM`
- **Finding:** The agent claimed it cancelled the appointment for Maya Chen on Tue Mar 3 2026, 10:00 AM EST, but it is still on the books (apt_0001).
- **What the caller heard (turn 2):** "Your appointment on Tuesday, March 3 at 10:00 AM is now cancelled."
- **Fix:** Cancel the appointment so the slot is released and the patient is not charged for a no-show.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `journal` `calendar 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to calendar in the call window. None cancels this appointment.
- `calendar` `apt_0001`: Still booked (current state)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-phantom-cancel-c1 (Cancelled): no write corresponds
7. `verdict` call-fault-phantom-cancel-c1 (Cancelled): NOT_FOUND
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `841546d1fe7fe8d10b3123fd85308fdac5dabd67d8c47427661ecf99d35fcbb8`
- chain `9889841bdac0a2b11bbe8056a2035de4e4c976cad9da3bcddc169b46745aa9fa` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
