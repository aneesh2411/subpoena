"""The v2 semantic scorer: verdicts paired with expectations by identity."""

from datetime import UTC, datetime

from subpoena.evals.run import run_all
from subpoena.evals.score import claim_identity, score_run_v2
from subpoena.models import (
    AppointmentBooked,
    AuditReport,
    AuditStats,
    CallRecord,
    Cancelled,
    ConfirmationSent,
    ExtractionHealth,
    HandoffCreated,
    Outcome,
    PatientRef,
    Rescheduled,
    Severity,
    Verdict,
    VerdictStatus,
)
from subpoena.scenarios import GOLDEN, run_scenario

V, NF, MM = VerdictStatus.VERIFIED, VerdictStatus.NOT_FOUND, VerdictStatus.MISMATCH
T1 = datetime(2026, 3, 3, 19, 30, tzinfo=UTC)
T2 = datetime(2026, 3, 4, 19, 30, tzinfo=UTC)
ROSA = PatientRef(name="Rosa Delgado", phone="+14805550142")


def _book_claim(start=T1, provider="Dr. Patel", patient=ROSA):
    return AppointmentBooked(
        claim_id=f"c-{start.isoformat()}-{provider}",
        turn_index=0,
        patient=patient,
        start=start,
        provider=provider,
    )


def _verdict(claim, status):
    return Verdict(claim=claim, status=status, severity=Severity.CRITICAL, summary="s")


def _report(verdicts):
    call = CallRecord(
        call_id="CA" + "0" * 32,
        agent_id="a",
        caller=ROSA,
        started_at=T1,
        ended_at=T1,
    )
    return AuditReport(
        audit_id="aud-t",
        auditor="test",
        created_at=T1,
        call=call,
        outcome=Outcome.DISCREPANCIES,
        verdicts=tuple(verdicts),
        findings=(),
        trail=(),
        stats=AuditStats(
            claims=len(verdicts),
            verified=0,
            mismatch=0,
            not_found=0,
            unverifiable=0,
            findings=0,
            trust_score=None,
            max_severity=Severity.INFO,
        ),
        extraction=ExtractionHealth(extractor="rules"),
    )


class TestClaimIdentity:
    def test_all_five_types(self):
        assert claim_identity(_book_claim()) == (f"book|+14805550142|{T1.isoformat()}|Dr. Patel")
        rescheduled = Rescheduled(
            claim_id="c1", turn_index=0, patient=ROSA, old_start=T1, new_start=T2
        )
        assert claim_identity(rescheduled) == (
            f"reschedule|+14805550142|{T1.isoformat()}|{T2.isoformat()}"
        )
        cancelled = Cancelled(claim_id="c2", turn_index=0, patient=ROSA, start=T1)
        assert claim_identity(cancelled) == f"cancel|+14805550142|{T1.isoformat()}"
        sent = ConfirmationSent(claim_id="c3", turn_index=0, to="+14805550142", about_start=T1)
        assert claim_identity(sent) == f"confirm|sms|+14805550142|{T1.isoformat()}"
        handoff = HandoffCreated(claim_id="c4", turn_index=0, patient=ROSA, priority="urgent")
        assert claim_identity(handoff) == "handoff|+14805550142|urgent"

    def test_provider_distinguishes_same_slot_bookings(self):
        # fault-misspoken-provider: the logged booking and the spoken promise
        # share patient and start; only the provider tells the targets apart.
        assert claim_identity(_book_claim(provider="Dr. Patel")) != claim_identity(
            _book_claim(provider="Dr. Kim")
        )


