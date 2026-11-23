# Audit report `aud_ffe49b2fd8b8`

**Outcome: DISCREPANCIES**

- Call: `call-fault-wrong-time` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:19-05:00
- Extraction: `none`
- Seal: sha256 chain `9cf656557d413239c356434d...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `CRITICAL`
- **Finding:** A booking was written (apt_0001) but it is not what the caller was told. start: claimed Tue Mar 3 2026, 2:30 PM EST, actual Tue Mar 3 2026, 3:30 PM EST.
- **What the caller heard (turn 2):** "Okay, I've got you down with Dr. Patel on Tuesday, March 3 at 2:30 PM. I just texted you a confirmation."
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
- `calendar` `apt_0001`: Appointment written by the agent during the call (journal #6; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 3 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `fix` Reverted apt_0002 from resolve approval 1.
6. `recheck` Re-checking claim call-fault-wrong-time-c1 after remediation.
7. `reconcile` call-fault-wrong-time-c1 (AppointmentBooked): partial match -> apt_0001
8. `verdict` call-fault-wrong-time-c1 (AppointmentBooked): MISMATCH

## Seal

- content `92cf1fecd6076b9dd9146afd7d1c744d5e29917005094342e0ff62d5b00ca21f`
- chain `9cf656557d413239c356434d8ca455f98edf6332af12143e9d6f37c3db0772e0` (prev `2fa12f265403bbe4...`)
- signed (key `6508b98a7ace`)
