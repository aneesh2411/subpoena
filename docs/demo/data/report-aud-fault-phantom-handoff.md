# Audit report `aud-fault-phantom-handoff`

**Outcome: DISCREPANCIES**

- Call: `call-fault-phantom-handoff` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 1, unverifiable 0
- Findings beyond claims: 0 | Claim trust score: 0% | Highest severity: `HIGH`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `29de2afd28e1c2d27735dc17...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. HandoffCreated (logged claim): NO RECEIPT

- **Status:** `NOT_FOUND` | **Severity:** `HIGH`
- **Finding:** The agent claimed it created a handoff ticket, but no ticket was created during the call. Nobody is going to call this person back.
- **What the caller heard (turn 2):** "I've created a ticket for the front desk about a billing question about my last invoice. Someone will call you back."
- **Fix:** Create the ticket now and have a human follow up with the caller.

Checks:
- `existence`: FAIL. No write by the agent during the call corresponds to this claim.
- `exactness`: n/a. Nothing to compare.
- `side_effects`: n/a. Nothing to compare.
- `coverage`: pass. The tickets change history for the call window was fully observed.

Evidence:
- `journal` `tickets 2026-03-02T09:05:00-05:00 .. 2026-03-02T09:05:46-05:00`: 0 agent write(s) to tickets in the call window. None is this handoff.

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `reconcile` call-fault-phantom-handoff-c1 (HandoffCreated): no write corresponds
7. `verdict` call-fault-phantom-handoff-c1 (HandoffCreated): NOT_FOUND
8. `verdict` Outcome: DISCREPANCIES

## Seal

- content `0054257ed5242173186c654ee3aa093bcb5b21a0c29d4cc727b4dab0f88024d2`
- chain `29de2afd28e1c2d27735dc178070b2f6403bb7b16318dede3055e9d0255cc8d6` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
