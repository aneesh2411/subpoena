# Human review workflow

Review disposition is independent of audit correctness. The API derives one
initially open item per standalone finding and per MISMATCH, NOT_FOUND, or
UNVERIFIABLE verdict in each sealed audit. VERIFIED verdicts and call-level
outcomes do not create items. The same rule applies to existing stored audits.
A pointer such as `/findings/0` or `/verdicts/1` addresses the immutable
result in `GET /audit/{audit_id}/report`. The item's ID binds its audit ID,
audit seal chain hash, and result pointer. Rerunning an audit produces a
distinct item if the new audit contains the same finding.

- `GET /reviews` returns `items`, the ordered `journal`, `journal_head`,
  and the journal's `authentication` status in the standard API envelope.
  Each item has its audit reference, current `state`, and concurrency token
  `head`. Initially the token is the item ID. It changes to the transition
  receipt hash on each accepted write.
- `POST /reviews/{item_id}/transition` takes `expected_head`, destination
  `state`, `actor`, and `reason` and returns the committed receipt.
  Fetch the item first and send its current `head` as `expected_head`.
- The permitted edges are `open → acknowledged`,
  `acknowledged → resolved`, `acknowledged → disputed`, and
  `disputed → open`. Resolved items are terminal. The server assigns the
  UTC timestamp. Actor and reason must be nonblank printable single-line
  strings of at most 128 and 2,000 characters respectively.
- Requests use the same `X-API-Key` boundary as the audit API. With no API key
  configured, the API is unauthenticated as in local development. Keep review
  data and receipts within the private API boundary. Actor is a label supplied
  by the operator under the shared key. The server HMAC attests to the
  recorded bytes under the signing key and provides no individual identity proof.
- Unknown items return 404. Invalid transitions, stale item heads, and missing
  checkpoints return 409. Malformed requests return 422. Storage lock contention
  or a missing signing key for an existing signed journal write returns 503.
  Integrity failures return 500. A rejected write leaves no receipt or state change.

## Assignment escalation evidence

When claims compete for writes in tied best assignments, their verdicts stay
UNVERIFIABLE. The report's `alternatives` lists the tied candidate pairings
involved in the conflict. Each pairing identifies the claim, its content
score, whether it was an exact match, and the write or writes it would use.

The sealed `escalation_log` records each non-VERIFIED claim's reason and
disposition. For assignment blocks, `candidate_count` counts the scored
pairings considered in the block, and `score_spread` is their maximum score
minus their minimum. A count of zero and a null spread indicate that the
decision recorded no scored options. The review demo includes a downloadable
sealed example.

## Receipts and verification

The review journal uses the audit database, so a file-backed
`SUBPOENA_DB` persists review history across restarts. Each transition
locks the database for writes before reading the audit ledger and review chain
and commits the receipt in the same transaction as the head and state checks.
Reads use a consistent SQLite snapshot. Current state is computed by replaying
receipts from genesis. Review writes leave audit bytes, seals, verdicts,
scorecards, and outbound webhook behavior untouched. Listing and writing
verify the audit ledger and replay the full review journal, so their cost
grows with the ledger and the list returns the full history.

V1 uses SHA-256 and canonical JSON with keys sorted lexicographically, no
spaces around separators, and non-ASCII characters escaped as in Python
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
Hash outputs are lowercase hex.

- Item ID hashes the ASCII prefix `subpoena.review.item.v1:` followed by the
  canonical JSON of `audit_id`, `audit_chain_hash`, and `result_pointer`.
- A review receipt's `hash` hashes the ASCII prefix
  `subpoena.review.transition.v1:` followed by canonical JSON for all its
  fields except `hash` and `signature`. The timestamp uses ISO 8601 UTC
  with six fractional digits and the `+00:00` suffix. The global sequence
  starts at 1, and `previous_hash` starts with 64 zeroes. Each
  `previous_item_head` links to the item's last receipt, or the item ID for
  its first transition.
- With `SUBPOENA_SIGNING_KEY` configured, the key ID is the first 12 hex
  digits of SHA-256 of the key bytes, as used for audit seals. The signature
  is HMAC-SHA256 over the ASCII string `subpoena.review.signature.v1:`
  followed by the receipt hash. Both signature fields are null for unsigned
  receipts. An empty journal reports `unsigned`. A signed journal read
  without the key reports `unverifiable`. A nonempty journal read successfully
  with the correct configured key reports `authenticated`. Keep the original
  signing configuration for existing data. Key rotation and migration of
  unsigned history to signed history are outside V1.

Both endpoints accept an optional `checkpoint` query parameter. Retain a
previously trusted `journal_head` in independent storage and pass it on
later requests to require that the verified journal still contains that prefix.
A journal can look valid after removal of its tail or replacement with an
older copy, even when signed. Checking a retained head detects rollback past
that point. Anything after that checkpoint remains subject to rollback until
a newer head is retained. In unsigned mode, an attacker who can rewrite
storage can also recompute hashes, including changes after a trusted
checkpoint. Unsigned verification establishes chain consistency and agreement
with supplied checkpoints. It carries no authenticity guarantee.

A verifier must obtain its HMAC key and retained checkpoint through trusted
channels. The review checkpoint anchors transitioned items and their source
audit seals through the receipt item IDs. Completeness of the source audit
ledger, including audits without review events, requires an independently
trusted audit head. The review API verifies source linkage from audit genesis
and checks audit signatures under the existing seal rules.
