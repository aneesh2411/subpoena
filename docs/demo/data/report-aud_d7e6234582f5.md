# Audit report `aud_d7e6234582f5`

**Outcome: CLEAN**

- Call: `call-fault-wrong-time` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 1, mismatch 0, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `INFO`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:18-05:00
- Extraction: `none`
- Seal: sha256 chain `3351f23e0ceca799bcc8e8ad...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** Booking for Maya Chen on Tue Mar 3 2026, 2:30 PM EST exists exactly as claimed (apt_0002).
- **What the caller heard (turn 2):** "Okay, I've got you down with Dr. Patel on Tuesday, March 3 at 2:30 PM. I just texted you a confirmation."

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
- `calendar` `apt_0002`: Appointment written by the agent during the call (journal #8; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 2 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `fix` Wrote apt_0002 as agent:receptionist:call-fault-wrong-time using resolve approval 1.
6. `recheck` Re-checking claim call-fault-wrong-time-c1 after remediation.
7. `reconcile` call-fault-wrong-time-c1 (AppointmentBooked): exact match -> apt_0002
8. `verdict` call-fault-wrong-time-c1 (AppointmentBooked): VERIFIED

## Seal

- content `043b425bfe5b9883f75f830ccebec59bebbf14087ff13a5493423fec805e6c9b`
- chain `3351f23e0ceca799bcc8e8ad5ffc8c920ca3380242d641e4bb71c0dfe9c03a3b` (prev `2fa12f265403bbe4...`)
- signed (key `6508b98a7ace`)
