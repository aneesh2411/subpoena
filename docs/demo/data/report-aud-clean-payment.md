# Audit report `aud-clean-payment`

**Outcome: CLEAN**

- Call: `call-clean-payment` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 1, mismatch 0, not found 0, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 100% | Highest severity: `INFO`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `9e5a295cab49eff488672dfb...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. PaymentCollected (logged claim): RECEIPT FOUND

- **Status:** `VERIFIED` | **Severity:** `INFO`
- **Finding:** The payment was collected as claimed (pay_0001).
- **What the caller heard (turn 2):** "I took the $40.00 copay by card."

| Field | Agent claimed | System of record | Match |
|---|---|---|---|
| patient | Maya Chen (+15550101001) | Maya Chen (+15550101001) | yes |
| amount_cents | 4000 | 4000 | yes |
| method | card | card | yes |

Checks:
- `existence`: pass. A write by the agent during the call corresponds to this claim.
- `exactness`: pass. All fields match.
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
7. `reconcile` call-clean-payment-c1 (PaymentCollected): exact match -> pay_0001
8. `verdict` call-clean-payment-c1 (PaymentCollected): VERIFIED
9. `verdict` Outcome: CLEAN

## Seal

- content `4e82354e83b8e5dc932148a580038205dd35ace0d5483ec0ad3d61ac6f90ab73`
- chain `9e5a295cab49eff488672dfbe1497bb10bb85e7e0548e935c320fc8c2ef2180d` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
