# Audit report `aud-fault-wrong-refill-medication`

**Outcome: DISCREPANCIES**

- Call: `call-fault-wrong-refill-medication` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `HIGH`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `b226d1b0febd0d637a523031...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. PrescriptionRefilled (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `HIGH`
- **Finding:** A refill was recorded (rx_0001) with different details. medication: claimed Lisinopril, actual Amlodipine.
- **What the caller heard (turn 2):** "Lisinopril refilled."
- **Fix:** Check the refill record and confirm the medication and patient.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| medication | Lisinopril | Amlodipine | **NO** |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: medication.
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
7. `reconcile` call-fault-wrong-refill-medication-c1 (PrescriptionRefilled): partial match -> rx_0001
8. `verdict` call-fault-wrong-refill-medication-c1 (PrescriptionRefilled): MISMATCH
9. `verdict` Outcome: DISCREPANCIES

## Seal

- content `a2770a50c3045287531649195665a05d49f5d93cb58e64133325714f0840d4fd`
- chain `b226d1b0febd0d637a5230313f82f88bfb63e5d8977c1cd8bc4e85b9cf9a5836` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
