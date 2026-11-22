# Audit report `aud-fault-reschedule-rewrite`

**Outcome: DISCREPANCIES**

- Call: `call-fault-reschedule-rewrite` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `e8cdbf2794140b0de918ae75...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. Rescheduled (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `MEDIUM`
- **Finding:** The calendar changed, but not the way the caller was told. rewrite_provider: claimed unchanged, actual now Dr. Kim; rewrite_duration: claimed unchanged, actual now 60 min; provider: claimed Dr. Patel, actual Dr. Kim.
- **What the caller heard (turn 2):** "All done. Your visit is rescheduled from Tuesday, March 3 at 10:00 AM to Thursday, March 5 at 11:00 AM."
- **Fix:** Reconcile the calendar with what the caller was told: one appointment, at the promised time, and the old slot released.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| new_start | Thu Mar 5 2026, 11:00 AM EST | Thu Mar 5 2026, 11:00 AM EST | yes |
| old_start | Tue Mar 3 2026, 10:00 AM EST | Tue Mar 3 2026, 10:00 AM EST | yes |
| old_slot_released | released | released (same record moved) | yes |
| rewrite_provider | unchanged | now Dr. Kim | **NO** |
| rewrite_duration | unchanged | now 60 min | **NO** |
| provider | Dr. Patel | Dr. Kim | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: provider.
- `side_effects`: FAIL. Failed: rewrite_provider, rewrite_duration.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0001`: Changed by the agent during the call (journal #7, #8; net: updated)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 2 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-reschedule-rewrite-c1 (Rescheduled): exact match -> apt_0001
7. `verdict` call-fault-reschedule-rewrite-c1 (Rescheduled): MISMATCH
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `6c9b170630b92ce02a761fca559809f7ab6c4a637286332791beb66b3f160a8b`
- chain `e8cdbf2794140b0de918ae75d5df79130138946d8bfcc8ff8cbc725a8bb5e2d1` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
