# Audit report `aud-fault-cancel-wrong-appointment`

**Outcome: DISCREPANCIES**

- Call: `call-fault-cancel-wrong-appointment` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `CRITICAL`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `4c08a309ead65de9bffb8dbd...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. Cancelled (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `CRITICAL`
- **Finding:** The agent cancelled a different appointment (apt_0002) than it claimed. start: claimed Tue Mar 3 2026, 10:00 AM EST, actual Tue Mar 10 2026, 3:00 PM EDT.
- **What the caller heard (turn 2):** "I've cancelled your appointment on Tuesday, March 3 at 10:00 AM."
- **Fix:** Restore the wrongly cancelled appointment and cancel the intended one.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| start | Tue Mar 3 2026, 10:00 AM EST | Tue Mar 10 2026, 3:00 PM EDT | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: start.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The calendar change history for the call window was fully observed.

Evidence:
- `calendar` `apt_0002`: Released by the agent during the call (journal #8; net: updated)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-cancel-wrong-appointment-c1 (Cancelled): partial match -> apt_0002
7. `verdict` call-fault-cancel-wrong-appointment-c1 (Cancelled): MISMATCH
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `ac680a1753914db11b996bfc6c516c49311a8f2d709ecf3d11717a97c82dd72d`
- chain `4c08a309ead65de9bffb8dbd7a4faf12c30d73fb69a05818d3376074099b6d72` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
