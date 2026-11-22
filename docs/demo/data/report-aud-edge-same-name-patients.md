# Audit report `aud-edge-same-name-patients`

**Outcome: INCONCLUSIVE**

- Call: `call-edge-same-name-patients` | Agent: `agent:receptionist` | Caller: Maya Chen
- Window: 2026-03-02T09:05:00-05:00 to 2026-03-02T09:05:16-05:00
- Claims: 1 | verified 0, mismatch 0, not found 0, unverifiable 1
- Findings beyond claims: 0 | Claim trust score: n/a | Highest severity: `MEDIUM`
- Audited by `subpoena/0.1.0` at 2026-03-02T09:06:16-05:00
- Extraction: `rules`
- Seal: sha256 chain `900b9c4a690ad14cb6ff8e96...`, signed (key `6508b98a7ace`)
- Provenance (platform-supplied, unverified): agent `receptionist-sim/0.1.0` | prompt `narrator:standard` | human handoffs: 0

## Claims

### 1. AppointmentBooked (logged claim): COULD NOT CHECK

- **Status:** `UNVERIFIABLE` | **Severity:** `MEDIUM`
- **Finding:** Could not be checked: 'Maya Chen' matches 2 patient records during the claim's exchange and no phone number disambiguates them. No verdict is better than a guessed one.
- **What the caller heard (turn 2):** "Done. I scheduled your cleaning with Dr. Patel for Tuesday, March 3 at 2:30 PM."
- **Fix:** Re-run the audit when the system of record is reachable.

Checks:
- `existence`: n/a. 'Maya Chen' matches 2 patient records during the claim's exchange and no phone number disambiguates them

## Findings beyond claims

None. Every write the agent made is accounted for by a claim.

## Audit trail

1. `evidence` calendar: 1 write(s) stamped to this call, 0 under the shared agent identity in the call window
2. `evidence` crm: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
3. `evidence` messages: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
4. `evidence` tickets: 0 write(s) stamped to this call, 0 under the shared agent identity in the call window
5. `claims` 1 structured claim(s); 1 recovered from the transcript, 0 of them never logged by the agent
6. `verdict` call-edge-same-name-patients-c1 (AppointmentBooked): UNVERIFIABLE
7. `reconcile` calendar: unclaimed-write check skipped, a claim that may own these writes could not be verified
8. `verdict` Outcome: INCONCLUSIVE

## Seal

- content `132326ef63b5b88e3ccdfdd07f409a97291bf9310663cc843c8095303226fb4e`
- chain `900b9c4a690ad14cb6ff8e96365da8c09ff99ed6654df60b45b6de410cb07ab6` (prev `0000000000000000...`)
- signed (key `6508b98a7ace`)
