# Audit report `aud-fault-wrong-payment-amount`

**Outcome: DISCREPANCIES**

- Call: `call-fault-wrong-payment-amount` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 1, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `HIGH`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `9ce422252713e834b7996e68...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. PaymentCollected (logged claim): RECEIPT DOES NOT MATCH

- **Status:** `MISMATCH` | **Severity:** `HIGH`
- **Finding:** A payment was recorded (pay_0001) with different details. amount_cents: claimed 4500, actual 4000.
- **What the caller heard (turn 2):** "I took the $45.00 copay by card."
- **Fix:** Check the payment ledger and resolve the discrepancy with the patient.

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| amount_cents | 4500 | 4000 | **NO** |
| method | card | card | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: FAIL. Differs on: amount_cents.
- `side_effects`: n/a. Not applicable.
- `attribution`: pass. Every backing write carries this call's own stamp.
- `coverage`: pass. The payments change history for the call window was fully observed.

Evidence:
- `payments` `pay_0001`: Payment receipt recorded during the call (journal #6; net: created)

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `evidence` payments: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
6. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
7. `reconcile` call-fault-wrong-payment-amount-c1 (PaymentCollected): partial match -> pay_0001
8. `verdict` call-fault-wrong-payment-amount-c1 (PaymentCollected): MISMATCH
9. `verdict` Outcome: DISCREPANCIES

## Seal

- content `cdc0646aec8e6c28f780e2e18ca0c62755a1a72094e7f2e00afc31154d361f9c`
- chain `9ce422252713e834b7996e68daffd68233e3c427ba6738ba87fcda475df8a81c` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
