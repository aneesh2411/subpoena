# Audit report `aud-correction-stale-write`

**Outcome: DISCREPANCIES**

- Call: `call-correction-stale-write` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:24-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:24-05:00
- Extraction: `rules`
- Seal: sha256 chain `a8e41aa357476d96ff8973f0...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `CRITICAL`
- **Finding:** A booking was written (apt_0001) but it is not what the caller was told. start: claimed Wed Mar 4 2026, 2:30 PM EST, actual Tue Mar 3 2026, 2:30 PM EST. The calendar holds Tue Mar 3 2026, 2:30 PM EST, the exact value the caller corrected away from at turn 2.
- **What the caller heard (turn 4):** "No problem at all. I've booked you for Wednesday, March 4 at 2:30 PM instead."
- **Fix:** Correct the appointment to match what the caller was told, or call them back with the real details.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Wed Mar 4 2026, 2:30 PM EST | Tue Mar 3 2026, 2:30 PM EST | **NO** |
| provider | Dr. Patel | Dr. Patel | yes |

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

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 2 structured claim(s); 2 recovered from the transcript, 0 of them never logged by the agent
6. `claims` call-correction-stale-write-c2 corrects call-correction-stale-write-c1 (turn 4); the earlier AppointmentBooked claim is withdrawn, the corrected value is what the caller is owed
7. `reconcile` call-correction-stale-write-c2 (AppointmentBooked): partial match -> apt_0001
8. `verdict` call-correction-stale-write-c2 (AppointmentBooked): MISMATCH
9. `verdict` Outcome: DISCREPANCIES

## Seal

- content `862d898abbc78a3db7f044042b55c05d4cb19ff286d82ba68dcaf717f8a42e66`
- chain `a8e41aa357476d96ff8973f0f7e64d68933441ea2785cea6565bec769cfec118` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
