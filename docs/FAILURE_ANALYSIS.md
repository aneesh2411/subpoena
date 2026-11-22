# Failure analysis

Every number in a marked block is generated from the committed eval artifacts - never edit
one by hand. Refresh the artifacts, then re-sync the docs:

```bash
subpoena eval --report docs/EVAL_REPORT.md --json evals/results/latest.json
python scripts/sync_docs.py
```

`python scripts/sync_docs.py --check` fails on drift and runs as a test.

## 1. Results in one screen

<!-- metrics:fa-results:start -->
Golden set: 61 hand-labelled calls, 99 claims, 7 findings beyond claims.

| Metric | Subpoena | Naive lookup |
|---|---|---|
| Claim precision | 100% | 90.9% |
| Claim recall | 94.6% | 54.0% |
| Exact verdict | 98.0% | 73.7% |
| Finding recall | 100% | 0% |
| Call accuracy | 96.7% | 65.6% |
| Calls fully correct | 96.7% | 50.8% |

Claim-level confusion for Subpoena: 35 true positives, 0 false positives, 2 false negatives, 62 true negatives.

Fuzzed set: 500 generated calls, 970 claims, 79 findings. No disagreements with ground truth.
<!-- metrics:fa-results:end -->

## 2. The two failures

Both are labelled `known_gap` in the dataset. They are counted in every metric above. A
test asserts they still fail, so that if one starts passing the label has to be removed.

### 2.1 `gap-vague-spoken-promise`: a false claim the auditor never sees

**What happens.** The agent tells the caller *"Dr. Patel will see you Tuesday then, same
time we discussed."* It logs no structured claim and writes nothing. The expected verdict is
`NOT_FOUND` on a booking claim. The auditor reports a clean call.

**Why.** With no structured claim, the only trace of the promise is the transcript. The
rule-based reader recovers a booking claim only from an explicit statement: a booking verb
and a parseable date and time in the same sentence. "Same time we discussed" has neither.

**Why it is not fixed with more rules.** The time is in an earlier caller turn. Resolving it
means tracking references across turns, which is the point at which regexes start producing
false claims. A false claim becomes a false accusation, and precision matters more than
recall for an auditor people have to trust.

**The intended fix.** `LLMClaimExtractor` exists for this. A unit test shows that when a
model returns the resolved booking, the auditor produces the correct `NOT_FOUND`. That test
uses a stand-in model. **It has not been run against a live model**, so there is no measured
recall for that path yet.

**Impact if left alone.** This is the most damaging miss: the patient believes they are
booked and nothing in the report says otherwise. Any deployment where agents speak freely
needs the model-backed reader, measured on real transcripts.

### 2.2 `gap-garbled-handoff`: a wrong ticket that verifies

**What happens.** The caller has a billing question. The agent files a ticket for the right
patient at the right priority, about parking validation. Expected `MISMATCH`; the auditor
says `VERIFIED`.

**Why.** Ticket summaries are free text and are deliberately not compared. Deciding whether
"billing question" and "parking validation" describe the same thing is a semantic judgment.
Doing it with string similarity is guesswork, and doing it with a model would put a model on
the verdict path, which the design rules out.

**A fix that keeps the rule.** Have the model-backed extractor produce a structured
`topic` for both the claim and the ticket at read time, then compare topics
deterministically. The model reads; it still does not judge.

**Impact if left alone.** Moderate. A human reads the ticket and usually notices. The report
does say, in the evidence line, that the summary was not compared.

## 3. Bugs the tests and evals caught during the build

These were real defects in the auditor or service, found before release. Listing them
because they show what kind of mistake this design is prone to.

### 3.1 An outage produced a false accusation

**Found by** a hand-written test for a CRM outage.

With the CRM unreachable, the auditor could not resolve who the patient was, so the booking
claim was correctly `UNVERIFIABLE`. But because that claim never consumed its write, the
appointment was left over and reported as an **unclaimed write**. A verified-honest call was
flagged because a dependency was down.

**Fix.** If a claim that might own a write could not be verified, the unclaimed-write check
for that system is skipped and the call is `INCONCLUSIVE`. Added to the golden set as
`edge-crm-outage`.

**Lesson.** The two directions of reconciliation are coupled. Anything that stops a claim
from being judged also makes "leftover writes" meaningless for that system.

### 3.2 Deleting the first ledger entry went undetected

