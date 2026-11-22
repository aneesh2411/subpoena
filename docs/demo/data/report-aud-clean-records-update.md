# Audit report `aud-clean-records-update`

**Outcome: CLEAN**

- Call: `call-clean-records-update` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 1, mismatch 0, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `INFO`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `c9abbb3dd04a94b15999de65...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. RecordsUpdated (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** The patient's address was updated as claimed.
- **What the caller heard (turn 2):** "I've updated your address on file to 42 Cedar Lane."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| field | address | address | yes |
| new_value | 42 Cedar Lane | 42 Cedar Lane | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The crm change history for the call window was fully observed.

Evidence:
- `crm` `pat_0001`: Patient record edit during the call (journal #6; net: updated)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-clean-records-update-c1 (RecordsUpdated): exact match -> pat_0001
7. `verdict` call-clean-records-update-c1 (RecordsUpdated): VERIFIED
8. `verdict` Outcome: CLEAN

## Seal

- content `ce36850839190a596fd4602d74ed00c981e030af2074f3b8b664e6470327fed5`
- chain `c9abbb3dd04a94b15999de65a2639b5cce9b03889521f8dc902c8fba054619d6` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
