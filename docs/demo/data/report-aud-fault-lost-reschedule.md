# Audit report `aud-fault-lost-reschedule`

**Outcome: DISCREPANCIES**

- Call: `call-fault-lost-reschedule` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `2da7874a9edf141557738054...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. Rescheduled (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `CRITICAL`
- **Finding:** The calendar changed, but not the way the caller was told. new_appointment: claimed Thu Mar 5 2026, 11:00 AM EST, actual none created.
- **What the caller heard (turn 2):** "All done. Your visit is rescheduled from Tuesday, March 3 at 10:00 AM to Thursday, March 5 at 11:00 AM."
- **Fix:** Reconcile the calendar with what the caller was told: one appointment, at the promised time, and the old slot released.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| new_appointment | Thu Mar 5 2026, 11:00 AM EST | none created | **NO** |
| old_slot_released | released | released | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: new_appointment.
- `side_effects`: pass. Side effects are as claimed.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0001`: Changed by the agent during the call (journal #7; net: updated)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-lost-reschedule-c1 (Rescheduled): partial match -> apt_0001
7. `verdict` call-fault-lost-reschedule-c1 (Rescheduled): MISMATCH
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `3f44f713e626f11a4105bd69fd92efd31b642c4bf13c957920b5697cb98dde47`
- chain `2da7874a9edf141557738054275faca170d8621d34c1f477cf8ec5b9a1a2ae00` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