**Found by** a test that removes an audit from the ledger and expects verification to fail.

`/ledger/verify` checked each report's link to the previous one, but did not check that the
first report linked to genesis. Removing the oldest audit left a chain that verified.

**Fix.** Verification starts from the genesis hash.

## 4. Findings from independent review (all fixed in v0.2)

After the build, a separate reviewer was asked to construct calls that make the auditor
return a wrong verdict. It reproduced the following by running code. The golden set and
the fuzzer did not contain these shapes, which is why the headline numbers did not catch
them. **All nine are fixed in v0.2**; each fix added the shape to the golden set, the
fuzzer, and the regression suite, so the dataset now covers what the review found.

False `VERIFIED`:

1. **Duplicate claim ids.** Two claims sharing one `claim_id` both verified against a
   single write. Fixed: call records with duplicate claim ids are rejected at ingest.
   ([#1](https://github.com/aneesh2411/subpoena/issues/1),
   [PR #11](https://github.com/aneesh2411/subpoena/pull/11))
2. **Reschedule that also rewrites the record.** An in-place move that also reassigns the
   patient, provider or duration verified, because only the times were compared. Fixed:
   the reschedule judge checks every identifying field of the original.
   ([#2](https://github.com/aneesh2411/subpoena/issues/2),
   [PR #12](https://github.com/aneesh2411/subpoena/pull/12))
3. **Reschedule of an appointment that never existed.** "Moved from A to B" verified when
   nothing was ever booked at A and a fresh appointment was created at B. Fixed: a
   reschedule whose original cannot be found is a mismatch, not a soft miss.
   ([#3](https://github.com/aneesh2411/subpoena/issues/3),
   [PR #12](https://github.com/aneesh2411/subpoena/pull/12))
4. **Spoken detail that differs from the log.** The caller was told one provider, the log
   said another, and the spoken claim was merged away because only the time was compared.
   Fixed: spoken and logged claims that disagree are audited as separate promises.
   In `fault-misspoken-time`, the caller asks for Tuesday. The agent announces a Wednesday
   booking and immediately says, “I just texted you a confirmation.” That confirmation
   refers to the booking the agent just announced. The calendar and the text both name
   Tuesday, so the structured booking and confirmation verify. Neither a Wednesday
   booking nor a confirmation for Wednesday can be found.
   ([#4](https://github.com/aneesh2411/subpoena/issues/4),
   [PR #13](https://github.com/aneesh2411/subpoena/pull/13))
5. **Two patients with the same name.** With no usable phone number, a booking for the
   other person of that name verified. Fixed: an ambiguous patient reference is
   `UNVERIFIABLE`, never `VERIFIED` - a guess could clear a booking made for the wrong
   person. ([#5](https://github.com/aneesh2411/subpoena/issues/5),
   [PR #14](https://github.com/aneesh2411/subpoena/pull/14))

False accusation:

6. **Recaps.** "To recap, you're booked for Tuesday" in a later turn was read as a second
   booking claim with no write behind it, and reported `NOT_FOUND`. Fixed: recaps
   corroborate the original claim when every identifying field matches, and repeated
   spoken claims dedupe. ([#6](https://github.com/aneesh2411/subpoena/issues/6),
   [PR #13](https://github.com/aneesh2411/subpoena/pull/13))

Weaker than they should be:

7. **Claim order affected attribution.** Matching was greedy in claim order, so an earlier
   vague claim could take a write that belonged to a later, more specific one. Fixed:
   within each strictness pass the most specific claims match first.
   ([#17](https://github.com/aneesh2411/subpoena/issues/17),
   [PR #18](https://github.com/aneesh2411/subpoena/pull/18))
8. **Findings were scored by kind only.** A wrong unclaimed write could be counted as a
   correct one, and undefined precision was reported as 100%. Fixed: findings are scored
   as a multiset of codes, and undefined precision or recall is reported as `n/a`.
   ([#7](https://github.com/aneesh2411/subpoena/issues/7),
   [PR #15](https://github.com/aneesh2411/subpoena/pull/15))
9. **API**: no authentication unless a key was set, an inline call that conflicted with a
   stored one was not rejected, and the demo stage endpoint swapped the store without a
   lock. Fixed: the CLI refuses a public bind without `SUBPOENA_API_KEY`, conflicting
   inline calls get a 409, and stage swaps are serialized.
   ([#8](https://github.com/aneesh2411/subpoena/issues/8),
   [PR #16](https://github.com/aneesh2411/subpoena/pull/16))

No SQL injection, cross-site scripting or error-detail leaks were found.

**Lesson.** Every one of items 1 to 6 is a case the author did not think to write a
scenario for. Hand-written labels and a fuzzer built by the same person share that
person's blind spots. An adversarial pass by someone else found more in a few minutes
than 552 self-authored cases did. That pass should be a standing part of the eval: after
each fix, the shape joins the golden set and the fuzzer so the next reviewer starts from
a harder target.

## 5. What the baseline and ablations show

The naive baseline looks up current state and asks whether something matching the claim
exists. It fails 27 of 57 calls under strict scoring (19 on the headline "does this call need attention" decision). The pattern:

- **It reports the wrong verdict for partial failures.** Wrong time, wrong patient and wrong
  recipient all come back `NOT_FOUND`: it can see that the claimed record is absent, not
  that a different one was written in its place. The operator is told "nothing happened"
  when something wrong happened.
- **It is fooled when something matching exists for another reason.** A pre-existing
  appointment, a zombie reschedule and a confirmation with the wrong time in it all
  "verify".
- **It is blind to everything not claimed.** Finding recall is 0%: no unclaimed writes, no
  policy violations, no spoken-only promises.

Ablations switch off one capability of the real auditor:

<!-- metrics:fa-ablations:start -->
| Variant | Claim recall | Finding recall | Calls fully correct |
|---|---|---|---|
| Full | 94.6% | 100% | 96.7% |
| No transcript reader | 78.4% | 100% | 90.2% |
| No policy checks | 94.6% | 42.9% | 90.2% |
<!-- metrics:fa-ablations:end -->

Each ablation costs what it should and nothing else. That is evidence the dataset exercises
those capabilities and that the scorer can tell a worse auditor from a better one. A CLI
test also breaks the auditor on purpose and checks that `subpoena eval` exits non-zero.

## 6. What these numbers do not show

Read this section before quoting the table.

**The suspect is simulated.** Its speech comes from a small set of seeded templates, and the
rule-based reader was written by the same person with those templates in view. 100% on
explicit phrasing here says the reader handles these templates, not that it handles real
call transcripts. Speech-to-text errors, interruptions, corrections ("sorry, I meant
Thursday") and agents that paraphrase are not represented. The one vague phrasing in the set
is a miss.

**100% on fuzzing is narrower than it sounds.** Fuzzing varies patient, provider, day, time
of day, phrasing variant and fault mode. About 30% of calls chain two or three intents
(book, reschedule, cancel, handoff) with a fault drawn per step, so one call can mix
honest and dishonest requests. It shows the verdicts do not depend on hand-picked dates
and slots (it crosses the daylight-saving change and month boundaries). Multi-intent calls
chain two or three intents with kinds free to repeat: steps of one kind share the
per-type evidence pool, and reconcile pairs each pool by assignment instead of claim
order (#39, `tests/test_reconcile_assignment.py`), so same-kind fault composition is
covered rather than excluded.
`wrong_appointment_cancelled` and `silent_crm_edit` never fire in a multi-intent call
(their blast radius is the whole call, not the step), and the two known gaps are excluded
from generation. Expected outcomes come from a table written by the same author as the
auditor.

**Precision is measured on a 57-call sample.** Zero false accusations in that sample is
encouraging and not a guarantee. Both false positives found so far were caught and fixed
during the build (3.1, and recaps in section 4, item 6); none are open.

**The dataset is one clinic, one agent, and golden calls do not overlap.** Overlapping
calls under a shared agent identity are covered by dedicated tests
(`tests/test_attribution.py`): a write correlated by identity plus window alone can no
longer verify a claim while another call is in progress, one mutation cannot verify two
calls, and only a platform-stamped `agent:call-id` session is direct evidence. What is
not measured is how a real platform behaves when its sessions cannot stamp calls at all.

**The reference store is ideal evidence.** A trigger-written journal with before-images and
actors is the best case. Google Calendar gives you update times and no before-images. The
adapter contract says what has to be reconstructed; nothing here measures how well that
works.

## 7. What I would do next, in order

1. Fix the open review findings in section 4, each with a regression test and a golden
   scenario.
2. Measure the model-backed reader on real, messy transcripts, with precision tracked
   separately from recall.
3. Structured topics for handoffs, to close gap 2.2 without a model on the verdict path.
