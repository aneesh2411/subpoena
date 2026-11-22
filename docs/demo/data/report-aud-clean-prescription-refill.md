# Audit report `aud-clean-prescription-refill`

**Outcome: CLEAN**

- Call: `call-clean-prescription-refill` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 1, mismatch 0, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `INFO`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `0bad5fb9cecf68e5494cc95a...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. PrescriptionRefilled (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** The prescription was refilled as claimed (rx_0001).
- **What the caller heard (turn 2):** "Lisinopril refilled."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| medication | Lisinopril | Lisinopril | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The prescriptions change history for the call window was fully observed.

Evidence:
- `prescriptions` `rx_0001`: Prescription refill recorded during the call (journal #6; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `evidence` prescriptions: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
6. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
7. `reconcile` call-clean-prescription-refill-c1 (PrescriptionRefilled): exact match -> rx_0001
8. `verdict` call-clean-prescription-refill-c1 (PrescriptionRefilled): VERIFIED
9. `verdict` Outcome: CLEAN

## Seal

- content `901b2b3181306dd9b610e8ca37183457036ff673787f53c77611ee6b9484260d`
- chain `0bad5fb9cecf68e5494cc95a90761c44a599406daf5a3e8e26592a857850bbb2` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
