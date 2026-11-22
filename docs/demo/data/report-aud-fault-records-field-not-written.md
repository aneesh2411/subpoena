# Audit report `aud-fault-records-field-not-written`

**Outcome: DISCREPANCIES**

- Call: `call-fault-records-field-not-written` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `HIGH`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `c6568210dd63d46d6df4027f...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. RecordsUpdated (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `HIGH`
- **Finding:** The patient record edit differs from what the agent claimed. field: claimed insurance, actual address; new_value: claimed Example Health, actual (unset).
- **What the caller heard (turn 2):** "I've updated your insurance on file to Example Health."
- **Fix:** Check the patient record and confirm the correct value with the caller.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| field | insurance | address | **NO** |
| new_value | Example Health | (unset) | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: field, new_value.
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
6. `reconcile` call-fault-records-field-not-written-c1 (RecordsUpdated): partial match -> pat_0001
7. `verdict` call-fault-records-field-not-written-c1 (RecordsUpdated): MISMATCH
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `d088409f16f6a6b7f1a6a87e7402b60bbb01490ae95bfb8059c5bc30999fc89a`
- chain `c6568210dd63d46d6df4027f4bb18a23a99c42994ef0fd6d6decfa1483c047d6` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