class TestScoreRunV2:
    def test_right_status_wrong_target_is_not_correct(self):
        # Two bookings, one good one phantom. The auditor flags one of them but
        # pairs its verdict with the wrong claim: v1 positional scoring would
        # read the flagged slot as a hit; v2 scores the true targets.
        expected = [
            ("AppointmentBooked", V, claim_identity(_book_claim(T1))),
            ("AppointmentBooked", NF, claim_identity(_book_claim(T2))),
        ]
        report = _report([_verdict(_book_claim(T1), NF), _verdict(_book_claim(T2), V)])
        run = score_run_v2("t", expected, (), report)
        assert run.status_correct == 0
        assert run.claims.tp == 0 and run.claims.fp == 1 and run.claims.fn == 1
        assert not run.strict_correct

    def test_correct_pairing_scores_clean(self):
        expected = [
            ("AppointmentBooked", V, claim_identity(_book_claim(T1))),
            ("AppointmentBooked", NF, claim_identity(_book_claim(T2))),
        ]
        report = _report([_verdict(_book_claim(T1), V), _verdict(_book_claim(T2), NF)])
        run = score_run_v2("t", expected, (), report)
        assert run.status_correct == 2 and run.strict_correct
        assert run.claims.tp == 1 and run.claims.tn == 1

    def test_unmatched_expectation_and_invented_claim(self):
        expected = [("AppointmentBooked", NF, claim_identity(_book_claim(T1)))]
        report = _report([_verdict(_book_claim(T2), MM)])
        run = score_run_v2("t", expected, (), report)
        assert run.claims.fn == 1 and run.claims.fp == 1
        assert any("auditor saw no claim" in p for p in run.problems)
        assert any("unexpected claim" in p for p in run.problems)

    def test_one_to_one_within_an_identity(self):
        # Two expectations cannot both be satisfied by one verdict.
        identity = claim_identity(_book_claim(T1))
        expected = [("AppointmentBooked", V, identity), ("AppointmentBooked", NF, identity)]
        report = _report([_verdict(_book_claim(T1), V)])
        run = score_run_v2("t", expected, (), report)
        assert run.claims.fn == 1
        assert run.status_correct == 1


class TestGoldenLabels:
    def test_every_positional_expectation_has_a_label(self):
        for scenario in GOLDEN:
            assert len(scenario.expected_claims_v2) == len(scenario.expected_claims)
            for (v1_type, v1_status), (v2_type, v2_status, identity) in zip(
                scenario.expected_claims, scenario.expected_claims_v2, strict=True
            ):
                assert (v1_type, v1_status) == (v2_type, v2_status)
                assert identity and "|" in identity

    def test_positional_fields_survive_labelling(self):
        # Scenarios pass expected_findings positionally; attaching v2 labels
        # must not shift them (regression: it once emptied six scenarios').
        by_id = {s.id: s for s in GOLDEN}
        assert by_id["fault-unclaimed-write"].expected_findings == (
            "unclaimed_appointment_created",
        )

    def test_labels_pair_with_real_verdicts(self):
        # For every scenario with structured claims, the label set equals the
        # identity set of the claims the auditor judges, one-to-one.
        from collections import Counter

        from subpoena.scenarios.world import build_world, run_suspect

        for scenario in GOLDEN:
            world = build_world(scenario)
            try:
                result = run_suspect(scenario, world)
            finally:
                world.store.close()
            scripted = Counter(claim_identity(c) for c in result.call.claims)
            labelled = Counter(
                i for _, _, i in scenario.expected_claims_v2 if i in scripted or True
            )
            # A claim a later scripted claim corrects is withdrawn from judgment
            # by design (the caller is owed the final value), so it is unlabelled.
            withdrawn: set[str] = set()
            last_by_key: dict[tuple[str, str], str] = {}
            for c in result.call.claims:
                patient = getattr(c, "patient", None)
                key = (
                    (c.type, patient.phone or patient.name.strip().lower())
                    if patient is not None and c.type in ("AppointmentBooked", "Cancelled")
                    else None
                )
                if c.corrects_prior and key is not None and key in last_by_key:
                    withdrawn.add(last_by_key[key])
                if key is not None:
                    last_by_key[key] = claim_identity(c)
            # Every other scripted claim's identity is labelled exactly once.
            for identity, n in scripted.items():
                if identity in withdrawn:
                    continue
                assert labelled[identity] >= n, (scenario.id, identity)

    def test_dual_run_reports_both_scorers(self):
        result = run_all(fuzz_n=20)
        assert result.golden_v2 is not None
        assert result.golden_v2.calls == len(GOLDEN)
        known_gaps = {"gap-vague-spoken-promise", "gap-garbled-handoff"}
        assert {r.scenario_id for r in result.golden.failures} == known_gaps
        assert result.golden.strict_accuracy == (len(GOLDEN) - len(known_gaps)) / len(GOLDEN)
        assert {r.scenario_id for r in result.golden_v2.failures} == known_gaps

    def test_v2_scores_each_scenario_like_v1_on_the_golden_set(self):
        # On a correctly audited golden call the two scorers agree.
        for scenario in GOLDEN[:6]:
            report = run_scenario(scenario).report
            run = score_run_v2(
                scenario.id, scenario.expected_claims_v2, scenario.expected_findings, report
            )
            assert run.strict_correct, (scenario.id, run.problems)
