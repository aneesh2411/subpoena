# Audit report `aud-fault-book-inline-provider-correction`

**Outcome: DISCREPANCIES**

- Call: `call-fault-book-inline-provider-correction` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `4e20edcd2dc88221d2f1883b...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (said to caller, never logged): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `MEDIUM`
- **Finding:** A booking was written (apt_0001) but it is not what the caller was told. provider: claimed Dr. Kim, actual Dr. Patel.
- **What the caller heard (turn 2):** "I've booked you with Dr. Patel for Tuesday, March 3 at 2:30 PM—actually, with Dr. Kim for Wednesday, March 4 at 2:30 PM."
- **Fix:** Correct the appointment to match what the caller was told, or call them back with the real details.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Wed Mar 4 2026, 2:30 PM EST | Wed Mar 4 2026, 2:30 PM EST | yes |
| provider | Dr. Kim | Dr. Patel | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: provider.
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
5. `claims` 0 structured claim(s); 1 recovered from the transcript, 1 of them never logged by the agent
6. `reconcile` call-fault-book-inline-provider-correction-t1 (AppointmentBooked): partial match -> apt_0001
7. `verdict` call-fault-book-inline-provider-correction-t1 (AppointmentBooked): MISMATCH
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `991b929f46cd4b37f2f0d60a738cc86362cc39c93e9cc966a3f2abee5fabed4c`
- chain `4e20edcd2dc88221d2f1883bf302eaec2b0263a959e0ffdf6b059c525c804737` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
